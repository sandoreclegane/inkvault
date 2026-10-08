"""Finding browser profiles, and which ones the user said are theirs."""
import contextlib
import hashlib
import sys
from pathlib import Path

import pytest

import webfixtures


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("INKVAULT_HOME", str(tmp_path / "vault"))
    return tmp_path


def test_finds_chromium_profiles_with_their_names_and_accounts(home, monkeypatch):
    from inkvault import browsers
    root = home / "chrome"
    webfixtures.chromium_profile(root, "Default", name="Person 1", user_name="me@example.com", gaia_id="111")
    webfixtures.chromium_profile(root, "Profile 3")  # has history, but Local State doesn't list it
    webfixtures.chromium_profile(root, "System Profile")
    webfixtures.chromium_profile(root, "Guest Profile")
    (root / "Crashpad").mkdir()
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    found = browsers.find_profiles()
    assert [p.key for p in found] == ["chrome/Default", "chrome/Profile 3"]
    me, other = found
    assert (me.name, me.email, me.kind) == ("Person 1", "me@example.com", "chromium")
    assert me.account == hashlib.sha256(b"111").hexdigest()
    assert me.history == root / "Default" / "History"
    assert (other.name, other.email, other.account) == ("Profile 3", None, None)


def test_opera_keeps_its_profile_in_the_user_data_folder(home, monkeypatch):
    from inkvault import browsers
    root = home / "Opera Stable"
    webfixtures.chromium_profile(root, None)
    webfixtures.install(monkeypatch, ("opera", "chromium", root))
    assert [p.key for p in browsers.find_profiles()] == ["opera/Opera Stable"]


def test_finds_firefox_profiles_from_profiles_ini(home, monkeypatch):
    from inkvault import browsers
    root = home / "firefox"
    webfixtures.firefox_profile(root, "abcd.default-release", name="default-release")
    with open(root / "profiles.ini", "a", encoding="utf-8") as f:
        f.write("\n[Profile9]\nName=gone\nIsRelative=1\nPath=Profiles/gone\n\n[Install4F96D1932A9F858E]\nDefault=x\n")
    webfixtures.install(monkeypatch, ("firefox", "firefox", root))
    (p,) = browsers.find_profiles()
    assert (p.key, p.name, p.kind) == ("firefox/abcd.default-release", "default-release", "firefox")
    assert p.history == root / "Profiles" / "abcd.default-release" / "places.sqlite"


@pytest.mark.skipif(sys.platform != "win32", reason="Arc's package folder is a Windows layout")
def test_arc_is_looked_for_in_its_windows_package_folder(home, monkeypatch):
    from inkvault import browsers
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
    arc = home / "local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4/LocalCache/Local/Arc/User Data"
    arc.mkdir(parents=True)
    assert ("arc", "chromium", arc) in browsers.real_user_data_dirs()
    assert ("chrome", "chromium", home / "local/Google/Chrome/User Data") in browsers.real_user_data_dirs()


def test_a_browser_that_isnt_installed_is_simply_absent(home, monkeypatch):
    from inkvault import browsers
    webfixtures.install(monkeypatch, ("chrome", "chromium", home / "nowhere"), ("firefox", "firefox", home / "nope"))
    assert browsers.find_profiles() == []


def profile(key):
    from inkvault import browsers
    return browsers.Profile(key, key.split("/")[0], "chromium", Path("History"), key.split("/")[1], None, None, None)


def test_asking_takes_numbers_all_or_none(capsys):
    from inkvault import browsers
    ps = [profile("chrome/Default"), profile("chrome/Profile 2")]
    answers = iter(["", "7", "2"])
    assert browsers.ask(ps, read=lambda _: next(answers)) == {"chrome/Default": False, "chrome/Profile 2": True}
    assert capsys.readouterr().out.count("Please type numbers from the list") == 2
    assert browsers.ask(ps, read=lambda _: "all") == {"chrome/Default": True, "chrome/Profile 2": True}
    assert browsers.ask(ps, read=lambda _: "none") == {"chrome/Default": False, "chrome/Profile 2": False}

    def eof(_):
        raise EOFError
    assert browsers.ask(ps, read=eof) is None


def test_a_profile_is_new_until_chosen_and_the_choice_is_saved(home, monkeypatch):
    from inkvault import browsers, paths
    webfixtures.chromium_profile(home / "chrome", "Default", name="Me")
    webfixtures.install(monkeypatch, ("chrome", "chromium", home / "chrome"))
    (p,) = browsers.find_profiles()
    choices = browsers.load_choices()
    assert choices == {"profiles": {}, "skip_sites": []}
    assert browsers.state(p, choices) == ("new", None)
    browsers.set_choice(choices, p, True)
    browsers.save_choices(choices)
    assert paths.browsers_file().exists()
    assert browsers.state(p, browsers.load_choices()) == ("yes", None)


def test_a_chosen_profile_goes_back_to_new_when_its_evidence_changes(home, monkeypatch):
    from inkvault import browsers
    root = home / "chrome"
    webfixtures.chromium_profile(root, "Default", name="Me", user_name="me@example.com", gaia_id="111")
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    (p,) = browsers.find_profiles()
    choices = browsers.load_choices()
    browsers.set_choice(choices, p, True)
    assert browsers.state(p, choices) == ("yes", None)

    webfixtures.chromium_profile(root, "Default", name="Me")  # signed out: the account disappears
    (p,) = browsers.find_profiles()
    assert browsers.state(p, choices) == ("new", "its signed-in account changed since you chose it")

    browsers.set_choice(choices, p, True)
    monkeypatch.setattr(browsers, "created_time", lambda folder: "2031-01-01T00:00:00Z")
    (p,) = browsers.find_profiles()
    assert browsers.state(p, choices) == ("new", "its folder was made again since you chose it")

    browsers.set_choice(choices, p, False)
    monkeypatch.setattr(browsers, "created_time", lambda folder: "2032-01-01T00:00:00Z")
    (p,) = browsers.find_profiles()
    assert browsers.state(p, choices) == ("no", None)  # a "no" never turns into a "yes" by itself


def test_answers_are_merged_into_the_choices_saved_since(home, monkeypatch):
    from inkvault import browsers
    root = home / "chrome"
    webfixtures.chromium_profile(root, "Default", name="Me")
    webfixtures.chromium_profile(root, "Profile 1", name="Work")
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    me, work = browsers.find_profiles()
    answers = browsers.ask_about_new([me, work], browsers.load_choices(), read=lambda _: "1")
    assert answers == {"chrome/Default": True, "chrome/Profile 1": False}
    other = browsers.load_choices()  # meanwhile, another command saved a skip site
    other["skip_sites"].append("bank.example")
    browsers.save_choices(other)
    saved = browsers.record_answers([me, work], answers)
    assert saved["skip_sites"] == ["bank.example"]
    assert browsers.state(me, saved) == ("yes", None) and browsers.state(work, saved) == ("no", None)
    assert not list(browsers.paths.home().glob("*.tmp"))


def test_an_unreadable_choices_file_means_nothing_is_chosen(home, capsys):
    from inkvault import browsers, paths
    paths.browsers_file().write_text("{not json", encoding="utf-8")
    assert browsers.load_choices() == {"profiles": {}, "skip_sites": []}
    assert "treating every browser profile as not chosen yet" in capsys.readouterr().out


def test_the_browsers_command_lists_and_sets_choices(home, monkeypatch, capsys):
    from inkvault import browsers, cli
    root = home / "chrome"
    webfixtures.chromium_profile(root, "Default", name="Me", user_name="me@example.com")
    webfixtures.chromium_profile(root, "Profile 2", name="TJ")
    webfixtures.install(monkeypatch, ("chrome", "chromium", root))
    assert cli.main(["browsers"]) == 0
    out = capsys.readouterr().out
    assert "Chrome: Me (me@example.com)  chrome/Default  [new]" in out and "chrome/Profile 2  [new]" in out
    assert cli.main(["browsers", "--yes", "chrome/Default"]) == 0
    assert cli.main(["browsers", "--no", "chrome/Profile 2"]) == 0
    assert browsers.load_choices()["profiles"]["chrome/Profile 2"] == {"choice": "no"}
    assert browsers.load_choices()["profiles"]["chrome/Default"]["choice"] == "yes"
    assert cli.main(["browsers", "--yes", "chrome/Nope"]) == 1
    assert cli.main(["browsers", "--no", "chrome/Nope"]) == 1


def test_skip_site_is_saved_lowercase_and_unskip_takes_it_back(home):
    from inkvault import browsers, cli
    assert cli.main(["browsers", "--skip-site", "Bank.Example"]) == 0
    assert browsers.load_choices()["skip_sites"] == ["bank.example"]
    assert cli.main(["browsers", "--unskip-site", "bank.example"]) == 0
    assert browsers.load_choices()["skip_sites"] == []


def test_a_choice_is_merged_with_what_another_command_saved_while_it_waited(home, monkeypatch):
    from inkvault import browsers, cli, nightly
    real = nightly.lock

    @contextlib.contextmanager
    def lock_after_someone_else(purpose="rescue"):
        other = browsers.load_choices()  # another command saves while this one waits for the lock
        other["skip_sites"].append("first.example")
        browsers.save_choices(other)
        with real(purpose):
            yield
    monkeypatch.setattr(nightly, "lock", lock_after_someone_else)
    assert cli.main(["browsers", "--skip-site", "second.example"]) == 0
    assert browsers.load_choices()["skip_sites"] == ["first.example", "second.example"]
