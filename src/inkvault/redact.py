"""Secrets out of everything screen capture stores: window titles and OCR text alike (spec §4).

Titles are redacted first (so a secret can't straddle the clip), then URL-cleaned and clipped to 300 characters
(urlclean.clean_title). Rules match the raw title in memory; only cleaned values are stored."""
import re

from . import urlclean

MASK = "[redacted]"
MIN_TEXT = 20

SECRET_WORDS = r"(?:api[_-]?key|key|token|secret|password|passwd|pwd|auth|bearer|credential)"
SHAPES = [
    re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),
    urlclean.JWT,
]
AFTER_WORD = re.compile(rf"(?i)(\b{SECRET_WORDS}\b\s*[:=]\s*[\"']?)([A-Za-z0-9+/_.-]{{12,}})")


def redact(text):
    if not text:
        return text
    for p in SHAPES:
        text = p.sub(MASK, text)
    return AFTER_WORD.sub(lambda m: m.group(1) + MASK, text)


def clean_title(title):
    return urlclean.clean_title(redact(title)) if title else title


def clean_text(lines):
    text = "\n".join(line.rstrip() for line in lines if line and line.strip())
    text = redact(text)
    return text if len(text.strip()) >= MIN_TEXT else None
