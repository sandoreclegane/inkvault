"""Backend behavior beyond rendering: exact commands issued, escaping, retries. subprocess.run is always faked
(conftest fails any test that runs a real scheduler command)."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

ARGV = ["/home/u/100%/v$x/python", "-m", "inkvault", "--home", "/h/my vault", "nightly"]


def fake_run(monkeypatch, module, results=None):
    """Record every subprocess.run argv. results maps an argv prefix to a result, an exception, or a callable."""
    calls = []

    def run(cmd, *a, **kw):
        calls.append(list(cmd))
        for prefix, r in (results or {}).items():
            if tuple(cmd[:len(prefix)]) == prefix:
                if callable(r):
                    r = r()
                if isinstance(r, Exception):
                    raise r
                return r
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def proc(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


# --- Windows ---------------------------------------------------------------------------------------------------

def test_windows_wake_timer_value_2_counts_as_blocked():
    from inkvault.schedulers import windows
    assert windows.wake_timers_allowed("Current AC Power Setting Index: 0x00000002\n") is False
    assert windows.wake_timers_allowed("Current AC Power Setting Index: 0x00000001\n") is True


def test_windows_xml_is_utf16_with_bom_and_quotes_spaced_command():
    import xml.etree.ElementTree as ET
    from inkvault.schedulers import windows
    argv = ["C:/Program Files/py/pythonw.exe", "-m", "inkvault", "nightly"]
    xml = windows.render(argv, 3, 5, wake=False)
    assert xml.encode("utf-16")[:2] in (b"\xff\xfe", b"\xfe\xff")
    root = ET.fromstring(xml.split("?>", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:Exec/t:Command", ns).text == '"C:/Program Files/py/pythonw.exe"'


def test_windows_install_runs_schtasks_create(monkeypatch):
    from inkvault.schedulers import windows
    calls = fake_run(monkeypatch, windows)
    windows.install(["C:/py/pythonw.exe", "-m", "inkvault", "nightly"], 3, 5, True)
    c = calls[0]
    assert c[:3] == ["schtasks", "/Create", "/XML"] and c[4:] == ["/TN", "InkVault Nightly", "/F"]


def test_windows_install_failure_raises(monkeypatch):
    from inkvault.schedulers import windows
    fake_run(monkeypatch, windows, {("schtasks",): proc(1, err="Access is denied")})
    with pytest.raises(RuntimeError, match="Access is denied"):
        windows.install(["C:/py/pythonw.exe"], 3, 5, False)


def test_windows_text_decoding_is_tolerant():
    from inkvault.schedulers import windows
    assert windows.TEXT["errors"] == "replace"
    if sys.platform == "win32":
        assert windows.TEXT["encoding"] == "oem"


# --- macOS -----------------------------------------------------------------------------------------------------

def test_macos_plist_keeps_the_mac_awake():
    import plistlib
    from pathlib import Path
    from inkvault.schedulers import macos
    plist = plistlib.loads(macos.render(["/u/py", "-m", "inkvault", "nightly"], 3, 5, Path("/tmp/l")))
    assert plist["ProgramArguments"] == ["/usr/bin/caffeinate", "-i", "/u/py", "-m", "inkvault", "nightly"]
    assert "ProcessType" not in plist


def test_macos_pmset_note_mentions_replacing_schedule():
    from inkvault.schedulers import macos
    assert "replaces any existing" in macos.wake_note(3, 0)


@pytest.fixture
def mac(monkeypatch, tmp_path):
    from inkvault.schedulers import macos
    monkeypatch.setattr(macos.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(macos.paths, "home", lambda: tmp_path)
    monkeypatch.setattr(macos.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(macos.time, "sleep", lambda s: None)
    return macos


def test_macos_install_bootout_then_bootstrap(mac, monkeypatch):
    calls = fake_run(monkeypatch, mac)
    mac.install(["/u/py", "nightly"], 3, 5, False)
    assert [c[:2] for c in calls] == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    assert calls[0][2] == "gui/501/org.inkvault.nightly" and calls[1][2] == "gui/501"
    assert mac.plist_path().exists()


def test_macos_bootstrap_retries_then_succeeds(mac, monkeypatch):
    outcomes = iter([proc(5, err="Input/output error")] * 2 + [proc(0)])
    calls = fake_run(monkeypatch, mac, {("launchctl", "bootstrap"): lambda: next(outcomes)})
    mac.install(["/u/py", "nightly"], 3, 5, False)
    assert [c[1] for c in calls].count("bootstrap") == 3
    assert mac.plist_path().exists()


def test_macos_bootstrap_gives_up_after_five_and_removes_plist(mac, monkeypatch):
    calls = fake_run(monkeypatch, mac, {("launchctl", "bootstrap"): proc(5, err="Input/output error")})
    with pytest.raises(RuntimeError, match="launchctl couldn't load"):
        mac.install(["/u/py", "nightly"], 3, 5, False)
    assert [c[1] for c in calls].count("bootstrap") == 5
    assert not mac.plist_path().exists()


# --- Linux -----------------------------------------------------------------------------------------------------

def test_linux_specifiers_escaped_in_service_and_cron():
    from inkvault.schedulers import linux
    service = linux.render_service(ARGV)
    assert "100%%" in service and "v$$x" in service and "100%/" not in service
    line = linux.cron_line(ARGV, 3, 5)
    assert "100\\%/" in line and "v$x" in line  # cron treats % as a newline, but $ is fine
    assert line.endswith(linux.TAG)


def test_linux_crontab_merge_keeps_blank_lines():
    from inkvault.schedulers import linux
    theirs = "# mine\n\n0 9 * * 1 backup.sh\n\n"
    line = linux.cron_line(["/p", "nightly"], 3, 5)
    assert linux.merge_crontab(theirs + line + "\n", None) == theirs


@pytest.fixture
def lin(monkeypatch, tmp_path):
    from inkvault.schedulers import linux
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(linux.shutil, "which", lambda name: f"/usr/bin/{name}")
    return linux


def test_linux_unit_dir_ignores_empty_xdg(lin, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setattr(lin.Path, "home", classmethod(lambda cls: tmp_path))
    assert lin.unit_dir() == tmp_path / ".config" / "systemd" / "user"


def test_linux_systemd_sequence(lin, monkeypatch):
    calls = fake_run(monkeypatch, lin)
    lin.install(["/p", "nightly"], 3, 5, False)
    assert [c[2] for c in calls if c[0] == "systemctl"] == ["show-environment", "daemon-reload", "enable", "restart",
                                                                 "is-active"]
    assert next(c for c in calls if c[2] == "enable")[-2:] == ["--now", "inkvault-nightly.timer"]
    assert next(c for c in calls if c[2] == "restart")[-1] == "inkvault-nightly.timer"
    assert (lin.unit_dir() / "inkvault-nightly.timer").exists()


def test_linux_falls_back_to_crontab_without_systemd(lin, monkeypatch):
    written = {}
    monkeypatch.setattr(lin, "has_systemd", lambda: False)
    monkeypatch.setattr(lin, "get_crontab", lambda: "0 9 * * 1 x\n")
    monkeypatch.setattr(lin, "set_crontab", lambda text: written.setdefault("t", text))
    desc = lin.install(["/p", "nightly"], 3, 5, False)
    assert "crontab" in desc
    assert written["t"].startswith("0 9 * * 1 x\n") and written["t"].rstrip().endswith(lin.TAG)
    assert not (lin.unit_dir() / "inkvault-nightly.timer").exists()


def test_linux_remove_deletes_units_even_without_systemctl(lin, monkeypatch):
    d = lin.unit_dir()
    d.mkdir(parents=True)
    (d / "inkvault-nightly.timer").write_text("x")
    (d / "inkvault-nightly.service").write_text("x")
    monkeypatch.setattr(lin, "has_systemd", lambda: False)
    monkeypatch.setattr(lin.shutil, "which", lambda name: None)
    calls = fake_run(monkeypatch, lin)
    assert lin.remove() is True
    assert not list(d.iterdir()) and calls == []


def test_get_crontab_error_handling(lin, monkeypatch):
    fake_run(monkeypatch, lin, {("crontab", "-l"): proc(1, err="no crontab for alex")})
    assert lin.get_crontab() == ""
    fake_run(monkeypatch, lin, {("crontab", "-l"): proc(0, out="0 9 * * 1 x\n")})
    assert lin.get_crontab() == "0 9 * * 1 x\n"
    fake_run(monkeypatch, lin, {("crontab", "-l"): proc(1, err="crontab: permission denied")})
    with pytest.raises(RuntimeError, match="permission denied"):
        lin.get_crontab()
    fake_run(monkeypatch, lin, {("crontab", "-l"): proc(2, err="no crontab for x")})
    with pytest.raises(RuntimeError):
        lin.get_crontab()


def test_set_crontab_wraps_failure(lin, monkeypatch):
    err = subprocess.CalledProcessError(1, "crontab", stderr="bad line 3")
    fake_run(monkeypatch, lin, {("crontab", "-"): err})
    with pytest.raises(RuntimeError, match="bad line 3"):
        lin.set_crontab("x\n")


# --- keep awake ------------------------------------------------------------------------------------------------

def test_keep_awake_never_raises():
    from inkvault import nightly
    with nightly.keep_awake():
        pass


# --- review round 2 --------------------------------------------------------------------------------------------

def test_windows_wake_check_is_language_independent():
    from inkvault.schedulers import windows
    german = ("    Moegliche Einstellung, Index: 000\n"
              "    Moegliche Einstellung, Beschreibung: Deaktivieren\n"
              "    Moegliche Einstellung, Index: 001\n"
              "    Moegliche Einstellung, Beschreibung: Aktivieren\n"
              "\n"
              "    Aktueller Wechselstrom-Energieeinstellungsindex: 0x00000002\n"
              "    Aktueller Gleichstrom-Energieeinstellungsindex: 0x00000001\n")
    assert windows.wake_timers_allowed(german) is False
    assert windows.wake_timers_allowed(german.replace("0x00000002", "0x00000001")) is True
    # AC is the second-to-last value, not the last
    assert windows.wake_timers_allowed("AC: 0x00000001\nDC: 0x00000000\n") is True
    assert windows.wake_timers_allowed("AC: 0x00000000\nDC: 0x00000001\n") is False
    assert windows.wake_timers_allowed("Possible Setting Index: 000\n") is True


def test_get_crontab_busybox_no_crontab(lin, monkeypatch):
    fake_run(monkeypatch, lin, {("crontab", "-l"): proc(1, err="cat: can't open 'alex': No such file or directory")})
    assert lin.get_crontab() == ""


def test_linux_remove_survives_unreadable_crontab_after_removing_units(lin, monkeypatch):
    d = lin.unit_dir()
    d.mkdir(parents=True)
    (d / "inkvault-nightly.timer").write_text("x")
    monkeypatch.setattr(lin, "has_systemd", lambda: False)

    def boom():
        raise RuntimeError("couldn't read your crontab: permission denied")
    monkeypatch.setattr(lin, "get_crontab", boom)
    assert lin.remove() is True  # the units went; the crontab problem doesn't fail --off
    with pytest.raises(RuntimeError):  # but with nothing else removed it is reported
        lin.remove()


def test_linux_systemd_install_removes_our_old_cron_line(lin, monkeypatch):
    fake_run(monkeypatch, lin)
    written = {}
    monkeypatch.setattr(lin, "get_crontab", lambda: "0 9 * * 1 x\n" + lin.cron_line(["/p"], 3, 5) + "\n")
    monkeypatch.setattr(lin, "set_crontab", lambda text: written.setdefault("t", text))
    lin.install(["/p", "nightly"], 3, 5, False)
    assert written["t"] == "0 9 * * 1 x\n"  # theirs stays, ours is gone


def test_linux_cron_install_removes_our_old_systemd_units(lin, monkeypatch):
    d = lin.unit_dir()
    d.mkdir(parents=True)
    (d / "inkvault-nightly.timer").write_text("x")
    (d / "inkvault-nightly.service").write_text("x")
    states = iter([False, True, True])  # install's own check, then drop_units' two
    monkeypatch.setattr(lin, "has_systemd", lambda: next(states, True))
    calls = fake_run(monkeypatch, lin)
    monkeypatch.setattr(lin, "get_crontab", lambda: "")
    monkeypatch.setattr(lin, "set_crontab", lambda text: None)
    lin.install(["/p", "nightly"], 3, 5, False)
    assert not list(d.iterdir())
    assert ["systemctl", "--user", "disable", "--now", "inkvault-nightly.timer"] in calls


def test_linux_cleanup_of_the_other_scheduler_never_fails_the_install(lin, monkeypatch):
    fake_run(monkeypatch, lin)

    def boom():
        raise RuntimeError("couldn't read your crontab")
    monkeypatch.setattr(lin, "get_crontab", boom)
    assert "systemd" in lin.install(["/p", "nightly"], 3, 5, False)


# --- review round 3: health, failures, escaping -----------------------------------------------------------------

def task_xml(argv, enabled=True, encoding="utf-16"):
    """What `schtasks /Query /XML` prints for our task: the XML we registered, possibly disabled since."""
    from inkvault.schedulers import windows
    xml = windows.render(argv, 3, 5, False)
    if not enabled:
        xml = xml.replace("    <Enabled>true</Enabled>\n  </Settings>", "    <Enabled>false</Enabled>\n  </Settings>")
    return xml.encode(encoding)


WIN_ARGV = ["C:\\Users\\a b\\uv\\tools\\inkvault\\Scripts\\pythonw.exe", "-m", "inkvault",
            "--home", "D:\\My Vault\\", "nightly"]


def test_windows_query_reads_the_registered_task(monkeypatch):
    from inkvault.schedulers import windows
    calls = fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=task_xml(WIN_ARGV))})
    job = windows.query()
    assert calls[0] == ["schtasks", "/Query", "/TN", "InkVault Nightly", "/XML"]
    assert job.present and job.enabled is True and job.scheduler == "Task Scheduler"
    assert job.executable == WIN_ARGV[0] and job.home == "D:\\My Vault\\"


def test_windows_query_sees_a_disabled_task_in_any_encoding(monkeypatch):
    from inkvault.schedulers import windows
    for enc in ("utf-16", "utf-16-le", "utf-8"):  # with a BOM, without one, or plain bytes
        fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=task_xml(WIN_ARGV, False, enc))})
        job = windows.query()
        assert job.present and job.enabled is False and job.home == "D:\\My Vault\\", enc


def test_windows_query_absent_and_unreadable(monkeypatch):
    from inkvault.schedulers import windows
    fake_run(monkeypatch, windows, {("schtasks",): proc(1, err=b"ERROR: The system cannot find the file")})
    assert windows.query().present is False and windows.installed() is False
    fake_run(monkeypatch, windows, {("schtasks",): proc(0, out=b"not xml at all")})
    job = windows.query()
    assert job.present and job.enabled is None and job.home is None  # there, but we can't tell more


def test_windows_split_args_inverts_list2cmdline():
    from inkvault.schedulers import windows
    for argv in (["-m", "inkvault", "--home", "D:\\My Vault\\", "nightly"], ['a "q" b', "c\\\\", "", "x\\y"]):
        assert windows.split_args(subprocess.list2cmdline(argv)) == argv


def test_windows_remove_says_absent_removed_or_fails(monkeypatch):
    from inkvault.schedulers import windows
    calls = fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(1, err=b"not found")})
    assert windows.remove() is False and not any("/Delete" in c for c in calls)
    fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=task_xml(WIN_ARGV))})
    assert windows.remove() is True
    fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=task_xml(WIN_ARGV)),
                                    ("schtasks", "/Delete"): proc(1, err="ERROR: Access is denied.")})
    with pytest.raises(RuntimeError, match="Access is denied"):
        windows.remove()


def test_windows_restart_comment_is_accurate():
    import inspect
    from inkvault.schedulers import windows
    src = inspect.getsource(windows.render)
    assert "only if the task fails to start" not in src and "per its own rules" in src


def test_macos_query(mac, monkeypatch):
    mac.plist_path().parent.mkdir(parents=True)
    calls = fake_run(monkeypatch, mac)
    assert mac.query().present is False and calls == []
    mac.plist_path().write_bytes(mac.render(["/u/py", "-m", "inkvault", "--home", "/v", "nightly"], 3, 5, "/l"))
    job = mac.query()
    assert calls[-1] == ["launchctl", "print", "gui/501/org.inkvault.nightly"]
    assert (job.present, job.enabled, job.home, job.executable, job.scheduler) == (True, True, "/v", "/u/py", "launchd")
    fake_run(monkeypatch, mac, {("launchctl", "print"): proc(113, err="Could not find service")})
    assert mac.query().enabled is False  # the file is there but launchd isn't running it


def test_macos_query_unknown_when_launchctl_cant_say(mac, monkeypatch):
    mac.plist_path().parent.mkdir(parents=True)
    mac.plist_path().write_bytes(mac.render(["/u/py", "-m", "inkvault", "nightly"], 3, 5, "/l"))
    # over SSH there's no GUI session to look in: that says nothing about our job
    fake_run(monkeypatch, mac, {("launchctl", "print"): proc(125, err="Domain does not support specified action")})
    assert mac.query().present and mac.query().enabled is None
    fake_run(monkeypatch, mac, {("launchctl", "print"): proc(1, err="Could not find service \"x\" in domain")})
    assert mac.query().enabled is False


def test_windows_query_distrusts_paths_the_oem_code_page_mangled(monkeypatch):
    from inkvault.schedulers import windows
    argv = ["C:\\Users\\Zoë\\uv\\tools\\inkvault\\Scripts\\pythonw.exe", "-m", "inkvault",
            "--home", "C:\\Users\\Zoë\\Vault 工具", "nightly"]
    # piped schtasks output is single-byte OEM text: characters it can't show come back as "?"
    mangled = windows.render(argv, 3, 5, False).replace("ë", "?").replace("工具", "??").encode("ascii")
    fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=mangled)})
    job = windows.query()
    assert job.present and job.enabled is True and job.home is None and job.executable is None
    replaced = windows.render(argv, 3, 5, False).replace("ë", "\ufffd").encode("utf-16")
    fake_run(monkeypatch, windows, {("schtasks", "/Query"): proc(0, out=replaced)})
    assert windows.query().executable is None


def test_macos_remove_checks_bootout(mac, monkeypatch):
    p = mac.plist_path()
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    fake_run(monkeypatch, mac, {("launchctl", "bootout"): proc(5, err="Input/output error")})
    with pytest.raises(RuntimeError, match="Input/output error"):
        mac.remove()
    assert p.exists()  # kept: launchd may still run it
    for not_loaded in (proc(3, err="whatever"), proc(113, err="x"), proc(1, err="Boot-out failed: 3: No such process")):
        fake_run(monkeypatch, mac, {("launchctl", "bootout"): not_loaded})
        p.write_bytes(b"x")
        assert mac.remove() is True and not p.exists()
        assert mac.remove() is False  # nothing loaded, no file


def test_macos_install_fails_if_the_old_job_wont_unload(mac, monkeypatch):
    calls = fake_run(monkeypatch, mac, {("launchctl", "bootout"): proc(1, err="Operation not permitted")})
    with pytest.raises(RuntimeError, match="Operation not permitted"):
        mac.install(["/u/py", "nightly"], 3, 5, False)
    assert not any(c[1] == "bootstrap" for c in calls)


def test_linux_exec_start_escapes_backslashes_for_systemd():
    from inkvault.schedulers import linux
    line = linux.exec_start(["/home/u/notes\\new/python", "nightly"])
    assert line == "'/home/u/notes\\\\new/python' nightly"
    assert linux.parse_exec_start(linux.exec_start(ARGV + ["a\\b"])) == ARGV + ["a\\b"]
    assert "notes\\new" in linux.cron_line(["/home/u/notes\\new/python"], 3, 5)  # cron: shlex only


def test_linux_query_systemd_and_cron(lin, monkeypatch):
    monkeypatch.setattr(lin, "has_systemd", lambda: True)
    monkeypatch.setattr(lin, "get_crontab", lambda: "")
    assert lin.query().present is False
    d = lin.unit_dir()
    d.mkdir(parents=True)
    (d / "inkvault-nightly.service").write_text(lin.render_service(ARGV))
    (d / "inkvault-nightly.timer").write_text(lin.render_timer(3, 5))
    calls = fake_run(monkeypatch, lin, {("systemctl", "--user", "is-enabled"): proc(1, out="disabled\n")})
    job = lin.query()
    assert ["systemctl", "--user", "is-enabled", "inkvault-nightly.timer"] in calls
    assert (job.present, job.enabled, job.home, job.executable, job.scheduler) == \
        (True, False, "/h/my vault", ARGV[0], "systemd")
    fake_run(monkeypatch, lin)
    assert lin.query().enabled is True

    for f in d.iterdir():
        f.unlink()
    monkeypatch.setattr(lin, "get_crontab", lambda: "0 9 * * 1 x\n" + lin.cron_line(ARGV, 3, 5) + "\n")
    job = lin.query()
    assert (job.present, job.enabled, job.home, job.executable, job.scheduler) == \
        (True, True, "/h/my vault", ARGV[0], "cron")


@pytest.mark.parametrize("step", ["daemon-reload", "enable", "restart", "is-active"])
def test_linux_install_fails_on_any_systemctl_failure(lin, monkeypatch, step):
    fake_run(monkeypatch, lin, {("systemctl", "--user", step): proc(1, err=f"{step} went wrong")})
    monkeypatch.setattr(lin, "get_crontab", lambda: "")
    with pytest.raises(RuntimeError, match=f"{step} went wrong"):
        lin.install(["/p", "nightly"], 3, 5, False)


@pytest.mark.parametrize("step", ["disable", "daemon-reload"])
def test_linux_remove_fails_on_systemctl_failure(lin, monkeypatch, step):
    d = lin.unit_dir()
    d.mkdir(parents=True)
    (d / "inkvault-nightly.timer").write_text("x")
    monkeypatch.setattr(lin, "has_systemd", lambda: True)
    monkeypatch.setattr(lin, "get_crontab", lambda: "")
    fake_run(monkeypatch, lin, {("systemctl", "--user", step): proc(1, err=f"{step} went wrong")})
    with pytest.raises(RuntimeError, match=f"{step} went wrong"):
        lin.remove()


def test_linux_remove_reports_nothing_there(lin, monkeypatch):
    monkeypatch.setattr(lin, "has_systemd", lambda: True)
    monkeypatch.setattr(lin, "get_crontab", lambda: "0 9 * * 1 x\n")
    calls = fake_run(monkeypatch, lin)
    assert lin.remove() is False
    assert not any("disable" in c for c in calls)  # no unit: nothing to disable (it would fail)


def test_linux_failed_cleanup_of_the_other_scheduler_warns(lin, monkeypatch, capsys):
    fake_run(monkeypatch, lin)

    def boom():
        raise RuntimeError("couldn't read your crontab")
    monkeypatch.setattr(lin, "get_crontab", boom)
    lin.install(["/p", "nightly"], 3, 5, False)
    assert "Warning" in capsys.readouterr().out
