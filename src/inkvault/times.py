"""Timestamps from PiecesOS, read into this machine's local time."""
from datetime import datetime


def local(ts: str) -> datetime:
    """ISO timestamp (UTC, usually ending in 'Z') -> local time: 'when you work' should read in your hours.

    Python 3.10's fromisoformat doesn't accept the 'Z' suffix (3.11+ does), so it is spelled out.
    """
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone()
