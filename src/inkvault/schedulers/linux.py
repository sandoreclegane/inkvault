"""Linux: a systemd user timer (Persistent=true runs a missed night at the next boot or wake), else a crontab line."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

UNIT = "inkvault-nightly"
TAG = "# inkvault-nightly"  # marks our crontab line so --off removes only it


def unit_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def exec_start(argv):
    """systemd expands %specifiers and $VARIABLES in ExecStart, so a literal one has to be doubled."""
    return shlex.join(argv).replace("%", "%%").replace("$", "$$")


def render_service(argv):
    return f"""[Unit]
Description=InkVault nightly refresh

[Service]
Type=oneshot
ExecStart={exec_start(argv)}
"""


def render_timer(hour, minute):
    return f"""[Unit]
Description=Run the InkVault nightly refresh every day

[Timer]
OnCalendar=*-*-* {hour:02d}:{minute:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""


def cron_line(argv, hour, minute):
    command = shlex.join(argv).replace("%", "\\%")  # an unescaped % in a crontab line means newline
    return f"{minute} {hour} * * * {command} {TAG}"


def merge_crontab(existing, line):
    """existing crontab text with our line replaced (or removed, when line is None)."""
    keep = [l for l in existing.splitlines() if not l.rstrip().endswith(TAG)]  # the user's blank lines stay
    if line:
        keep.append(line)
    return "".join(l + "\n" for l in keep)


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, errors="replace")


def has_systemd():
    return bool(shutil.which("systemctl")) and systemctl("show-environment").returncode == 0


def get_crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace")
    if r.returncode == 0:
        return r.stdout
    err = r.stderr.lower()
    if r.returncode == 1 and ("no crontab" in err or "no such file" in err):  # busybox says the latter
        return ""  # an empty crontab; any other failure must not be mistaken for it, or we'd overwrite theirs
    raise RuntimeError(f"couldn't read your crontab: {(r.stderr or r.stdout).strip()}")


def set_crontab(text):
    try:
        subprocess.run(["crontab", "-"], input=text, text=True, errors="replace", capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"couldn't write your crontab: {(e.stderr or '').strip()}") from e


def drop_cron_line():
    """Best-effort: remove our crontab line, so switching to a systemd timer doesn't leave a second job."""
    try:
        if shutil.which("crontab"):
            current = get_crontab()
            if TAG in current:
                set_crontab(merge_crontab(current, None))
    except Exception:  # noqa: BLE001
        pass


def drop_units():
    """Best-effort: remove our systemd units, so switching to cron doesn't leave a second job."""
    try:
        if has_systemd():
            systemctl("disable", "--now", f"{UNIT}.timer")
        for ext in ("timer", "service"):
            (unit_dir() / f"{UNIT}.{ext}").unlink(missing_ok=True)
        if has_systemd():
            systemctl("daemon-reload")
    except Exception:  # noqa: BLE001
        pass


def install(argv, hour, minute, wake):
    if has_systemd():
        d = unit_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{UNIT}.service").write_text(render_service(argv), encoding="utf-8")
        (d / f"{UNIT}.timer").write_text(render_timer(hour, minute), encoding="utf-8")
        systemctl("daemon-reload")
        r = systemctl("enable", "--now", f"{UNIT}.timer")
        if r.returncode:
            raise RuntimeError(f"systemctl couldn't enable the timer: {(r.stderr or r.stdout).strip()}")
        systemctl("restart", f"{UNIT}.timer")  # enable --now doesn't re-read a timer that was already running
        drop_cron_line()
        return (f"systemd user timer {UNIT}.timer; it runs while you're logged in "
                "(`loginctl enable-linger $USER` keeps it running when you're not)")
    if not shutil.which("crontab"):
        raise RuntimeError("Neither systemd (user) nor crontab is available, so there's nothing to schedule with.")
    set_crontab(merge_crontab(get_crontab(), cron_line(argv, hour, minute)))
    drop_units()
    return "crontab entry (note: cron doesn't catch up on a run missed while the computer was off or asleep)"


def remove():
    removed = False
    systemd = has_systemd()  # checked once: it shells out to systemctl
    if systemd:
        systemctl("disable", "--now", f"{UNIT}.timer")
    for ext in ("timer", "service"):  # delete the files even if systemctl is gone, so nothing is left behind
        f = unit_dir() / f"{UNIT}.{ext}"
        if f.exists():
            f.unlink()
            removed = True
    if systemd:
        systemctl("daemon-reload")
    if shutil.which("crontab"):
        try:
            current = get_crontab()
            if TAG in current:
                set_crontab(merge_crontab(current, None))
                removed = True
        except RuntimeError:
            if not removed:  # if the units were removed, an unreadable crontab mustn't turn --off into a failure
                raise
    return removed


def installed():
    if (unit_dir() / f"{UNIT}.timer").exists():
        return True
    if not shutil.which("crontab"):
        return False
    try:
        return TAG in get_crontab()
    except RuntimeError:  # can't read it: say "not installed" rather than crash a status check
        return False


def wake_note(hour, minute):
    return ("Linux doesn't let a user's timer wake the computer, so the run happens at the next wake instead "
            "(the timer catches up on missed nights).")
