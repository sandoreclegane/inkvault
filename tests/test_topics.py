"""Rules for turning Pieces' topic tags into the dashboard's Ongoing and Bursts groups."""
from datetime import date, timedelta

from inkvault import topics

D0 = date(2026, 1, 1)


def every(n, step, start=D0):
    """n dates, `step` days apart."""
    return [start + timedelta(days=step * i) for i in range(n)]


def test_normalize_merges_case_punctuation_and_spacing():
    assert {topics.normalize(t) for t in ["Work-Life-Balance", "work_life  balance", " work life balance "]} \
        == {"work life balance"}
    assert topics.normalize("") == "" and topics.normalize(None) == "" and topics.normalize(" - ") == ""


def test_peak_window_is_21_days_inclusive():
    assert topics.peak_share([D0, D0 + timedelta(days=20)]) == 1.0
    assert topics.peak_share([D0, D0 + timedelta(days=21)]) == 0.5


def test_several_summaries_on_one_day_count_separately():
    assert topics.peak_share([D0, D0, D0, date(2026, 6, 1)]) == 0.75


def test_clear_burst_and_clear_ongoing():
    got = topics.classify({"launch": every(8, 1), "philosophy": every(10, 19)})
    assert got == {"ongoing": ["philosophy"], "bursts": ["launch"]}


def test_burst_at_exactly_the_threshold_is_included():
    tag = every(7, 3) + every(3, 40, start=date(2026, 4, 1))  # 7 of 10 in one window
    assert topics.peak_share(tag) == 0.7
    assert topics.classify({"x": tag})["bursts"] == ["x"]


def test_ongoing_needs_four_months():
    four = every(7, 19)   # Jan 1 .. Apr 25: Jan, Feb, Mar, Apr
    three = every(6, 15)  # Jan 1 .. Mar 17: Jan, Feb, Mar
    assert len({(d.year, d.month) for d in four}) == 4 and len({(d.year, d.month) for d in three}) == 3
    assert topics.peak_share(four) < 0.5 and topics.peak_share(three) < 0.5
    got = topics.classify({"four": four, "three": three})
    assert got == {"ongoing": ["four"], "bursts": []}


def test_in_between_and_too_rare_tags_are_dropped():
    between = every(4, 2) + every(4, 30, start=date(2026, 3, 1))  # peak 0.5: neither group
    rare = every(5, 1)                                             # below MIN_SESSIONS
    assert topics.peak_share(between) == 0.5
    assert topics.classify({"between": between, "rare": rare}) == {"ongoing": [], "bursts": []}


def test_groups_are_capped_and_ordered_by_count_then_name():
    tags = {f"t{i:02}": every(6 + i % 3, 1) for i in range(20)}
    got = topics.classify(tags)["bursts"]
    assert len(got) == topics.MAX_PER_GROUP
    assert got[:3] == ["t02", "t05", "t08"]  # 8 sessions each, alphabetical
    assert got == sorted(got, key=lambda t: (-len(tags[t]), t))
