"""macOS: a LaunchAgent. launchd runs a calendar job it missed while the Mac slept as soon as it wakes."""
import os
import plistlib
import subprocess
import time
from pathlib import Path

from .. import paths
from . import Job, home_of

LABEL = "org.inkvault.nightly"


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render(argv, hour, minute, log_path):
    return plistlib.dumps({
        "Label": LABEL,
        # caffeinate -i keeps the Mac from idle-sleeping until the run ends
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", *argv],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        # nightly writes its own log; this only catches a crash before it starts
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    })


def domain():
    return f"gui/{os.getuid()}"


def bootout():
    """Unload our job: True if it was loaded, False if it wasn't; RuntimeError if launchctl failed otherwise."""
    r = subprocess.run(["launchctl", "bootout", f"{domain()}/{LABEL}"], capture_output=True, text=True,
                       errors="replace")
    if r.returncode == 0:
        return True
    err = (r.stderr or r.stdout or "").strip()
    # 3 is ESRCH ("No such process") and 113 "Could not find specified service": it wasn't loaded
    if r.returncode in (3, 113) or "no such process" in err.lower() or "could not find" in err.lower():
        return False
    raise RuntimeError(f"launchctl couldn't unload {LABEL}: {err or f'exit code {r.returncode}'}")


def install(argv, hour, minute, wake):
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    bootout()  # replace an older one; if it can't be unloaded, bootstrap would fail too
    p.write_bytes(render(argv, hour, minute, paths.home() / "launchd.log"))
    for attempt in range(5):  # bootstrap can fail right after bootout (errors 5 / 37) while launchd finishes
        r = subprocess.run(["launchctl", "bootstrap", domain(), str(p)], capture_output=True, text=True,
                           errors="replace")
        if not r.returncode:
            break
        if attempt < 4:
            time.sleep(0.5)
    else:
        p.unlink(missing_ok=True)  # don't leave a plist that launchd will load at next login but we couldn't confirm
        raise RuntimeError(f"launchctl couldn't load {p}: {(r.stderr or r.stdout).strip()}")
    return f"LaunchAgent {p}"


def remove():
    loaded = bootout()  # raises before touching the plist, so a job launchd still runs is never orphaned
    p = plist_path()
    existed = p.exists()
    p.unlink(missing_ok=True)
    return loaded or existed


def query():
    """Our LaunchAgent: the plist on disk says what it runs; `launchctl print` says whether launchd has it loaded."""
    p = plist_path()
    if not p.exists():
        return Job(False, scheduler="launchd")
    try:
        argv = [str(a) for a in plistlib.loads(p.read_bytes()).get("ProgramArguments", [])]
    except Exception:  # noqa: BLE001 - a damaged plist: it's there, but we can't say more
        argv = []
    if argv[:2] == ["/usr/bin/caffeinate", "-i"]:
        argv = argv[2:]
    try:
        r = subprocess.run(["launchctl", "print", f"{domain()}/{LABEL}"], capture_output=True, text=True,
                           errors="replace")
        if r.returncode == 0:
            enabled = True
        elif r.returncode == 113 or "could not find" in (r.stderr or r.stdout or "").lower():
            enabled = False  # the plist is there, but launchd isn't running it
        else:
            enabled = None  # can't look (no GUI session over SSH, say): that says nothing about our job
    except OSError:
        enabled = None
    return Job(True, enabled=enabled, home=home_of(argv), executable=argv[0] if argv else None, scheduler="launchd")


def installed():
    return query().present


def wake_note(hour, minute):
    h, m = divmod((hour * 60 + minute - 2) % (24 * 60), 60)
    return ("On a Mac, only an administrator can schedule a wake. To wake for the run, run this once "
            "(InkVault never uses sudo itself):\n"
            f"  sudo pmset repeat wakeorpoweron MTWRFSU {h:02d}:{m:02d}:00\n"
            "This replaces any existing `pmset repeat` schedule. Without it, the run happens as soon as the Mac next wakes.")
