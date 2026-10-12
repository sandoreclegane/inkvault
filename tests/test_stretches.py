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
