"""macOS: a LaunchAgent. launchd runs a calendar job it missed while the Mac slept as soon as it wakes."""
import os
import plistlib
import subprocess
from pathlib import Path

from .. import paths

LABEL = "org.inkvault.nightly"


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render(argv, hour, minute, log_path):
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": list(argv),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "ProcessType": "Background",
        # nightly writes its own log; this only catches a crash before it starts
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    })


def domain():
    return f"gui/{os.getuid()}"


def install(argv, hour, minute, wake):
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(render(argv, hour, minute, paths.home() / "launchd.log"))
    subprocess.run(["launchctl", "bootout", f"{domain()}/{LABEL}"], capture_output=True)  # replace an older one
    r = subprocess.run(["launchctl", "bootstrap", domain(), str(p)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"launchctl couldn't load {p}: {(r.stderr or r.stdout).strip()}")
    return f"LaunchAgent {p}"


def remove():
    p = plist_path()
    subprocess.run(["launchctl", "bootout", f"{domain()}/{LABEL}"], capture_output=True)
    existed = p.exists()
    p.unlink(missing_ok=True)
    return existed


def installed():
    return plist_path().exists()


def wake_note(hour, minute):
    h, m = divmod((hour * 60 + minute - 2) % (24 * 60), 60)
    return ("On a Mac, only an administrator can schedule a wake. To wake for the run, run this once "
            "(InkVault never uses sudo itself):\n"
            f"  sudo pmset repeat wakeorpoweron MTWRFSU {h:02d}:{m:02d}:00\n"
            "Without it, the run happens as soon as the Mac next wakes.")
