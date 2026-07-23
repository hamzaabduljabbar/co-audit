"""
drawdiff.py - Semantic revision diff for construction drawing sets.

Pixel-overlay tools (Bluebeam Compare, web diff sites) tell you *where pixels
changed*. They can't tell you *what* changed in words, and they light up the
whole sheet if the title block shifts. This reads the vector TEXT layer of both
revisions and reports change at the level a takeoff actually cares about:

    - schedule objects added / removed / with a changed attribute
    - printed dimensions that changed
    - general notes that changed

Every change is tagged with a confidence, same reliability model as takeoff.py.

Usage:
    py scripts/drawdiff.py SET_revC.pdf SET_revD.pdf
    py scripts/drawdiff.py old.pdf new.pdf --json report.json

It does not need grid-ruled tables (many real schedules have none): rows are
recovered by clustering words on their y-position, columns by x-position.
"""
import argparse
import json
import os
import re
import sys

import fitz  # PyMuPDF

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patterns  # noqa: E402

# A schedule mark / callout: F09, F10, D-01, W05, C3, B-12a ...
MARK_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Za-z]?$")

# Words that start a header row (used to locate the header and its columns).
HEADER_HINTS = {
    "MARK", "TYPE", "SIZE", "DEPTH", "WIDTH", "HEIGHT", "REINFORCEMENT",
    "CONCRETE", "REMARKS", "MATERIAL", "FINISH", "RATING", "QTY", "REF",
}

# Schedule title -> object type.
TITLE_TYPE = [
    ("FOOTING", "footing"),
    ("PILE", "pile"),
    ("COLUMN", "column"),
    ("BEAM", "beam"),
    ("DOOR", "door"),
    ("OPENING", "opening"),
    ("WINDOW", "window"),
    ("PANEL", "panel"),
    ("FIXTURE", "fixture"),
    ("SCHEDULE", "schedule"),  # generic fallback
]

ROW_TOL = 5.0     # points; words within this y-distance are the same row
NOTE_RE = re.compile(r"^\d{1,2}[.)]\s+")


def _lines(page):
    """Group a page's words into rows. Returns list of (y, [ (x0,text), ... ])."""
    words = page.get_text("words")  # x0,y0,x1,y1,text,block,line,word
    words.sort(key=lambda w: (round(w[1] / ROW_TOL), w[0]))
    rows, cur, cur_y = [], [], None
    for x0, y0, x1, y1, text, *_ in words:
        if not text.strip():
            continue
        if cur_y is None or abs(y0 - cur_y) <= ROW_TOL:
            cur.append((x0, text.strip()))
            cur_y = y0 if cur_y is None else cur_y
        else:
            rows.append((cur_y, sorted(cur)))
            cur, cur_y = [(x0, text.strip())], y0
    if cur:
        rows.append((cur_y, sorted(cur)))
    return rows


def _page_title(page):
    txt = page.get_text().upper()
    for key, typ in TITLE_TYPE:
        if key in txt:
            return typ, key
    return None, None


def _sheet_number(page):
    """Sheet number from the title block: prefer the candidate nearest the
    bottom-right corner, not the first one in reading order (a schedule mark
    like F10 also matches the sheet-number pattern)."""
    w, h = page.rect.width, page.rect.height
    best, best_score = None, -1.0
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        tok = text.strip()
        m = patterns.SHEET_NUMBER_RE.match(tok)
        if m and m.group(1) in patterns.DISCIPLINE_MAP:
            score = (x0 / w) + (y0 / h)  # higher = closer to bottom-right
            if score > best_score:
                best, best_score = tok, score
    return best


def _extract_schedule(page, obj_type):
    """Row-cluster a schedule page into {mark: {header: value}}."""
    rows = _lines(page)
    # locate header row: the one with the most HEADER_HINTS tokens
    header_idx, header = -1, None
    for i, (_, cells) in enumerate(rows):
        toks = [t.upper() for _, t in cells]
        hits = sum(1 for t in toks if t in HEADER_HINTS)
        if hits >= 2:
            header = cells  # [(x0, label), ...]
            header_idx = i
            break
    objects = {}
    if header_idx < 0:
        return objects
    hx = [(x, lbl.upper()) for x, lbl in header]
    for _, cells in rows[header_idx + 1:]:
        if not cells:
            continue
        mark = cells[0][1]
        if not MARK_RE.match(mark):
            continue
        attrs = {}
        for x0, tok in cells[1:]:
            # assign token to nearest header column by x
            lbl = min(hx, key=lambda h: abs(h[0] - x0))[1]
            attrs[lbl] = (attrs.get(lbl, "") + " " + tok).strip()
        objects[mark] = {"type": obj_type, "attrs": attrs}
    return objects


def _extract_dimensions(page):
    """Collect printed dimension strings on a sheet (feet-inch + metric)."""
    words = page.get_text("words")
    dims = set()
    feet, inch = [], []
    for x0, y0, x1, y1, text, *_ in words:
        t = text.strip()
        if patterns.SINGLE_DIM_RE.fullmatch(t):
            dims.add(t.replace(" ", ""))
        elif patterns.METRIC_DIM_RE.fullmatch(t):
            dims.add(t.replace(" ", ""))
        elif patterns.FEET_RE.fullmatch(t):
            feet.append((x0, y0, t))
        elif patterns.INCH_RE.fullmatch(t) and (any(c.isdigit() for c in t)):
            inch.append((x0, y0, t))
    # pair feet + inch tokens sitting next to each other
    for fx, fy, ft in feet:
        best, bestd = None, 75.0
        for ix, iy, it in inch:
            d = ((fx - ix) ** 2 + (fy - iy) ** 2) ** 0.5
            if d < bestd:
                best, bestd = it, d
        dims.add((ft + "-" + best).replace(" ", "") if best else ft)
    return dims


def _extract_notes(page):
    """General notes: numbered lines under a NOTES heading."""
    txt = page.get_text()
    if "NOTE" not in txt.upper():
        return {}
    notes = {}
    for raw in txt.splitlines():
        line = raw.strip()
        m = NOTE_RE.match(line)
        if m:
            num = re.match(r"\d{1,2}", line).group(0)
            notes[num] = line[m.end():].strip()
    return notes


def read_set(path):
    """Extract a comparable structure from one revision PDF."""
    doc = fitz.open(path)
    data = {"objects": {}, "dims": {}, "notes": {}, "sheets": {}}
    for i, page in enumerate(doc):
        obj_type, _ = _page_title(page)
        sheet = _sheet_number(page) or f"p{i+1}"
        data["sheets"][i] = sheet
        if obj_type:
            for mark, obj in _extract_schedule(page, obj_type).items():
                obj["sheet"] = sheet
                data["objects"][mark] = obj
        dims = _extract_dimensions(page)
        if dims:
            data["dims"][sheet] = dims
        notes = _extract_notes(page)
        if notes:
            data["notes"].update(notes)
    doc.close()
    return data


def diff(old, new):
    """Compare two read_set() structures. Returns a structured change report."""
    report = {"objects_added": [], "objects_removed": [], "objects_modified": [],
              "dims_changed": [], "notes_changed": []}

    o_obj, n_obj = old["objects"], new["objects"]
    for mark in sorted(set(n_obj) - set(o_obj)):
        report["objects_added"].append({
            "mark": mark, "type": n_obj[mark]["type"],
            "sheet": n_obj[mark]["sheet"], "attrs": n_obj[mark]["attrs"],
            "confidence": "high"})
    for mark in sorted(set(o_obj) - set(n_obj)):
        report["objects_removed"].append({
            "mark": mark, "type": o_obj[mark]["type"],
            "sheet": o_obj[mark]["sheet"], "attrs": o_obj[mark]["attrs"],
            "confidence": "high"})
    for mark in sorted(set(o_obj) & set(n_obj)):
        oa, na = o_obj[mark]["attrs"], n_obj[mark]["attrs"]
        changes = {}
        for key in sorted(set(oa) | set(na)):
            if oa.get(key, "") != na.get(key, ""):
                changes[key] = {"from": oa.get(key, "-"), "to": na.get(key, "-")}
        if changes:
            report["objects_modified"].append({
                "mark": mark, "type": n_obj[mark]["type"],
                "sheet": n_obj[mark]["sheet"], "changes": changes,
                "confidence": "high"})

    for sheet in sorted(set(old["dims"]) | set(new["dims"])):
        od, nd = old["dims"].get(sheet, set()), new["dims"].get(sheet, set())
        added, removed = nd - od, od - nd
        if added or removed:
            report["dims_changed"].append({
                "sheet": sheet, "added": sorted(added),
                "removed": sorted(removed), "confidence": "high"})

    o_notes, n_notes = old["notes"], new["notes"]
    for num in sorted(set(o_notes) | set(n_notes), key=lambda x: int(x)):
        if o_notes.get(num, "") != n_notes.get(num, ""):
            report["notes_changed"].append({
                "note": num, "from": o_notes.get(num, "(none)"),
                "to": n_notes.get(num, "(removed)"), "confidence": "high"})
    return report


def _fmt_attrs(attrs):
    return "; ".join(f"{k}={v}" for k, v in attrs.items())


def print_report(report, old_path, new_path):
    def head(s):
        print("\n" + s)
        print("-" * len(s))

    total = sum(len(v) for v in report.values())
    print(f"REVISION DIFF   {os.path.basename(old_path)}  ->  {os.path.basename(new_path)}")
    print(f"{total} change(s) detected. Every change read from the text layer (confidence: high).")

    if report["objects_added"]:
        head("OBJECTS ADDED")
        for o in report["objects_added"]:
            print(f"  + {o['mark']:6} {o['type']:8} [{o['sheet']}]  {_fmt_attrs(o['attrs'])}")
    if report["objects_removed"]:
        head("OBJECTS REMOVED")
        for o in report["objects_removed"]:
            print(f"  - {o['mark']:6} {o['type']:8} [{o['sheet']}]  {_fmt_attrs(o['attrs'])}")
    if report["objects_modified"]:
        head("OBJECTS MODIFIED")
        for o in report["objects_modified"]:
            print(f"  ~ {o['mark']:6} {o['type']:8} [{o['sheet']}]")
            for key, ch in o["changes"].items():
                print(f"        {key}: {ch['from']}  ->  {ch['to']}")
    if report["dims_changed"]:
        head("DIMENSIONS CHANGED")
        for d in report["dims_changed"]:
            for r in d["removed"]:
                print(f"  - [{d['sheet']}] {r}")
            for a in d["added"]:
                print(f"  + [{d['sheet']}] {a}")
    if report["notes_changed"]:
        head("GENERAL NOTES CHANGED")
        for n in report["notes_changed"]:
            print(f"  ~ note {n['note']}")
            print(f"        was: {n['from']}")
            print(f"        now: {n['to']}")
    if total == 0:
        print("\nNo textual changes found between the two revisions.")


def main():
    ap = argparse.ArgumentParser(description="Semantic revision diff for drawing sets.")
    ap.add_argument("old_pdf")
    ap.add_argument("new_pdf")
    ap.add_argument("--json", help="also write the full report to this JSON file")
    args = ap.parse_args()

    old = read_set(args.old_pdf)
    new = read_set(args.new_pdf)
    report = diff(old, new)
    print_report(report, args.old_pdf, args.new_pdf)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nFull report written to {args.json}")


if __name__ == "__main__":
    main()
