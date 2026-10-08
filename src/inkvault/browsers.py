"""Browser profiles on this computer, and which of them are the user's (browsers.json).

A profile can belong to someone else, so none is read until the user says it's theirs (`inkvault browsers`).
"""
import configparser
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths

NAMES = {"chrome": "Chrome", "edge": "Edge", "comet": "Comet", "brave": "Brave", "arc": "Arc", "vivaldi": "Vivaldi",
         "opera": "Opera", "chromium": "Chromium", "firefox": "Firefox"}
NOT_PROFILES = {"System Profile", "Guest Profile"}


@dataclass
class Profile:
    key: str             # "chrome/Profile 1": the browser and the profile's folder name
    browser: str         # "chrome"
    kind: str            # "chromium" or "firefox"
    history: Path        # History or places.sqlite
    name: str            # what the browser calls it
    email: str | None    # signed-in account, shown when choosing; never stored
    account: str | None  # SHA-256 of the account's id, stored with a choice
    created: str | None  # the folder's creation time, where the OS gives one


def user_data_dirs():
    """(browser, kind, folder) for every place a supported browser keeps its profiles on this platform."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        chromium = {"chrome": [local / "Google/Chrome/User Data"], "edge": [local / "Microsoft/Edge/User Data"],
                    "comet": [local / "Perplexity/Comet/User Data"],
                    "brave": [local / "BraveSoftware/Brave-Browser/User Data"],
                    "arc": sorted((local / "Packages").glob("TheBrowserCompany.Arc_*/LocalCache/Local/Arc/User Data")),
                    "vivaldi": [local / "Vivaldi/User Data"], "opera": [roaming / "Opera Software/Opera Stable"],
                    "chromium": [local / "Chromium/User Data"]}
        firefox = [roaming / "Mozilla/Firefox"]
    elif sys.platform == "darwin":
        support = home / "Library/Application Support"
        chromium = {"chrome": [support / "Google/Chrome"], "edge": [support / "Microsoft Edge"],
                    "comet": [support / "Comet"], "brave": [support / "BraveSoftware/Brave-Browser"],
                    "arc": [support / "Arc/User Data"], "vivaldi": [support / "Vivaldi"],
                    "opera": [support / "com.operasoftware.Opera"], "chromium": [support / "Chromium"]}
        firefox = [support / "Firefox"]
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        chromium = {"chrome": [config / "google-chrome"], "edge": [config / "microsoft-edge"],
                    "brave": [config / "BraveSoftware/Brave-Browser"], "vivaldi": [config / "vivaldi"],
                    "opera": [config / "opera"], "chromium": [config / "chromium"]}
        firefox = [home / ".mozilla/firefox", home / "snap/firefox/common/.mozilla/firefox",
                   home / ".var/app/org.mozilla.firefox/.mozilla/firefox"]
    return ([(browser, "chromium", d) for browser, dirs in chromium.items() for d in dirs]
            + [("firefox", "firefox", d) for d in firefox])


def account_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def created_time(folder):
    """When the folder was made: st_birthtime (macOS; Windows on Python 3.12+), st_ctime on older Windows Pythons
    (where it is the creation time). None on Linux, which doesn't say."""
    st = folder.stat()
    t = getattr(st, "st_birthtime", None)
    if t is None and sys.platform == "win32":
        t = st.st_ctime
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chromium_profiles(browser, root):
    try:
        info = json.loads((root / "Local State").read_text(encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError, TypeError):
        info = {}
    info = info if isinstance(info, dict) else {}
    folders = [root] if (root / "History").is_file() else []  # Opera keeps its one profile in the folder itself
    folders += sorted(p for p in root.iterdir()
                      if p.is_dir() and p.name not in NOT_PROFILES and (p / "History").is_file())
    for folder in folders:
        entry = info.get(folder.name) if folder != root else None
        entry = entry if isinstance(entry, dict) else {}
        yield Profile(f"{browser}/{folder.name}", browser, "chromium", folder / "History",
                      entry.get("name") or folder.name, entry.get("user_name") or None,
                      account_hash(entry.get("gaia_id") or entry.get("user_name")), created_time(folder))


def firefox_profiles(root):
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(root / "profiles.ini", encoding="utf-8")
    except configparser.Error:
        return
    for section in ini.sections():
        if not section.startswith("Profile") or not ini.has_option(section, "Path"):
            continue
        relative = ini.get(section, "IsRelative", fallback="1") == "1"
        folder = root / ini.get(section, "Path") if relative else Path(ini.get(section, "Path"))
        if (folder / "places.sqlite").is_file():
            yield Profile(f"firefox/{folder.name}", "firefox", "firefox", folder / "places.sqlite",
                          ini.get(section, "Name", fallback=folder.name), None, None, created_time(folder))


def find_profiles():
    """Every browser profile on this computer, sorted by key. A browser that isn't installed is simply absent."""
    found = {}
    for browser, kind, root in user_data_dirs():
        if not root.is_dir():
            continue
        try:
            for p in (chromium_profiles(browser, root) if kind == "chromium" else firefox_profiles(root)):
                found.setdefault(p.key, p)
        except OSError as e:
            print(f"{NAMES[browser]}: couldn't look in {root} ({e})")
    return [found[k] for k in sorted(found)]


def load_choices():
    """browsers.json. A missing or unreadable file means nothing is chosen, so nothing is read."""
    try:
        data = json.loads(paths.browsers_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        print(f"Couldn't read {paths.browsers_file()} ({e}); treating every browser profile as not chosen yet.")
        data = {}
    data = data if isinstance(data, dict) else {}
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    sites = data.get("skip_sites") if isinstance(data.get("skip_sites"), list) else []
    return {"profiles": profiles, "skip_sites": [s for s in sites if isinstance(s, str)]}


def save_choices(choices):
    """Callers hold the nightly lock (see cli.py); the temp file's name is unique anyway."""
    f = paths.browsers_file()
    fd, tmp = tempfile.mkstemp(prefix="browsers-", suffix=".tmp", dir=f.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(json.dumps(choices, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_answers(profiles, answers):
    """Merge answers ({key: True/False}) into browsers.json as it is now, and save. Called under the lock, after
    asking: so an answer never overwrites a choice another command saved in the meantime."""
    choices = load_choices()
    for p in profiles:
        if p.key in answers:
            set_choice(choices, p, answers[p.key])
    save_choices(choices)
    return choices


def state(profile, choices):
    """("yes" | "no" | "new", why a chosen profile is new again, or None). A "yes" is bound to the profile's
    folder creation time and account; any change sends it back to "new". Best effort: Linux gives no creation
    time, and a profile never signed in has no account."""
    c = choices["profiles"].get(profile.key)
    if not isinstance(c, dict) or c.get("choice") not in ("yes", "no"):
        return "new", None
    if c["choice"] == "yes":
        if c.get("created") != profile.created:
            return "new", "its folder was made again since you chose it"
        if c.get("account") != profile.account:
            return "new", "its signed-in account changed since you chose it"
    return c["choice"], None


def set_choice(choices, profile, yes):
    choices["profiles"][profile.key] = ({"choice": "yes", "created": profile.created, "account": profile.account}
                                        if yes else {"choice": "no"})


def describe(profile, choices=None):
    who = f" ({profile.email})" if profile.email else ""
    mark = f"  [{state(profile, choices)[0]}]" if choices is not None else ""
    return f"{NAMES[profile.browser]}: {profile.name}{who}  {profile.key}{mark}"


def ask(profiles, read=input, choices=None):
    """Ask which of these profiles are the user's. Returns {key: True/False}, or None if nothing was answered."""
    for i, p in enumerate(profiles, 1):
        print(f"  {i}. {describe(p, choices)}")
    while True:
        try:
            answer = read("Which of these are yours? Numbers separated by spaces, `all`, or `none`: ").strip().lower()
        except EOFError:
            return None
        if answer in ("all", "none"):
            return {p.key: answer == "all" for p in profiles}
        try:
            picked = {int(n) for n in answer.replace(",", " ").split()}
        except ValueError:
            picked = set()
        if picked and all(1 <= n <= len(profiles) for n in picked):
            return {p.key: i in picked for i, p in enumerate(profiles, 1)}
        print("Please type numbers from the list, `all`, or `none`.")


def interactive():
    return bool(sys.stdin and sys.stdin.isatty())


def ask_about_new(profiles, choices, read=input):
    """Ask about profiles not chosen yet (or chosen, but changed since). Returns the answers, or {} if there was
    nothing to ask or no answer; the caller records them under the lock (record_answers)."""
    waiting = [p for p in profiles if state(p, choices)[0] == "new"]
    if not waiting:
        return {}
    print("InkVault found browser profiles it hasn't asked you about yet:")
    return ask(waiting, read) or {}
