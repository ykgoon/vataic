"""Plain-language narration of factor movement.

Factors are stored as an abstract 0-100 index that everyone starts at 50.
Strings like "Income Trajectory shifted from 50 to 35", "drifting to 35/100",
"ends above baseline" or "snapping back to baseline" are just the storage
format read aloud: a reader has no idea what 35 means, and "baseline" names a
data column rather than anything in the scenario.

Every user-facing description of a factor's movement goes through this module
instead. A change is narrated by how big it is and which way it went
("eases down", "drops sharply", "ends well below where it started"), never by
exposing the index, the number 50, or the word baseline.

The bands are relative to the 50 (neutral) starting point and tuned for the
0-100 index: under 3 points is noise, 25+ is a different world.
"""
from __future__ import annotations

import re

# |delta| thresholds, in index points.
_SLIGHT = 3.0
_NOTICE = 8.0
_CLEAR = 15.0
_STRONG = 25.0


def delta_of(value, start: float = 50.0) -> float:
    """Signed distance from the starting level; 0.0 for unusable input."""
    try:
        return float(value) - float(start)
    except (TypeError, ValueError):
        return 0.0


def magnitude(delta: float) -> int:
    """0 = flat, 1 = slight, 2 = noticeable, 3 = clear, 4 = dramatic."""
    d = abs(float(delta))
    if d >= _STRONG:
        return 4
    if d >= _CLEAR:
        return 3
    if d >= _NOTICE:
        return 2
    if d >= _SLIGHT:
        return 1
    return 0


def _up(delta: float) -> bool:
    return float(delta) > 0


def movement(delta: float) -> str:
    """Verb phrase for how a factor travels: "holds roughly steady",
    "eases down", "climbs sharply"."""
    m = magnitude(delta)
    if m == 0:
        return "holds roughly steady"
    up = _up(delta)
    return {
        1: "ticks up" if up else "eases down",
        2: "rises noticeably" if up else "falls noticeably",
        3: "climbs sharply" if up else "drops sharply",
        4: "rises steeply" if up else "falls steeply",
    }[m]


def end_state(delta: float) -> str:
    """Where a factor finishes, relative to where it began."""
    m = magnitude(delta)
    if m == 0:
        return "ends close to where it started"
    up = _up(delta)
    if m == 1:
        return "ends slightly higher than where it started" if up \
            else "ends slightly lower than where it started"
    if m == 2:
        return "ends noticeably higher than where it started" if up \
            else "ends noticeably lower than where it started"
    if m == 3:
        return "ends well above where it started" if up \
            else "ends well below where it started"
    return "ends far above where it started" if up \
        else "ends far below where it started"


def level_word(delta: float) -> str:
    """Very short form for chips and labels: "much lower", "flat"."""
    m = magnitude(delta)
    if m == 0:
        return "flat"
    up = _up(delta)
    if m == 1:
        return "a little higher" if up else "a little lower"
    if m == 2:
        return "higher" if up else "lower"
    return "much higher" if up else "much lower"


def onset(delta: float) -> str:
    """Early-signal phrasing: what to start noticing, before it has happened."""
    m = magnitude(delta)
    if m == 0:
        return "holding near its starting level"
    up = _up(delta)
    return {
        1: "starting to tick up" if up else "starting to ease down",
        2: "beginning to rise" if up else "beginning to fall",
        3: "starting to climb sharply" if up else "starting to drop sharply",
        4: "starting to surge" if up else "starting to slide sharply",
    }[m]


def stays(delta: float) -> str:
    """Assumption phrasing: this outcome depends on the factor staying put."""
    m = magnitude(delta)
    if m == 0:
        return "stays close to where it started"
    up = _up(delta)
    if up:
        return "stays up near this level rather than falling back"
    return "stays down near this level rather than recovering"


def reversal(delta: float) -> str:
    """Risk phrasing: what would undo this path.

    The direction matters for meaning, not just wording: for a factor that
    ended *down*, sliding back up toward its starting level is a rebound, not
    a recovery of health — "Financial Resilience recovering to where it
    started" would read as good news when it is the risk.
    """
    m = magnitude(delta)
    if m == 0:
        return "moving off that level"
    up = _up(delta)
    if up:
        return "falling back toward where it started"
    return "rebounding toward where it started"


def arc(delta: float) -> str:
    """The whole story of one factor in a single clause for causal paths.

    Deliberately one phrase and not movement() + end_state(): those two say
    the same thing at different resolutions ("rises steeply, and ends far
    above where it started"), which reads like a stutter in a list.
    """
    m = magnitude(delta)
    if m == 0:
        return "stays where it started through the whole horizon"
    up = _up(delta)
    return {
        1: "drifts slightly higher" if up else "drifts slightly lower",
        2: "moves up steadily" if up else "moves down steadily",
        3: "climbs and finishes well above where it started" if up
           else "slides and finishes well below where it started",
    }.get(m, "rises steeply to a level far above where it started" if up
           else "falls steeply to a level far below where it started")


def with_name(name: str, phrase: str) -> str:
    """Join a factor name to a narrated clause without doubling punctuation."""
    return f"{name} {phrase}"
# ---------------------------------------------------------------- prose repair
#
# Older explanations (and LLM explanations written before the prompt forbade
# it) quote the internal index inside otherwise good prose: "falls from its
# starting point of 50 to roughly 35 out of 100", "now around 67 on a 100-point
# scale", "drift below their starting baseline". The sentences are worth
# keeping; only the numbers and the storage word need to become plain English.
#
# Each rule rewrites a whole span, so no rule can leave a half-sentence behind
# ("falls ends well below"). Rules run most-specific first.

_SCALE_TAIL = r"(?:\s+out\s+of\s+100|\s*/\s*100|\s+on\s+a\s+100(?:-point)?\s+scale)?"

# "0/100", "(81/100)" — a bare parenthetical reading of the same value the
# surrounding phrase already describes. Worthless to a reader; drop it.
_PAREN_VALUE = re.compile(r"\s*\(\s*\d{1,3}\s*/\s*100\s*\)")

# "falls from its starting point of 50 to roughly 35 out of 100"
_FROM_TO = re.compile(
    r"\b(?:falls?|rises?|drops?|climbs?|moves?|slides?|slips?|drifts?|sinks?|"
    r"settles?|jumps?|grows?|shrinks?|goes|go|declines?|improves?|pulls?|"
    r"is|are|was|were)?\s*"
    r"(?:from\s+)?(?:its\s+|their\s+|the\s+)?"
    r"(?:starting\s+(?:point|level|baseline)\s+of\s+)?\d{1,3}\s+"
    r"(?:up\s+)?to\s+(?:roughly|about|around|approximately)?\s*(\d{1,3})"
    r"\s*(?:out\s+of\s+100|/\s*100|on\s+a\s+100(?:-point)?\s+scale)",
    re.I)

# "now around 67 on a 100-point scale", "about 36 out of 100", "at 78/100",
# "drift up to 62 out of 100" (the leading preposition is part of the phrase
# and must be consumed with it).
_BARE_VALUE = re.compile(
    r"\b(?P<prep>(?:of|up\s+to|to|at|is|was)\s+)?(?:now\s+)?"
    r"(?:around|roughly|about|approximately|near)?\s*(?P<v>\d{1,3})\s*"
    r"(?:out\s+of\s+100|/\s*100|on\s+a\s+100(?:-point)?\s+scale)",
    re.I)

# "(Habit Consistency, around 66)" — an index value in a parenthetical aside.
_PAREN_NAMED = re.compile(
    r"\(\s*([^()]{1,60}?),\s*(?:around|about|roughly|near)\s*(\d{1,3})\s*\)")

_WORD_FIXES = [
    (re.compile(r"\b(?:the\s+|its\s+|their\s+|a\s+)?starting\s+baseline\b", re.I),
     "starting point"),
    (re.compile(r"\bbaselines\b", re.I), "starting levels"),
    (re.compile(r"\bbaseline\b", re.I), "starting level"),
    (re.compile(r"\bstarting level of 50\b", re.I), "where it started"),
    (re.compile(r"\bthe\s+50-point\s+(midpoint|mark)\b", re.I), r"the midpoint"),
]


def sanitize_prose(text: str) -> str:
    """Rewrite index numbers and storage vocabulary in a sentence as words.

    Idempotent and conservative: only explicit references to the 0-100 scale,
    "/100" values, and the word "baseline" are touched, so ordinary prose and
    probabilities survive unchanged.
    """
    if not text:
        return text
    out = str(text)
    out = _PAREN_VALUE.sub("", out)
    out = _FROM_TO.sub(lambda m: end_state(delta_of(m.group(1))), out)
    out = _PAREN_NAMED.sub(
        lambda m: f"({m.group(1)}, at a {level_word(delta_of(m.group(2)))} level)", out)
    def _bare(m):
        # "drift up to 62 out of 100" -> "drift up to a higher level";
        # "at 78/100" -> "at a much higher level".
        prep = (m.group("prep") or "at ").strip()
        word = level_word(delta_of(m.group("v")))
        if prep in ("is", "was"):
            return f"{prep} at a {word} level"
        if prep == "of":
            return f"of a {word} level"
        return f"{prep} a {word} level"

    out = _BARE_VALUE.sub(_bare, out)
    for pat, repl in _WORD_FIXES:
        out = pat.sub(repl, out)
    # Tidy seams left by the substitutions.
    out = re.sub(r"\b(?:of|to|at|around|about|roughly)\s*([,.;:)])", r"\1", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"\b(?:of|to|at|around|about|roughly)\s*$", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def prose_has_index(text: str) -> bool:
    """True when a sentence still quotes the internal index."""
    if not text:
        return False
    return bool(re.search(r"/100|\bbaselines?\b|out of 100|100-point scale",
                          str(text), re.I))
