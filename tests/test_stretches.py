"""The watcher's pure core (spec §1): stretches from 2 s samples, without Windows."""
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


def settings(**kw):
    from inkvault import screen
    d = screen.defaults()
    d["text_apps"].append("Discord.exe")
    d.update(kw)
    return screen.validate(d)


def win(title="Chat", exe="Discord.exe", hwnd=1, pid=10, created=1):
    from inkvault.stretches import Identity
    return Identity(hwnd, pid, created, exe, title)


def run(core, polls, s=None, start=0.0, step=2.0):
    from inkvault.stretches import Sample
    s = s or settings()
    out = []
    for i, (w, idle, locked) in enumerate(polls):
        t = start + i * step
        out += [(i, a) for a in core.step(Sample(t, T0 + timedelta(seconds=t), w, idle, locked), s)]
    return out


def kinds(actions):
    return [type(a).__name__ for _, a in actions]


def core():
    from inkvault.stretches import Core
    n = iter(range(10_000))
    return Core(new_id=lambda: f"screen:{next(n)}")


def test_one_poll_is_a_flick():
    acts = run(core(), [(win("A"), 0, False), (win("B"), 0, False), (win("B"), 0, False)])
    assert kinds(acts) == ["Opened"] and acts[0][1].identity.title == "B"


def test_open_extend_close():
    acts = run(core(), [(win(), 0, False)] * 18 + [(win("Other"), 0, False)])
    assert kinds(acts)[0] == "Opened" and kinds(acts).count("Extended") == 1 and kinds(acts)[-1] == "Closed"
    assert acts[0][1].started == T0 and acts[0][1].ended == T0 + timedelta(seconds=2)


def test_idle_closes_at_last_input_never_before_start():
    from inkvault.stretches import IDLE_LIMIT
    acts = run(core(), [(win(), 0, False), (win(), 0, False), (win(), IDLE_LIMIT + 50, False)])
    assert kinds(acts)[-1] == "Closed" and acts[-1][1].ended == T0


def test_lock_and_exclusion_close_at_the_observing_poll():
    for polls in ([(win(), 0, False)] * 2 + [(win(), 0, True)],
                  [(win(), 0, False)] * 2 + [(win("New InPrivate tab"), 0, False)]):
        acts = run(core(), polls)
        assert kinds(acts) == ["Opened", "Closed"] and acts[-1][1].ended == T0 + timedelta(seconds=4)


def test_a_pause_starting_mid_stretch_closes_at_the_observing_poll():          # P12: the pause case
    c = core()
    acts = run(c, [(win(), 0, False)] * 2)
    paused = settings(paused_until=(T0 + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    acts += run(c, [(win(), 0, False)], paused, start=4.0)
    assert kinds(acts) == ["Opened", "Closed"] and acts[-1][1].ended == T0 + timedelta(seconds=4)


def test_poll_gap_closes_at_the_earlier_poll():
    from inkvault.stretches import Sample
    c, s = core(), settings()
    c.step(Sample(0, T0, win(), 0, False), s)
    c.step(Sample(2, T0 + timedelta(seconds=2), win(), 0, False), s)
    acts = c.step(Sample(100, T0 + timedelta(seconds=100), win(), 0, False), s)
    assert type(acts[0]).__name__ == "Closed" and acts[0].ended == T0 + timedelta(seconds=2)
    assert c.open.started == T0 + timedelta(seconds=100)


def test_dwell_and_per_window_budget_are_separate():
    c, s = core(), settings()
    assert kinds(run(c, [(win("A"), 0, False)] * 16, s)).count("ReadDue") == 1      # read at t = 30
    assert "ReadDue" not in kinds(run(c, [(win("B"), 0, False)] * 20, s, start=32.0))
    acts = run(c, [(win("B"), 0, False)] * 41, s, start=72.0)                        # t = 72 … 152, no gap
    due = [i for i, a in acts if type(a).__name__ == "ReadDue"]
    assert len(due) == 1 and 72.0 + due[0] * 2.0 == 150.0


def test_a_reused_pid_is_a_different_window():
    c = core()
    run(c, [(win(created=1), 0, False)] * 16)
    assert "ReadDue" in kinds(run(c, [(win(created=2), 0, False)] * 16, start=32.0))


def test_no_window_closes_and_timeline_only_apps_never_read():
    assert kinds(run(core(), [(win(), 0, False)] * 2 + [(None, 0, False)])) == ["Opened", "Closed"]
    assert "ReadDue" not in kinds(run(core(), [(win(exe="chrome.exe"), 0, False)] * 40))


def test_wall_clock_going_backwards_never_ends_before_start():
    from inkvault.stretches import Sample
    c, s = core(), settings()
    c.step(Sample(0, T0, win(), 0, False), s)
    c.step(Sample(2, T0 + timedelta(seconds=2), win(), 0, False), s)
    acts = c.step(Sample(4, T0 - timedelta(hours=1), win("Other"), 0, False), s)
    assert [a for a in acts if type(a).__name__ == "Closed"][0].ended >= T0


def test_drop_forgets_without_writing():
    c = core()
    run(c, [(win(), 0, False)] * 3)
    c.drop()
    assert c.open is None and c.close(T0) == []


def test_a_spinner_in_the_title_is_one_stretch_and_a_read_comes_due():     # I3
    glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    polls = [(win(f"{glyphs[i % len(glyphs)]} task"), 0, False) for i in range(20)]
    acts = run(core(), polls, s=settings())
    assert kinds(acts).count("Opened") == 1 and "Closed" not in kinds(acts)
    reads = [(i, a) for i, a in acts if type(a).__name__ == "ReadDue"]
    assert [i for i, _ in reads] == [15] and reads[0][1].identity.title == f"{glyphs[15 % 10]} task"
    assert acts[0][1].identity.title == "⠋ task"                           # the stretch keeps its first title


def test_alternating_symbols_and_a_ticking_timer_are_one_stretch_each():   # I3
    for titles in (["✳ Claude Code", "✶ Claude Code"] * 6, [f"Build 0:{40 + i}" for i in range(12)]):
        acts = run(core(), [(win(t), 0, False) for t in titles])
        assert kinds(acts).count("Opened") == 1 and "Closed" not in kinds(acts)


def test_a_real_title_change_is_still_two_stretches():                    # I3
    acts = run(core(), [(win("Inbox"), 0, False)] * 3 + [(win("Drafts"), 0, False)] * 3)
    assert kinds(acts) == ["Opened", "Closed", "Opened"]


def test_a_changed_process_is_never_merged_by_a_matching_title():          # I3
    for other in (win("Chat", hwnd=2), win("Chat", pid=11), win("Chat", created=2), win("Chat", exe="Teams.exe")):
        acts = run(core(), [(win(), 0, False)] * 3 + [(other, 0, False)] * 3)
        assert kinds(acts)[:2] == ["Opened", "Closed"], other


def test_title_key():
    from inkvault.stretches import title_key
    assert title_key("⠋ task ⠙") == "task" and title_key("Build 0:41") == title_key("Build 0:42") == "Build #:#"
    assert title_key("") == "" and title_key(None) == "" and title_key("...") == ""


def test_ended_never_goes_backwards_when_the_clock_steps_back():          # M: monotonic ended
    from inkvault.stretches import Extended, Sample
    c, s, w, out = core(), settings(), win(), []
    utcs = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50, 52,
            54, 56, 58, 60, -300, -298, -296, -294, -292, -290, -288, -286, -284, -282, -280, -278, -276, -274, -272,
            -270, -268]
    for i, u in enumerate(utcs):
        out += c.step(Sample(i * 2.0, T0 + timedelta(seconds=u), w, 0, False), s)
    ended = [a.ended for a in out if isinstance(a, Extended)]
    assert len(ended) >= 2 and ended == sorted(ended)
