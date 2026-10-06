"""Topics from Pieces' own topic tags: ongoing interests and short bursts, for the dashboard's Topics card.

Pure functions: no database or file access, so the rules are easy to test with hand-made dates.
"""
import re

MIN_SESSIONS = 6        # a tag needs this many summaries to be considered
WINDOW_DAYS = 21        # length of a tag's "busiest window"
BURST_MIN_PEAK = 0.7    # burst: at least this share of its summaries fall in its busiest window
ONGOING_MONTHS = 4      # ongoing: appears in at least this many calendar months...
ONGOING_MAX_PEAK = 0.5  # ...and less than this share falls in its busiest window
MAX_PER_GROUP = 12


def normalize(text):
    """'Work-Life-Balance', 'work_life  balance' -> 'work life balance'."""
    if not isinstance(text, str):
        return ""  # a malformed record (number, list, ...) is no topic, and must never stop indexing
    return re.sub(r"[\s_-]+", " ", text.lower()).strip()


def peak_share(days):
    """Largest share of the dates falling in any one WINDOW_DAYS-day span (inclusive). Needs at least one date;
    classify only calls it with MIN_SESSIONS or more."""
    ds = sorted(days)
    best = start = 0
    for end, d in enumerate(ds):
        while (d - ds[start]).days >= WINDOW_DAYS:
            start += 1
        best = max(best, end - start + 1)
    return best / len(ds)


def classify(tag_days):
    """{tag: [date per summary]} -> {"ongoing": [...], "bursts": [...]}, each by count desc, then name."""
    ongoing, bursts = [], []
    for tag, days in tag_days.items():
        if len(days) < MIN_SESSIONS:
            continue
        peak = peak_share(days)
        if peak >= BURST_MIN_PEAK:
            bursts.append(tag)
        elif peak < ONGOING_MAX_PEAK and len({(d.year, d.month) for d in days}) >= ONGOING_MONTHS:
            ongoing.append(tag)
    order = lambda tag: (-len(tag_days[tag]), tag)
    return {"ongoing": sorted(ongoing, key=order)[:MAX_PER_GROUP], "bursts": sorted(bursts, key=order)[:MAX_PER_GROUP]}
