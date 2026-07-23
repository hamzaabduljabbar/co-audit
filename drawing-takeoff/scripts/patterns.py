"""
patterns.py - Detection rules for the drawing-takeoff pipeline.

Everything drawing-specific lives here so you can tune it per client set
without touching the engine. Counting objects is only as good as these
patterns, so edit them for the tagging convention your drawings actually use.
"""

import re

# ---------------------------------------------------------------------------
# Discipline lookup from the leading letter(s) of a sheet number (A-101, S2.1)
# ---------------------------------------------------------------------------
DISCIPLINE_MAP = {
    "G": "General",
    "C": "Civil",
    "L": "Landscape",
    "A": "Architectural",
    "I": "Interiors",
    "S": "Structural",
    "M": "Mechanical",
    "P": "Plumbing",
    "E": "Electrical",
    "F": "Fire Protection",
    "T": "Telecom",
    "FP": "Fire Protection",
    "FA": "Fire Alarm",
}

# A sheet number in a title block, e.g. A-101, A204, S2.1, M-2.01, E1.1a
SHEET_NUMBER_RE = re.compile(r"^([A-Z]{1,2})[- ]?(\d{1,4}(?:\.\d{1,3})?[A-Za-z]?)$")

# ---------------------------------------------------------------------------
# Scale detection. Returns a scale_ratio = real-world units per paper unit.
# Metric   1:100                     -> 100
# Imperial 1/4" = 1'-0"              -> 48   (12 real inches / 0.25 paper inch)
# ---------------------------------------------------------------------------
METRIC_SCALE_RE = re.compile(r"\b1\s*[:/]\s*(\d{1,4})\b")
IMPERIAL_SCALE_RE = re.compile(
    r"(\d+(?:/\d+)?)\s*\"\s*=\s*(\d+)\s*'(?:\s*-?\s*(\d+)\s*\")?"
)


def _frac_to_float(tok: str) -> float:
    if "/" in tok:
        n, d = tok.split("/")
        return float(n) / float(d)
    return float(tok)


def detect_scale(text: str):
    """Return (scale_label, scale_ratio) or (None, None)."""
    m = IMPERIAL_SCALE_RE.search(text)
    if m:
        paper_in = _frac_to_float(m.group(1))
        real_in = float(m.group(2)) * 12 + (float(m.group(3)) if m.group(3) else 0)
        if paper_in > 0:
            return (m.group(0).replace(" ", ""), real_in / paper_in)
    m = METRIC_SCALE_RE.search(text)
    if m:
        return (f"1:{m.group(1)}", float(m.group(1)))
    return (None, None)


# ---------------------------------------------------------------------------
# Dimension annotations already printed on the sheet. HIGH confidence because
# we read the number the designer wrote, not pixels.
#
# In real drawing PDFs the string "12'-1/2"" is usually split into TWO adjacent
# tokens: a FEET token  12'  and an INCH token  1/2"  sitting right next to it
# (side by side on a horizontal dimension line, or stacked on a rotated one).
# So we classify tokens and pair them spatially in takeoff.py rather than
# trying to regex the whole thing out of one string.
# ---------------------------------------------------------------------------
FEET_RE = re.compile(r"(\d{1,3})'")                       # 12'  205'
INCH_RE = re.compile(r"(\d{1,2})?\s*(\d{1,2}/\d{1,2})?\"")  # 9"  1/2"  9 1/2"
SINGLE_DIM_RE = re.compile(r"(\d{1,3})'\s*-\s*(\d{1,2})(?:\s+(\d{1,2}/\d{1,2}))?\"")
METRIC_DIM_RE = re.compile(r"(\d{1,5}(?:\.\d+)?)\s*(mm|cm|m)\b")

# Feet counts above this are almost always a glued reference number, not a real
# building dimension. Tune per set.
MAX_PLAUSIBLE_FEET = 400


def _frac(tok):
    if not tok:
        return 0.0
    if "/" in tok:
        n, d = tok.split("/")
        return float(n) / float(d)
    return float(tok)


def parse_inches(text: str):
    """Parse an inch token (9"  1/2"  9 1/2") to inches, or None."""
    m = INCH_RE.fullmatch(text.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    whole = float(m.group(1)) if m.group(1) else 0.0
    return whole + _frac(m.group(2))


def feet_inches_to_m(feet: float, inches: float):
    return round(feet * 0.3048 + inches * 0.0254, 4)


def parse_length_to_m(text: str):
    """Parse a *single* self-contained dimension token to metres, or None.
    Used for the rare case where feet-inches live in one token, plus metric."""
    t = text.strip()
    m = SINGLE_DIM_RE.fullmatch(t)
    if m:
        feet = float(m.group(1))
        if feet > MAX_PLAUSIBLE_FEET:
            return None
        inches = float(m.group(2)) + _frac(m.group(3))
        return feet_inches_to_m(feet, inches)
    m = METRIC_DIM_RE.fullmatch(t)
    if m:
        val, unit = float(m.group(1)), m.group(2)
        return round({"mm": val / 1000, "cm": val / 100, "m": val}[unit], 4)
    return None


# ---------------------------------------------------------------------------
# Schedule detection. If a table's header row contains one of these keywords
# we treat each data row as one object of that type (HIGH confidence: it is a
# literal schedule row, not a guess).
# ---------------------------------------------------------------------------
SCHEDULE_KEYWORDS = {
    "door": "door",
    "window": "window",
    "column": "column",
    "beam": "beam",
    "footing": "footing",
    "luminaire": "luminaire",
    "light": "luminaire",
    "fixture": "fixture",
    "panelboard": "panel",
    "panel": "panel",
    "equipment": "equipment",
    "diffuser": "diffuser",
    "grille": "grille",
    "vav": "vav",
    "room": "room",
    "finish": "finish",
    "penetration": "penetration",
    "wall type": "wall",
    "partition": "wall",
}

# First-cell values that mark a header/label row, not a countable schedule entry.
SCHEDULE_STOPWORDS = {
    "", "mark", "no", "no.", "id", "tag", "type", "room", "number", "qty",
    "scale", "scale:", "notes", "note", "general", "symbol", "key", "legend",
    "addition", "existing", "existing bldg", "total", "remarks", "comments",
    "door number", "door schedule", "window schedule", "panel schedule",
    "revision", "date", "sheet", "drawing",
}

# When several schedule types share a page we can't safely attribute rows to one,
# so we resolve by priority (dedicated single-type sheets are the common case).
TYPE_PRIORITY = ["door", "window", "panel", "luminaire", "fixture",
                 "equipment", "penetration", "beam", "column", "footing",
                 "finish", "room", "wall", "diffuser", "grille", "vav"]

# ---------------------------------------------------------------------------
# Tag patterns for counting objects from callouts on plan views (MEDIUM
# confidence: pattern match on the drawing body, not a schedule row). These
# are deliberately conservative defaults - edit per set. Each entry:
#   type -> compiled regex that must FULLMATCH a single word token
# ---------------------------------------------------------------------------
TAG_PATTERNS = {
    "door":   re.compile(r"D[- ]?\d{2,3}[A-Z]?$"),
    "window": re.compile(r"W[- ]?\d{2,3}[A-Z]?$"),
    "column": re.compile(r"C[- ]?\d{1,3}$"),
    "grid":   re.compile(r"[A-Z]{1,2}-\d{1,2}$"),  # grid intersection bubbles
}
