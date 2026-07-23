#!/usr/bin/env python3
"""
takeoff.py - Turn a construction PDF drawing set into a structured, queryable
database, so Claude (or anyone) reads a small answer instead of a huge PDF.

Pipeline:
    build   PDF  -> SQLite  (extract vector text, sheets, schedules, dimensions)
    check   run cross-checks / sanity flags on measurements
    query   ask the database questions cheaply (count, sheets, dims, flags, sql)

Design honesty:
  * Counting from schedules  -> HIGH   confidence (reading literal rows)
  * Counting from tag callouts-> MEDIUM confidence (pattern match on plan)
  * Dimensions written on the sheet -> HIGH confidence (reading a printed number)
  * Anything scaled from pixels     -> LOW  confidence + must pass a sanity check

Usage:
    py takeoff.py build  DRAWINGS.pdf  --db set.db
    py takeoff.py check  --db set.db
    py takeoff.py query  count           --db set.db
    py takeoff.py query  count --type door --db set.db
    py takeoff.py query  sheets          --db set.db
    py takeoff.py query  dims  --sheet A-101 --db set.db
    py takeoff.py query  flags           --db set.db
    py takeoff.py query  sql "SELECT type, COUNT(*) FROM objects GROUP BY type" --db set.db

Requires: PyMuPDF  (pip install pymupdf)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patterns as P  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema.sql")


# --------------------------------------------------------------------------- #
#  DB helpers
# --------------------------------------------------------------------------- #
def open_db(path, fresh=False):
    if fresh and os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    with open(SCHEMA, "r", encoding="utf-8") as fh:
        con.executescript(fh.read())
    return con


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


# --------------------------------------------------------------------------- #
#  BUILD
# --------------------------------------------------------------------------- #
def _guess_sheet_number(words, page_w, page_h):
    """Pick the sheet number token nearest the bottom-right title block."""
    best, best_score = None, -1.0
    for (x0, y0, x1, y1, txt, *_ ) in words:
        m = P.SHEET_NUMBER_RE.match(txt.strip())
        if not m:
            continue
        # score = closeness to bottom-right corner
        score = (x1 / page_w) + (y1 / page_h)
        if score > best_score:
            best_score, best = score, (txt.strip(), m.group(1))
    return best  # (sheet_number, disc_letters) or None


def _extract_dimensions(words, pair_radius=75.0):
    """Yield (raw_text, value_m) for dimension annotations on the sheet.

    Feet and inches are usually separate, spatially-adjacent tokens
    (12' next to 1/2"). We classify tokens, then pair each FEET token with its
    nearest INCH token within `pair_radius` points. Falls back to single-token
    and metric dimensions. Deduped by rounded position so a dim is counted once.
    """
    feet, inch, single = [], [], []
    for (x0, y0, x1, y1, txt, *_) in words:
        t = txt.strip()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # single self-contained token (12'-6") or metric (3600mm)
        val = P.parse_length_to_m(t)
        if val is not None and val >= 0.05:
            single.append((round(cx), round(cy), t, val))
            continue
        fm = P.FEET_RE.fullmatch(t)
        if fm:
            fv = float(fm.group(1))
            if fv <= P.MAX_PLAUSIBLE_FEET:
                feet.append((cx, cy, t, fv))
            continue
        iv = P.parse_inches(t)
        if iv is not None:
            inch.append((cx, cy, t, iv))

    seen = set()

    def emit(cx, cy, raw, val):
        key = (round(cx / 5), round(cy / 5), raw)
        if key in seen:
            return
        seen.add(key)
        return (raw, val)

    for cx, cy, raw, val in single:
        r = emit(cx, cy, raw, val)
        if r:
            yield r

    for fx, fy, ftxt, fv in feet:
        # nearest inch token within radius
        best, bestd = None, pair_radius
        for ix, iy, itxt, iv in inch:
            d = ((fx - ix) ** 2 + (fy - iy) ** 2) ** 0.5
            if d < bestd:
                bestd, best = d, (itxt, iv)
        if best:
            raw = f"{ftxt}-{best[0]}"
            val = P.feet_inches_to_m(fv, best[1])
        else:
            raw, val = ftxt, P.feet_inches_to_m(fv, 0.0)
        if val >= 0.3:  # drop sub-foot noise
            r = emit(fx, fy, raw, val)
            if r:
                yield r


def _page_has_schedule(text):
    """Cheap gate: does the page text even mention a schedule? Table detection
    (find_tables) is expensive, so we only run it on pages that plausibly hold a
    schedule instead of every sheet in a large-format set."""
    # A real schedule is titled "... SCHEDULE" (DOOR SCHEDULE, PANEL SCHEDULE).
    # That single word is a far more reliable — and cheaper — gate than the
    # broad keyword list, which appears on almost every sheet.
    return "schedule" in text.lower()


def _page_schedule_type(text):
    """Classify a schedule page by its title(s). find_tables often splits one
    schedule into many header-less fragments, so we can't rely on per-table
    headers - we decide the type once, from the sheet, then count data rows.
    Returns (obj_type, mixed?) or (None, False)."""
    up = re.sub(r"\s+", " ", text.upper())
    titles = re.findall(r"([A-Z][A-Z ./]{1,25}?)SCHEDULE", up)
    found = []
    for title in titles:
        for kw, typ in P.SCHEDULE_KEYWORDS.items():
            if kw.upper() in title and typ not in found:
                found.append(typ)
    if not found:
        return (None, False)
    # resolve to one type by priority; flag if genuinely several
    for typ in P.TYPE_PRIORITY:
        if typ in found:
            return (typ, len([t for t in found if t in P.TYPE_PRIORITY]) > 1)
    return (found[0], len(found) > 1)


def _is_data_row(cells):
    """A countable schedule entry: a non-empty first cell that isn't a header
    label, and enough filled cells to be a real row (not a stray fragment)."""
    nonempty = [c for c in cells if c]
    if len(nonempty) < 3:
        return False
    first = cells[0].strip()
    if not first or first.lower() in P.SCHEDULE_STOPWORDS or len(first) > 20:
        return False
    return True


def _find_headers(all_rows):
    """Map column-count -> header cell names. find_tables splits one schedule
    into fragments, but every fragment keeps the same column count, so we find
    the header once (an all-text row, no digit in the first cell) and reuse its
    column names to label the header-less data fragments."""
    headers = {}
    for cells in all_rows:
        ne = [c for c in cells if c]
        if len(ne) < 4:
            continue
        first = cells[0]
        if re.search(r"\d", first):      # data rows carry a digit in the mark
            continue
        headers.setdefault(len(cells),
                           [re.sub(r"\s+", " ", c).strip() or f"col{i}"
                            for i, c in enumerate(cells)])
    return headers


def _extract_schedules(page):
    """Return list of (obj_type, tag, attributes_json, note): one entry per
    schedule data row on the page, typed from the sheet title, with the row's
    other columns captured as attributes. Deduped by mark."""
    obj_type, mixed = _page_schedule_type(page.get_text("text"))
    if not obj_type:
        return []
    label = "mixed_schedule" if mixed else obj_type
    note = "schedule row" + (f" (page has multiple schedules; typed as {obj_type})"
                             if mixed else "")
    try:
        finder = page.find_tables()
    except Exception:
        return []

    all_rows = []
    for tbl in getattr(finder, "tables", []):
        try:
            for r in tbl.extract():
                all_rows.append([str(c).strip() if c else "" for c in r])
        except Exception:
            continue

    headers = _find_headers(all_rows)
    seen, out = set(), []
    for cells in all_rows:
        if not _is_data_row(cells):
            continue
        mark = cells[0][:40]
        if mark in seen:
            continue
        seen.add(mark)
        hdr = headers.get(len(cells))
        attrs = {}
        if hdr:
            for name, val in zip(hdr[1:], cells[1:]):  # skip the mark column
                v = re.sub(r"\s+", " ", val).strip()
                if v and name:
                    attrs[name] = v
        attrs_json = json.dumps(attrs, ensure_ascii=False) if attrs else None
        out.append((label, mark, attrs_json, note))
    return out


def _count_tags(words):
    """Return Counter of (type, tag) from tag-pattern callouts on the plan."""
    hits = Counter()
    for w in words:
        tok = w[4].strip()
        for typ, rx in P.TAG_PATTERNS.items():
            if rx.fullmatch(tok):
                hits[(typ, tok)] += 1
                break
    return hits


def cmd_build(args):
    import fitz  # imported here so `query` works without pymupdf

    if not os.path.exists(args.pdf):
        sys.exit(f"PDF not found: {args.pdf}")

    con = open_db(args.db, fresh=True)
    doc = fitz.open(args.pdf)
    set_meta(con, "source_pdf", os.path.abspath(args.pdf))
    set_meta(con, "page_count", doc.page_count)

    n_words = n_obj = n_dim = 0
    for pno in range(doc.page_count):
        if not args.quiet and pno % 10 == 0:
            print(f"  ...page {pno}/{doc.page_count}", file=sys.stderr, flush=True)
        page = doc[pno]
        pw, ph = page.rect.width, page.rect.height
        words = page.get_text("words")
        text = page.get_text("text")

        sn = _guess_sheet_number(words, pw, ph)
        sheet_number = sn[0] if sn else None
        disc = None
        if sn:
            disc = P.DISCIPLINE_MAP.get(sn[1]) or P.DISCIPLINE_MAP.get(sn[1][0])
        scale_label, scale_ratio = P.detect_scale(text)

        con.execute(
            "INSERT INTO sheets(page,sheet_number,title,discipline,scale_label,"
            "scale_ratio,width_pt,height_pt,word_count) VALUES(?,?,?,?,?,?,?,?,?)",
            (pno, sheet_number, None, disc, scale_label, scale_ratio,
             pw, ph, len(words)),
        )

        # store words (kept so the DB can answer ad-hoc text questions too)
        con.executemany(
            "INSERT INTO words(page,text,x0,y0,x1,y1) VALUES(?,?,?,?,?,?)",
            [(pno, w[4], w[0], w[1], w[2], w[3]) for w in words],
        )
        n_words += len(words)

        # tag callouts (MEDIUM confidence counts)
        for (typ, tag), cnt in _count_tags(words).items():
            con.execute(
                "INSERT INTO objects(type,tag,page,sheet_number,source_method,"
                "confidence,note) VALUES(?,?,?,?,?,?,?)",
                (typ, tag, pno, sheet_number, "tag_pattern", "medium",
                 f"x{cnt} on plan"),
            )
            n_obj += 1

        # dimension annotations (HIGH confidence measurements)
        for raw, val in _extract_dimensions(words):
            con.execute(
                "INSERT INTO measurements(description,raw_text,value_m,page,"
                "sheet_number,method,confidence,sanity_status) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (f"dimension on {sheet_number or 'p'+str(pno)}", raw, val, pno,
                 sheet_number, "annotation", "high", "unchecked"),
            )
            n_dim += 1

    con.commit()
    run_sanity(con)
    con.commit()
    con.close()
    md, _ = _write_summary(args.db)
    print(f"Built {args.db}")
    print(f"  pages:            {doc.page_count}")
    print(f"  words stored:     {n_words}")
    print(f"  objects (tags):   {n_obj}")
    print(f"  measurements:     {n_dim}")
    print(f"  index written:    {md}")
    print(f"  schedule counts:  add high-confidence counts with:")
    print(f"      py takeoff.py schedules \"{os.path.basename(args.pdf)}\" "
          f"--db {args.db} --auto")


# --------------------------------------------------------------------------- #
#  SCHEDULES  (opt-in high-confidence count pass)
# --------------------------------------------------------------------------- #
def cmd_schedules(args):
    """Second pass: run table detection on schedule sheets and store each row as
    a high-confidence object. Table detection is slow on large-format sheets
    (~30s/page), so we only touch the pages you point at."""
    import fitz

    con = sqlite3.connect(args.db)
    doc = fitz.open(args.pdf)

    if args.pages:
        pages = [int(x) for x in args.pages.split(",") if x.strip() != ""]
    else:  # --auto: pages whose text is titled with a schedule
        pages = [i for i in range(doc.page_count)
                 if _page_has_schedule(doc[i].get_text("text"))]

    print(f"Scanning {len(pages)} page(s) for schedules "
          f"(~30s each on large sheets): {pages}", file=sys.stderr, flush=True)

    # clear any previous schedule objects so re-runs don't double count
    con.execute("DELETE FROM objects WHERE source_method='schedule'")
    added = 0
    for pno in pages:
        if pno < 0 or pno >= doc.page_count:
            continue
        page = doc[pno]
        sn = _guess_sheet_number(page.get_text("words"), page.rect.width, page.rect.height)
        sheet_number = sn[0] if sn else None
        rows = _extract_schedules(page)
        for typ, tag, attrs_json, note in rows:
            con.execute(
                "INSERT INTO objects(type,tag,page,sheet_number,source_method,"
                "confidence,attributes,note) VALUES(?,?,?,?,?,?,?,?)",
                (typ, tag, pno, sheet_number, "schedule", "high", attrs_json, note),
            )
            added += 1
        print(f"  page {pno} ({sheet_number or '-'}): {len(rows)} rows",
              file=sys.stderr, flush=True)
    con.commit()
    con.close()
    md, _ = _write_summary(args.db)
    print(f"Added {added} high-confidence schedule objects to {args.db}")
    print(f"Refreshed index: {md}")


# --------------------------------------------------------------------------- #
#  CHECK  (sanity / cross-checks)
# --------------------------------------------------------------------------- #
def run_sanity(con, tol=1.5):
    """
    Establish the building envelope from HIGH-confidence annotation dimensions,
    then check every measurement against it. A length longer than ~1.5x the
    building envelope is almost certainly a bad read (a scaled measurement gone
    wrong, or a glued reference number) -> flag it. Non-positive -> fail.

    The reference uses the 98th percentile of annotation lengths rather than the
    raw max, so a single stray value can't inflate the envelope and hide the
    others.
    """
    anns = [r[0] for r in con.execute(
        "SELECT value_m FROM measurements "
        "WHERE method='annotation' AND confidence='high' AND value_m>0 "
        "ORDER BY value_m")]
    if anns:
        idx = min(len(anns) - 1, int(round(0.98 * (len(anns) - 1))))
        envelope = anns[idx]
    else:
        envelope = con.execute(
            "SELECT MAX(value_m) FROM measurements").fetchone()[0] or 0.0
    set_meta(con, "building_envelope_m", round(envelope, 3))

    limit = envelope * tol
    updates = []
    for mid, val in con.execute("SELECT id, value_m FROM measurements"):
        if val is None or val <= 0:
            updates.append(("fail", "non-positive length", mid))
        elif envelope and val > limit:
            updates.append(("warn",
                            f"{val:.1f}m > 1.5x building envelope {envelope:.1f}m",
                            mid))
        else:
            updates.append(("ok", f"within envelope ~{envelope:.1f}m", mid))
    con.executemany(
        "UPDATE measurements SET sanity_status=?, sanity_note=? WHERE id=?",
        updates,
    )


def _write_summary(db, out=None):
    """Generate drawing.md - a one-page index that sits on top of the database.
    Read this first: it says which sheet holds what, so a query goes straight to
    the right place instead of scanning the whole set."""
    import datetime
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    n_meas = con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    n_flag = con.execute(
        "SELECT COUNT(*) FROM measurements WHERE sanity_status IN ('warn','fail')"
    ).fetchone()[0]

    # per-sheet contents
    obj_by_page = {}
    for r in con.execute(
        "SELECT page, type, COUNT(*) n FROM objects GROUP BY page, type"):
        obj_by_page.setdefault(r["page"], []).append(f"{r['n']} {r['type']}")
    dims_by_page = dict(con.execute(
        "SELECT page, COUNT(*) FROM measurements GROUP BY page").fetchall())

    L = []
    L.append(f"# Drawing Set Index - {os.path.basename(meta.get('source_pdf',''))}")
    L.append("")
    L.append("_Read this first. It tells you which sheet holds what. Query the "
             "database for exact numbers; only open the original PDF to "
             "double-check a visual detail._")
    L.append("")
    L.append(f"- **Source PDF:** `{meta.get('source_pdf','?')}`")
    L.append(f"- **Pages:** {meta.get('page_count','?')}")
    L.append(f"- **Indexed:** {datetime.date.today().isoformat()}")
    if meta.get("building_envelope_m"):
        L.append(f"- **Building envelope (from dimensions):** "
                 f"~{meta['building_envelope_m']} m  _(used for sanity checks)_")
    L.append(f"- **Dimensions captured:** {n_meas}"
             + (f"  -- **{n_flag} FLAGGED** (see `query flags`)" if n_flag else ""))
    L.append("")

    L.append("## Object totals")
    L.append("")
    L.append("| type | total | high conf | medium conf |")
    L.append("|---|---:|---:|---:|")
    for r in con.execute(
        "SELECT type, COUNT(*) n, SUM(confidence='high') hi, "
        "SUM(confidence='medium') med FROM objects GROUP BY type ORDER BY n DESC"):
        L.append(f"| {r['type']} | {r['n']} | {r['hi'] or 0} | {r['med'] or 0} |")
    L.append("")

    L.append("## Sheets")
    L.append("")
    L.append("| pg | sheet | discipline | scale | on this sheet |")
    L.append("|---:|---|---|---|---|")
    for s in con.execute(
        "SELECT page, sheet_number, discipline, scale_label FROM sheets "
        "ORDER BY page"):
        bits = list(obj_by_page.get(s["page"], []))
        if dims_by_page.get(s["page"]):
            bits.append(f"{dims_by_page[s['page']]} dims")
        L.append(f"| {s['page']} | {s['sheet_number'] or '-'} | "
                 f"{s['discipline'] or '-'} | {s['scale_label'] or '-'} | "
                 f"{', '.join(bits) or '-'} |")
    L.append("")
    L.append("---")
    L.append("_Generated by drawing-takeoff. Counts from schedules are high "
             "confidence; tag-callout counts are medium; scaled measurements are "
             "low and sanity-checked against the building envelope._")

    text = "\n".join(L) + "\n"
    if not out:
        out = os.path.join(os.path.dirname(os.path.abspath(db)) or ".",
                           "drawing.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    con.close()
    return out, len(text)


def cmd_summary(args):
    out, n = _write_summary(args.db, args.out)
    print(f"Wrote {out}  ({n} bytes)")


def cmd_check(args):
    con = sqlite3.connect(args.db)
    run_sanity(con)
    con.commit()
    counts = dict(
        con.execute(
            "SELECT sanity_status, COUNT(*) FROM measurements GROUP BY sanity_status"
        ).fetchall()
    )
    con.close()
    print("Sanity check complete:", json.dumps(counts))


# --------------------------------------------------------------------------- #
#  QUERY  (the cheap layer)
# --------------------------------------------------------------------------- #
def cmd_query(args):
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    sub = args.subquery

    if sub == "count":
        if args.type:
            rows = con.execute(
                "SELECT confidence, COUNT(*) n, GROUP_CONCAT(DISTINCT source_method) src "
                "FROM objects WHERE type=? GROUP BY confidence", (args.type,)
            ).fetchall()
            total = sum(r["n"] for r in rows)
            print(f"{args.type}: {total} total")
            for r in rows:
                print(f"  {r['n']:>4}  {r['confidence']:<7} ({r['src']})")
        else:
            rows = con.execute(
                "SELECT type, COUNT(*) n, "
                "SUM(confidence='high') hi, SUM(confidence='medium') med "
                "FROM objects GROUP BY type ORDER BY n DESC"
            ).fetchall()
            print(f"{'type':<12}{'total':>7}{'high':>7}{'medium':>7}")
            for r in rows:
                print(f"{r['type']:<12}{r['n']:>7}{r['hi'] or 0:>7}{r['med'] or 0:>7}")

    elif sub == "list":
        if not args.type:
            sys.exit("list requires --type (e.g. --type door)")
        q = ("SELECT tag, sheet_number, confidence, attributes FROM objects "
             "WHERE type=?")
        p = [args.type]
        if args.sheet:
            q += " AND sheet_number=?"
            p.append(args.sheet)
        q += " ORDER BY tag LIMIT ?"
        p.append(args.limit)
        rows = con.execute(q, p).fetchall()
        for r in rows:
            attrs = ""
            if r["attributes"]:
                d = json.loads(r["attributes"])
                # show the most useful few columns compactly
                attrs = "  " + "; ".join(f"{k}={v}" for k, v in list(d.items())[:6])
            print(f"{r['tag']:<12} [{r['sheet_number'] or '-'}] "
                  f"{r['confidence']}{attrs}")

    elif sub == "sheets":
        rows = con.execute(
            "SELECT page, sheet_number, discipline, scale_label, word_count "
            "FROM sheets ORDER BY page"
        ).fetchall()
        print(f"{'pg':>3} {'sheet':<10}{'discipline':<16}{'scale':<14}{'words':>6}")
        for r in rows:
            print(f"{r['page']:>3} {r['sheet_number'] or '-':<10}"
                  f"{r['discipline'] or '-':<16}{r['scale_label'] or '-':<14}"
                  f"{r['word_count']:>6}")

    elif sub == "dims":
        q = ("SELECT sheet_number, raw_text, value_m, confidence, sanity_status "
             "FROM measurements")
        p = ()
        if args.sheet:
            q += " WHERE sheet_number=?"
            p = (args.sheet,)
        q += " ORDER BY value_m DESC LIMIT ?"
        p = p + (args.limit,)
        rows = con.execute(q, p).fetchall()
        print(f"{'sheet':<10}{'raw':<14}{'metres':>9}  {'conf':<7}{'sanity'}")
        for r in rows:
            print(f"{r['sheet_number'] or '-':<10}{r['raw_text']:<14}"
                  f"{r['value_m']:>9.2f}  {r['confidence']:<7}{r['sanity_status']}")

    elif sub == "flags":
        rows = con.execute(
            "SELECT sheet_number, raw_text, value_m, sanity_status, sanity_note "
            "FROM measurements WHERE sanity_status IN ('warn','fail') "
            "ORDER BY value_m DESC"
        ).fetchall()
        if not rows:
            print("No measurement flags. (Nothing exceeds building extent.)")
        for r in rows:
            print(f"[{r['sanity_status'].upper()}] {r['sheet_number'] or '-'} "
                  f"{r['raw_text']} = {r['value_m']:.2f}m  -> {r['sanity_note']}")
        low = con.execute(
            "SELECT type, COUNT(*) n FROM objects WHERE confidence!='high' "
            "GROUP BY type"
        ).fetchall()
        if low:
            print("\nLower-confidence object counts (verify before bidding):")
            for r in low:
                print(f"  {r['type']}: {r['n']}")

    elif sub == "sql":
        if not args.sqltext:
            sys.exit("provide a SQL string")
        if not args.sqltext.lstrip().lower().startswith("select"):
            sys.exit("only SELECT queries are allowed here")
        cur = con.execute(args.sqltext)
        cols = [d[0] for d in cur.description]
        print(" | ".join(cols))
        for row in cur.fetchall():
            print(" | ".join(str(row[c]) for c in cols))

    con.close()


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("build", help="build the database from a PDF")
    b.add_argument("pdf")
    b.add_argument("--db", default="drawings.db")
    b.add_argument("--quiet", action="store_true", help="suppress per-page progress")
    b.set_defaults(func=cmd_build)

    s = sp.add_parser("schedules", help="opt-in high-confidence schedule count pass")
    s.add_argument("pdf")
    s.add_argument("--db", default="drawings.db")
    s.add_argument("--pages", help="comma list of 0-based page numbers to scan")
    s.add_argument("--auto", action="store_true",
                   help="scan all pages whose text mentions a schedule")
    s.set_defaults(func=cmd_schedules)

    c = sp.add_parser("check", help="re-run sanity cross-checks")
    c.add_argument("--db", default="drawings.db")
    c.set_defaults(func=cmd_check)

    m = sp.add_parser("summary", help="write drawing.md (one-page set index)")
    m.add_argument("--db", default="drawings.db")
    m.add_argument("--out", help="output path (default: drawing.md next to the db)")
    m.set_defaults(func=cmd_summary)

    q = sp.add_parser("query", help="query the database")
    q.add_argument("subquery",
                   choices=["count", "list", "sheets", "dims", "flags", "sql"])
    q.add_argument("sqltext", nargs="?", help="SQL string for the 'sql' subcommand")
    q.add_argument("--db", default="drawings.db")
    q.add_argument("--type", help="filter objects by type (for count)")
    q.add_argument("--sheet", help="filter by sheet number (for dims)")
    q.add_argument("--limit", type=int, default=25)
    q.set_defaults(func=cmd_query)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
