"""Linux: a systemd user timer (Persistent=true runs a missed night at the next boot or wake), else a crontab line."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

UNIT = "inkvault-nightly"
TAG = "# inkvault-nightly"  # marks our crontab line so --off removes only it


def unit_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def render_service(argv):
    return f"""[Unit]
Description=InkVault nightly refresh

[Service]
Type=oneshot
ExecStart={shlex.join(argv)}
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
    return f"{minute} {hour} * * * {shlex.join(argv)} {TAG}"


def merge_crontab(existing, line):
    """existing crontab text with our line replaced (or removed, when line is None)."""
    keep = [l for l in existing.splitlines() if l.strip() and not l.rstrip().endswith(TAG)]
    if line:
        keep.append(line)
    return "".join(l + "\n" for l in keep)


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def has_systemd():
    return bool(shutil.which("systemctl")) and systemctl("show-environment").returncode == 0


def get_crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""  # "no crontab for user" is exit 1


def set_crontab(text):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


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
        return (f"systemd user timer {UNIT}.timer; it runs while you're logged in "
                "(`loginctl enable-linger $USER` keeps it running when you're not)")
    if not shutil.which("crontab"):
        raise RuntimeError("Neither systemd (user) nor crontab is available, so there's nothing to schedule with.")
    set_crontab(merge_crontab(get_crontab(), cron_line(argv, hour, minute)))
    return "crontab entry (note: cron doesn't catch up on a run missed while the computer was off or asleep)"


def remove():
    removed = False
    if has_systemd():  # checked once: it shells out to systemctl
        systemctl("disable", "--now", f"{UNIT}.timer")
        for ext in ("timer", "service"):
            f = unit_dir() / f"{UNIT}.{ext}"
            if f.exists():
                f.unlink()
                removed = True
        systemctl("daemon-reload")
    if shutil.which("crontab"):
        current = get_crontab()
        if TAG in current:
            set_crontab(merge_crontab(current, None))
            removed = True
    return removed


def installed():
    if (unit_dir() / f"{UNIT}.timer").exists():
        return True
    return bool(shutil.which("crontab")) and TAG in get_crontab()


def wake_note(hour, minute):
    return ("Linux doesn't let a user's timer wake the computer, so the run happens at the next wake instead "
            "(the timer catches up on missed nights).")
