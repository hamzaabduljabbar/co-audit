"""Audit a construction change-order proposal for pricing abuse (owner / PM side).

A change order arrives as a line-item breakdown: material qty x unit price,
labor hours x rate, equipment, subcontractor cost, then overhead & profit
markup on top, then bond & insurance. Owners' project managers audit every
line by hand against the contract's rate schedule and its markup caps - one to
three hours per CO, dozens of COs per job - to catch four things:

  1. MARKUP / O&P CAP ABUSE   - markup over the contract cap, and markup
     stacking (sub adds 15%, GC adds 15% on top of the marked-up subtotal, so
     the owner really pays ~32%).                                [implemented]
  2. UNIT-PRICE INFLATION     - a CO unit price higher than the rate the
     contractor already agreed to in the contract's rate schedule. [implemented]
  3. LABOR-HOUR REASONABLENESS- hours that do not match the scope. [flag only]
  4. MATERIAL-QUANTITY TIE-OUT- quantities that do not tie to the drawing.
                                                                  [flag only]

This tool does checks 1 and 2 deterministically - they are arithmetic against a
stated cap or a contracted rate, so the finding is a defensible dollar figure
with a page anchor, not an opinion. Checks 3 and 4 are judgement calls: the tool
surfaces the lines a human must eyeball but never rules on them, because a wrong
"this is padded" is worse than no finding for this high-trust persona.

    usage:  py work/co_audit.py <change-order.pdf> [contract-or-rate-schedule.pdf]

The second argument is optional. Markup caps are read, in order of authority:
  (a) from the contract / rate schedule PDF if one is supplied and states them;
  (b) from the caps printed on the change-order form itself (many agency PCO
      forms print "not to exceed ten percent (10%)" right on the ladder);
  (c) from inputs/co_caps.txt if present, else built-in industry defaults.
Every finding names which of these its cap came from, because "your own
contract says 5%, they billed 15%" is a different conversation than "vs the
industry norm".
"""
import re, sys, os, glob, subprocess

# -----------------------------------------------------------------------------
# built-in fallback caps (industry-typical; overridden by contract or config)
DEFAULT_CAPS = {
    "self":     10.0,   # a GC's markup on its OWN (self-performed) work
    "sub_op":   10.0,   # a subcontractor's OWN overhead & profit on its work
    "sub":       5.0,   # the markup a GC adds ON TOP of subcontractor work
                        #   - this is the anti-stacking cap
    "combined": 15.0,   # ceiling on total stacked O&P through all tiers
    "bond":      1.5,   # bond & insurance
}
# words -> number, so "ten percent (10%)" and a bare "10%" both resolve
WORD_PCT = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
            "eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,
            "fourteen":14,"fifteen":15,"twenty":20,"twenty-five":25,"thirty":30}
ROUND_TOL = 0.02        # $ rounding slack before an arithmetic mismatch is real
PCT_TOL   = 0.05        # percentage-point slack before a cap breach is real


# -----------------------------------------------------------------------------
# input handling  (mirrors the RFP tools: accept a PDF or a pre-extracted .txt)
SEARCH_ROOTS = [".", "inputs", "work/rfp", "co_audit_docs",
                os.path.expanduser("~/Downloads"),
                os.path.expanduser("~/Downloads/co_audit_docs")]


def find_pdf(stem):
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        for depth in ("", "*/", "*/*/"):
            for ext in ("pdf", "PDF"):
                hit = glob.glob(os.path.join(root, depth, f"{stem}.{ext}"))
                if hit:
                    return hit[0]
    return None


def scanned_pages(pages):
    """1-based page numbers that carry no usable text layer (a scan/image)."""
    return [i for i, p in enumerate(pages, 1)
            if len(re.findall(r"[A-Za-z0-9]", p or "")) < 20]


def scan_dir_for(path):
    """Sidecar dir next to the source holding rendered page images and their
    transcriptions: <name>.scan/pNN.png (rendered) + <name>.scan/pNN.txt (text)."""
    return os.path.splitext(path)[0] + ".scan"


def render_scan_pages(pdf, page_nums, sdir):
    """Render each scanned page to a PNG in sdir so Claude can read it natively
    (no OCR, no API - inside Claude Code the model transcribes the image). Returns
    the [(page, png)] actually written."""
    if not pdf or not os.path.exists(pdf):
        return []
    os.makedirs(sdir, exist_ok=True)
    out = []
    for i in page_nums:
        stem = os.path.join(sdir, f"p{i:02d}")
        if not os.path.exists(stem + ".png"):
            subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile",
                            "-f", str(i), "-l", str(i), pdf, stem],
                           capture_output=True)
        if os.path.exists(stem + ".png"):
            out.append((i, stem + ".png"))
    return out


def load(path, cache="work/rfp", ocr_scanned=False):
    """Return (page_list, pdf_path, ocr_pages). Pages are split on the form-feed
    pdftotext writes between pages, so every string keeps a page number for
    anchoring a finding.

    LIMITATION: this tool expects a TEXT / VECTOR PDF (one you can select text
    in), not a flat scan. A cost breakdown is arithmetic, and the tool reads the
    exact numbers off the text layer. A scanned image has no text layer, so it
    would have to be OCR'd - and OCR misreads digits ($1,850 -> $1,85O), which is
    exactly the kind of silent error a pricing audit must not introduce. Scanned
    COs are therefore out of scope: run them through OCR-to-text first and review
    the result, or ask the contractor for the native PDF.

    (An OCR fallback exists behind ocr_scanned=True, kept off by default: it uses
    the project's ocr.py and flags every OCR'd page LOW confidence. It is a
    deliberate opt-in, not part of the normal path.)"""
    if path.lower().endswith(".pdf"):
        os.makedirs(cache, exist_ok=True)
        txt = os.path.join(cache, os.path.splitext(os.path.basename(path))[0] + ".txt")
        subprocess.run(["pdftotext", "-layout", path, txt], capture_output=True)
        pdf = path
    else:
        txt = path
        pdf = find_pdf(os.path.splitext(os.path.basename(path))[0])
    if not os.path.exists(txt):
        return None, pdf, set()
    pages = open(txt, encoding="utf-8", errors="replace").read().split("\f")

    # Scanned pages carry no text layer. This tool does NOT OCR them and does NOT
    # call any model itself. Inside Claude Code, Claude reads the rendered page
    # image natively (the same way a user pastes a screenshot) and writes its
    # transcription to a sidecar file; we merge those here. Every merged page is
    # returned as "scan-derived" so the audit can flag its findings for human
    # verification - a number read from a picture must never be laundered into
    # the same certainty as a vector-text number.
    scan_pages = set()
    sdir = scan_dir_for(path)
    if os.path.isdir(sdir):
        for i in scanned_pages(pages):        # 1-based
            t = os.path.join(sdir, f"p{i:02d}.txt")
            if os.path.exists(t):
                text = open(t, encoding="utf-8", errors="replace").read()
                if len(re.findall(r"[A-Za-z0-9]", text)) >= 20:
                    pages[i - 1] = text
                    scan_pages.add(i)
    return pages, pdf, scan_pages


# -----------------------------------------------------------------------------
# number / percent parsing
def money(s):
    """'$9,243.75' -> 9243.75 ; returns None if there is no money token."""
    m = re.search(r"\$?\s*(-?[\d,]+\.\d\d)\b", s)
    return float(m.group(1).replace(",", "")) if m else None


def all_money(s):
    return [float(x.replace(",", "")) for x in re.findall(r"-?[\d,]+\.\d\d", s)]


def pct_in(s):
    """Pull a percentage from a cap phrase. Prefer the parenthesised numeral
    '(10%)', fall back to a spelled 'ten percent', so both forms resolve to
    10.0 even when the form writes them together."""
    m = re.search(r"\(?\s*(\d{1,2}(?:\.\d+)?)\s*%\)?", s)
    if m:
        return float(m.group(1))
    for word, val in sorted(WORD_PCT.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{word}\b(?:\s+and\s+a\s+half)?\s+percent", s, re.I):
            half = re.search(rf"\b{word}\b\s+and\s+a\s+half", s, re.I)
            return val + (0.5 if half else 0)
    return None


# -----------------------------------------------------------------------------
# CHECK 1 : the markup / O&P cost ladder
#
# Agency PCO forms lay the price out as a lettered ladder:
#     (d) Subtotal .................. base direct cost
#     (e) O&P for sub, <= 10% of (d)
#     (f) Subtotal .................. (d)+(e)
#     (g) O&P for GC,  <=  5% of (f)
#     (h) Subtotal .................. (f)+(g)
#     (i) Bond & Insurance <= 1.5% of (h)
#     (j) TOTAL
# pdftotext reflow puts the dollar amount on the marker line and wraps the cap
# phrase ("exceed five percent (5%) of Item (f)") onto the next line, so each
# rung is parsed as the marker line plus its continuation up to the next marker.
MARK_HDR = re.compile(r"\(([a-z])\)\s*(.*)")
CAP_REF  = re.compile(r"of\s+Item\s+\(([a-z])\)", re.I)


def parse_ladders(pages):
    """Return a list of ladders, one per cost block found. Each ladder is a
    list of rung dicts: {letter,label,amount,cap_pct,base_ref,kind,page}."""
    ladders, cur = [], []
    for pno, page in enumerate(pages, 1):
        lines = page.splitlines()
        i = 0
        while i < len(lines):
            m = MARK_HDR.match(lines[i].strip())
            if not m:
                i += 1
                continue
            letter, rest = m.group(1), m.group(2)
            block = [rest]
            j = i + 1
            while j < len(lines) and not MARK_HDR.match(lines[j].strip()):
                block.append(lines[j].strip())
                j += 1
            text = " ".join(b for b in block if b)
            amt = money(rest) if money(rest) is not None else money(text)
            rung = {
                "letter": letter, "label": re.sub(r"\s+", " ", text)[:80],
                "amount": amt, "cap_pct": pct_in(text),
                "base_ref": (CAP_REF.search(text).group(1).lower()
                             if CAP_REF.search(text) else None),
                "kind": classify(text), "page": pno,
            }
            # a ladder resets when we hit a fresh (a) or (d) header row after
            # having already collected a TOTAL rung
            if letter in ("a", "d") and any(r["kind"] == "total" for r in cur):
                if cur:
                    ladders.append(cur)
                cur = []
            cur.append(rung)
            i = j
    if cur:
        ladders.append(cur)
    return [L for L in ladders if any(r["kind"] in ("markup", "bond", "total")
                                      for r in L)]


def classify(text):
    t = text.lower()
    # a calendar-days / time-extension row is not a cost rung; forms often
    # letter it '(i) Time ...' right after the money ladder
    if t.startswith("time") or "calendar" in t and money(t) is None:
        return "time"
    if "contingency" in t:
        return "contingency"
    if "bond" in t or "insurance" in t:
        return "bond"
    if ("overhead" in t or "profit" in t or "o&p" in t or "o & p" in t
            or "markup" in t or "mark-up" in t or "m/u" in t):
        return "markup"
    if re.match(r"subtotal\b", t):
        return "subtotal"
    if re.match(r"total\b", t):
        return "total"
    return "cost"


def tier_of(label, stacked=False):
    """Which cap governs this markup rung.

    Label wording alone is ambiguous: 'Overhead and Profit for Contractor'
    means the self cap on a first-tier rung but the GC-on-sub cap when it sits
    on top of a markup that already ran (`stacked`). Position disambiguates.
      - names a subcontractor tier      -> sub_op (the sub's own O&P)
      - 'for Contractor', already stacked-> sub    (GC markup on sub work)
      - 'for Contractor', first tier     -> self   (GC's own work)
    """
    t = label.lower()
    if "sub" in t and "contractor" in t:      # 'for any and all tiers of Subcontractor'
        return "sub_op"
    if stacked:                               # a markup already ran below this one
        return "sub"
    return "self"


def audit_markups(ladder, caps, cap_src):
    """Recompute every markup and bond rung against the rung it marks up.
    Returns (findings, effective_stack_pct or None)."""
    findings = []
    by_letter = {r["letter"]: r for r in ladder}
    # the true base a markup chain sits on is the subtotal the FIRST markup
    # references - not merely the first number on the form, which on a combined
    # or backup-laden ladder can be a stray estimate-sheet subtotal
    first_markup = next((r for r in ladder if r["kind"] == "markup"), None)
    base_cost = (by_letter.get(first_markup["base_ref"], {}).get("amount")
                 if first_markup else None)
    markup_sum = 0.0
    stacked = False     # has a markup already been applied lower in this ladder

    for r in ladder:
        if r["kind"] not in ("markup", "bond", "contingency"):
            continue

        if r["kind"] == "contingency":
            findings.append(("VIOLATION", r["page"],
                f"Contingency line item '{r['label']}' ({fmt(r['amount'])}) - "
                f"most contract change-order clauses forbid a separate "
                f"contingency line; verify it is allowed."))
            continue

        base = by_letter.get(r["base_ref"], {}).get("amount")
        # A rung's effective cap is the STRICTER of what the form prints and
        # what the governing caps (contract / config / default) allow. A
        # contractor cannot escape a tighter contract cap by printing a looser
        # number on its own PCO form; and if the form prints a tighter number
        # than the contract, hold them to their own form. min() gives both.
        tier = "bond" if r["kind"] == "bond" else tier_of(r["label"], stacked)
        if r["kind"] == "markup":
            stacked = True      # rungs above this one are now stacked markups
        form_pct = r["cap_pct"]
        gov_pct = caps[tier]
        candidates = [(v, s) for v, s in
                      ((form_pct, "stated on the CO form"),
                       (gov_pct, cap_src.get(tier, "")))
                      if v is not None]
        cap, cap_note = min(candidates, key=lambda vs: vs[0])

        # a markup rung with a printed cap but no dollar amount means the
        # contractor left it blank - i.e. did not charge it. That is not a
        # parse failure and not a violation; note it and move on.
        if r["amount"] is None:
            findings.append(("OK", r["page"],
                f"Rung ({r['letter']}) {r['label']}: left blank - "
                f"not charged."))
            continue
        if base is None:
            findings.append(("REVIEW", r["page"],
                f"Rung ({r['letter']}) '{r['label']}': billed {fmt(r['amount'])} "
                f"but its base subtotal could not be read - verify by hand."))
            continue

        expected = round(base * cap / 100, 2)
        actual = r["amount"]
        eff = actual / base * 100 if base else 0
        if r["kind"] == "markup":
            markup_sum += actual
        note = f"cap {cap:g}% ({cap_note})"

        if eff > cap + PCT_TOL:
            findings.append(("VIOLATION", r["page"],
                f"Rung ({r['letter']}) {r['label']}: billed {fmt(actual)} = "
                f"{eff:.2f}% of {fmt(base)} (Item {r['base_ref']}), over the "
                f"{note}. Expected <= {fmt(expected)}. "
                f"OVERCHARGE {fmt(actual - expected)}."))
        elif abs(actual - expected) > ROUND_TOL and eff < cap - PCT_TOL:
            findings.append(("OK", r["page"],
                f"Rung ({r['letter']}) {r['label']}: {fmt(actual)} = {eff:.2f}% "
                f"of Item {r['base_ref']}, under the {note}."))
        else:
            findings.append(("OK", r["page"],
                f"Rung ({r['letter']}) {r['label']}: {fmt(actual)} = {eff:.2f}% "
                f"of Item {r['base_ref']}, at the {note}."))

    # stacking: total O&P dollars charged, as a percentage of the base direct
    # cost. Summing the markup rungs (rather than differencing subtotals) is
    # immune to extra direct-cost lines inserted mid-ladder on combined COs,
    # and it is exactly the number that exposes tier stacking: a 10% sub markup
    # plus a 5% GC markup on the marked-up subtotal reads as ~15.5% here.
    stack = (markup_sum / base_cost * 100) if base_cost and markup_sum else None
    n_markups = sum(1 for r in ladder if r["kind"] == "markup" and r["amount"])
    return findings, stack, base_cost, n_markups


# -----------------------------------------------------------------------------
# CHECK 2 : unit price vs the contracted rate schedule
#
# Match each CO line ('3000 PSI concrete  12 CY  $185.00') to a line in the
# contract's rate schedule and flag any CO unit price above the agreed rate.
# The rate schedule text is turned into {normalised description -> unit rate}.
UNIT = r"(?:EA|LF|SF|SY|CY|CF|LS|HR|TON|GAL|LB|MBF|each|hour)"
RATE_LINE = re.compile(
    rf"^(?P<desc>.+?)\s+(?P<qty>[\d,]+(?:\.\d+)?)\s*(?P<unit>{UNIT})\b"
    rf".*?\$?\s*(?P<rate>[\d,]+\.\d\d)", re.I)
STOP = set("the a an of for and to per with each unit price rate item no "
           "material labor total add".split())

# Civil / DOT change orders list pay items as a numbered row:
#   '97 AGGREGATE BASE CLASS 5      90  $45.00'
# The unit of measure often reflows onto a neighbouring line (pdftotext puts a
# tall AMOUNT cell out of row), so RATE_LINE - which requires a unit token on the
# same line - misses it. This fallback needs only the item number, description,
# quantity and $rate, which do sit together, and matches on description (the unit
# is recovered from the contract item it matches). Anchored on a leading 1-3
# digit item number and a $rate with cents at end of line, it will not fire on
# prose. Lump-sum rows still parse but never match a unit-priced contract item.
CO_ITEM = re.compile(
    r"^\s*\d{1,3}\s+(?P<desc>[A-Za-z][^$]*?)\s+(?P<qty>[\d,]+(?:\.\d+)?)\s+"
    r"\$\s*(?P<rate>[\d,]+\.\d\d)\s*$")


def best_contract_match(desc, rates):
    """Match a CO line description to a contract rate by containment - what
    fraction of the CO line's tokens appear in the contract description - so a CO
    'AGGREGATE BASE CLASS 5' ties to a contract 'Aggregate Base, CL 5 (CV)' that
    plain Jaccard overlap would score 0.5 and miss. Among equally-contained
    entries prefer the closest (fewest extra tokens). Returns (info, score)."""
    nd = set(norm_desc(desc).split())
    if not nd:
        return None, 0.0
    best, score, extra = None, 0.0, 1e9
    for rd, info in rates.items():
        rdset = set(rd.split())
        inter = nd & rdset
        if not inter:
            continue
        c = len(inter) / len(nd)
        x = len(rdset - nd)
        if c > score or (c == score and x < extra):
            best, score, extra = info, c, x
    return best, score


def norm_desc(s):
    toks = [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP]
    return " ".join(toks)


def parse_rate_schedule(pages):
    """Material / equipment unit rates. Hourly (labor) rates are excluded here
    and handled by check 3, so a labor-rate overcharge is never double-counted
    across checks 2 and 3."""
    rates = {}
    for pno, page in enumerate(pages, 1):
        for line in page.splitlines():
            m = RATE_LINE.match(line.strip())
            if not m or m.group("unit").upper() in ("HR", "HOUR"):
                continue
            d = norm_desc(m.group("desc"))
            if len(d) < 4:
                continue
            rates[d] = {"rate": float(m.group("rate").replace(",", "")),
                        "unit": m.group("unit").upper(), "page": pno,
                        "raw": re.sub(r"\s+", " ", m.group("desc")).strip()[:60]}
    return rates


def overlap(a, b):
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / max(1, len(sa | sb))


def audit_unit_prices(co_pages, rates):
    """Flag CO unit prices that exceed the matched contract rate."""
    findings = []
    if not rates:
        return findings
    for pno, page in enumerate(co_pages, 1):
        for line in page.splitlines():
            s = line.strip()
            m = RATE_LINE.match(s)
            if m and m.group("unit").upper() not in ("HR", "HOUR"):
                desc = m.group("desc")           # unit is on the line
            elif m:
                continue                         # labor rate -> check 3's job
            else:
                m = CO_ITEM.match(s)             # numbered pay-item fallback
                if not m:
                    continue
                desc = m.group("desc")
            co_rate = float(m.group("rate").replace(",", ""))
            qty = float(m.group("qty").replace(",", ""))
            best, score = best_contract_match(desc, rates)
            if best and score >= 0.6:
                shown = re.sub(r"\s+", " ", desc).strip()[:50]
                if co_rate > best["rate"] * 1.001:
                    delta = (co_rate - best["rate"]) * qty
                    findings.append(("VIOLATION", pno,
                        f"'{shown}' "
                        f"billed {fmt(co_rate)}/{best['unit']} vs contract rate "
                        f"{fmt(best['rate'])} (p{best['page']}, match {score:.0%}). "
                        f"x {qty:g} = OVERCHARGE {fmt(delta)}."))
                else:
                    findings.append(("OK", pno,
                        f"'{best['raw']}' {fmt(co_rate)} <= contract "
                        f"{fmt(best['rate'])}/{best['unit']}."))
    return findings


# -----------------------------------------------------------------------------
# CHECK 3 : labor-hour reasonableness
#
# Reasonableness is a judgement call - 42 hours for 4 CY of formwork may be
# right on a tight retrofit and padded on open ground - so the tool never
# *rules* a line padded. What it CAN do deterministically, and what a PM
# actually burns time on, is:
#   (a) recompute hours x rate and flag any extension that does not tie out
#       (a padded extension hidden behind correct-looking inputs);
#   (b) compare the billed labor rate to the contract's labor rate schedule and
#       flag rates above it (rate inflation - same engine as check 2, HR unit);
#   (c) surface the totals - hours, blended rate, labor as a share of direct
#       cost - so the human eyeball lands where it matters.
LABOR_LINE = re.compile(
    r"^(?P<desc>.+?)\s+(?P<hrs>\d+(?:\.\d+)?)\s*(?:HR|HRS|HOURS|MH)\b"
    r".*?\$?\s*(?P<rate>[\d,]+(?:\.\d\d)?)\s*(?:/\s*(?:HR|HOUR))?"
    r"(?:.*?\$?\s*(?P<ext>[\d,]+\.\d\d))?\s*$", re.I)

# Many real COs write each cost line inline as "TAG = qty Unit Desc x $rate/Unit
# = $ext", e.g. "L = 6 Man Hours x $108.50/Hr = $651.00" or "M = 246 Sft of EPDM
# x $1.32/Sft = $324.72", including a rate-first variant "@ $5.76/Lft x 164 Lft"
# and deducts in angle brackets "<$217.00>". TAG L=labor, M=material, E=equip.
INLINE_TAG = re.compile(r"^\s*(?P<tag>[LME])\s*=\s*(?P<body>.+)$")
INLINE_QR  = re.compile(  # qty ... unit ... x ... $rate  (quantity first)
    r"(?P<qty>\d*\.?\d+)\s*(?P<unit>Man\s*Hours?|Hours?|Hrs?|[A-Za-z]{1,4})\b"
    r".*?x\s*\$?\s*(?P<rate>[\d,]+(?:\.\d+)?)", re.I)
INLINE_RQ  = re.compile(  # $rate ... x ... qty unit   (rate first)
    r"\$?\s*(?P<rate>[\d,]+(?:\.\d+)?)\s*/\s*(?P<unit>[A-Za-z]{1,4})\b"
    r".*?x\s*(?P<qty>\d*\.?\d+)", re.I)

# Real T&M ("time & material") tags print a labor line as a trade CLASSIFICATION
# followed by the hourly rate, then the hours, with NO 'HR' unit token on the
# line and the extension offset a row away (column reflow) - e.g. Dinelli
# Plumbing's tags in Redwood City CO#4:  'JOURNEYMAN   $152.35   7.00   $285.60'.
# The classification keyword is the anchor that makes this a labor line (so we do
# not mistake an arbitrary '$x  y' pair for one); the trailing money is the
# offset column, so - as everywhere else - we take only rate and hours and let
# the caller recompute the extension.
TM_CLASS = (r"GENERAL\s+FOREMAN|FOREMAN|JOURNEYMAN|APPRENTICE|LABORER|"
            r"OPERATOR|PLUMBER|CARPENTER|ELECTRICIAN")
TM_LABOR = re.compile(
    rf"^\s*(?P<desc>(?:{TM_CLASS})(?:\s+[A-Za-z]+){{0,3}})"
    r"\s+\$\s*(?P<rate>[\d,]+\.\d\d)\s+(?P<hrs>\d+(?:\.\d+)?)\b", re.I)


def parse_inline(line):
    """Parse a 'TAG = qty Unit Desc x $rate/Unit = $ext' cost line. Returns
    (tag, qty, unit, rate, ext_None, is_deduct) or None.

    NOTE ext is deliberately always None. In real COs the dollar column is
    routinely offset by a row from the description column (pdftotext linearises
    the two columns independently), so the '$ext' printed on a text line often
    belongs to the neighbouring line. Tying out against it fabricates
    discrepancies on documents whose math is actually correct. We therefore take
    only the reliable inputs on the same line - the quantity and the rate, which
    sit together before the first '=' - and let callers recompute the extension.
    A leading 'Deduct' in the body (reliable, it is in the description column)
    marks a deduction, so the recomputed amount is negated."""
    m = INLINE_TAG.match(line)
    if not m:
        return None
    tag, body = m.group("tag").upper(), m.group("body")
    qr = INLINE_QR.search(body) or INLINE_RQ.search(body)
    if not qr:
        return None
    qty = float(qr.group("qty"))
    rate = float(qr.group("rate").replace(",", ""))
    unit = re.sub(r"man\s*hours?|hours?|hrs?", "HR", qr.group("unit"), flags=re.I).upper()
    is_deduct = "deduct" in body.lower()
    return tag, qty, unit, rate, None, is_deduct


def labor_rates(pages):
    """Contract labor rates: rate-schedule lines whose unit is an hour."""
    out = {}
    for pno, page in enumerate(pages, 1):
        for line in page.splitlines():
            m = RATE_LINE.match(line.strip())
            if m and m.group("unit").upper() in ("HR", "HOUR"):
                out[norm_desc(m.group("desc"))] = {
                    "rate": float(m.group("rate").replace(",", "")),
                    "page": pno,
                    "raw": re.sub(r"\s+", " ", m.group("desc")).strip()[:50]}
    return out


def labor_items(page):
    """Yield (desc, hrs, rate, ext_or_None, raw_desc) labor lines on a page,
    from either the column format (LABOR_LINE) or the inline 'L = ...' format."""
    for line in page.splitlines():
        s = line.strip()
        p = parse_inline(s)
        if p and p[0] == "L":            # inline labor
            _, hrs, unit, rate, ext, ded = p
            if "HR" not in unit:
                continue
            desc = re.sub(r"\s+", " ", INLINE_TAG.match(s).group("body"))
            yield desc[:45], hrs, rate, ext, desc   # ext is None -> computed
            continue
        m = LABOR_LINE.match(s)
        if m:
            yield (re.sub(r"\s+", " ", m.group("desc")).strip()[:45],
                   float(m.group("hrs")),
                   float(m.group("rate").replace(",", "")),
                   float(m.group("ext").replace(",", "")) if m.group("ext") else None,
                   m.group("desc"))
            continue
        m = TM_LABOR.match(s)          # T&M classification tag (rate before hours)
        if m:
            desc = re.sub(r"\s+", " ", m.group("desc")).strip()
            yield (desc[:45], float(m.group("hrs")),
                   float(m.group("rate").replace(",", "")),
                   None,               # printed ext is the offset column -> recompute
                   desc)


def audit_labor(co_pages, rates):
    findings = []
    total_hrs, total_cost, lines = 0.0, 0.0, 0
    for pno, page in enumerate(co_pages, 1):
        for desc, hrs, rate, ext, raw in labor_items(page):
            if hrs <= 0 or rate <= 0 or rate > 100000:
                continue
            lines += 1
            total_hrs += hrs
            total_cost += ext if ext is not None else hrs * rate

            # (a) arithmetic integrity: hours x rate must equal the extension
            #     (only when the extension is actually printed on this line)
            if ext is not None and abs(hrs * rate - ext) > max(ROUND_TOL, abs(ext) * 0.001):
                findings.append(("VIOLATION", pno,
                    f"'{desc}': {hrs:g} hr x {fmt(rate)} = {fmt(hrs*rate)}, but "
                    f"the line extends to {fmt(ext)} - "
                    f"discrepancy {fmt(abs(hrs*rate-ext))}."))

            # (b) rate inflation vs the contract labor rate schedule.
            #     A T&M tag often names only the classification ('JOURNEYMAN')
            #     while the contract lists it in full ('Journeyman Plumber'), so
            #     match on containment - what fraction of the CO line's tokens
            #     appear in the contract description - not plain Jaccard overlap,
            #     which would score that pair 0.5 and miss it. Among equally
            #     contained entries prefer the closest (fewest extra tokens), so a
            #     bare 'Journeyman' ties to 'Journeyman Plumber' (straight time),
            #     not 'Journeyman Plumber overtime' - the conservative pick.
            if rates:
                nd = set(norm_desc(raw).split())
                best, score, extra = None, 0.0, 1e9
                for rd, info in rates.items():
                    rdset = set(rd.split())
                    inter = nd & rdset
                    if not inter:
                        continue
                    c = len(inter) / max(1, len(nd))
                    x = len(rdset - nd)
                    if c > score or (c == score and x < extra):
                        best, score, extra = info, c, x
                if best and score >= 0.6 and rate > best["rate"] * 1.001:
                    findings.append(("VIOLATION", pno,
                        f"'{desc}': billed {fmt(rate)}/hr vs contract labor rate "
                        f"{fmt(best['rate'])}/hr (p{best['page']}, "
                        f"match {score:.0%}). x {hrs:g} hr = "
                        f"OVERCHARGE {fmt((rate-best['rate'])*hrs)}."))

            # (c) surface the line for the human reasonableness call
            shown = fmt(ext) if ext is not None else fmt(hrs * rate) + " (computed)"
            findings.append(("REVIEW", pno,
                f"'{desc}': {hrs:g} hr @ {fmt(rate)}/hr = {shown} - "
                f"verify hours against the scope."))
    summary = None
    if lines:
        blended = total_cost / total_hrs if total_hrs else 0
        summary = (f"{lines} labor line(s): {total_hrs:g} hr, {fmt(total_cost)} "
                   f"total, blended {fmt(blended)}/hr.")
    return findings, summary


# -----------------------------------------------------------------------------
# CHECK 4 : material-quantity tie-out to the drawing
#
# The gold check is "does the CO's material quantity tie to the extra scope on
# the drawing". A number is only worth comparing against another number, so the
# tool does NOT eyeball a rendered page - it drives the existing drawing-takeoff
# engine, which builds the drawing PDF into a queryable quantity database and
# reports each count with a confidence level (schedule=high, tag=medium). So:
#   (a) recompute qty x unit price and flag extensions that do not tie out;
#   (b) build the drawing into the takeoff DB (or reuse a supplied .db / takeoff
#       text export), query the taken-off quantity for each CO material line,
#       and flag any CO quantity that exceeds it - carrying the takeoff's own
#       confidence so a medium-confidence count is not sold as certainty;
#   (c) only when the engine cannot quantify an item at all does the tool fall
#       back to rendering the page for a human / vision count.
MATERIAL_LINE = re.compile(
    r"^(?P<desc>.+?)\s+(?P<qty>[\d,]+(?:\.\d+)?)\s*(?P<unit>"
    rf"{UNIT})\b.*?\$?\s*(?P<rate>[\d,]+\.\d\d)"
    r"(?:.*?\$?\s*(?P<ext>[\d,]+\.\d\d))?\s*$", re.I)

# the drawing-takeoff engine ships alongside this repo; find it without
# hard-coding one machine's path
_HERE = os.path.dirname(os.path.abspath(__file__))
TAKEOFF_ROOTS = [os.path.join(_HERE, "drawing-takeoff"),
                 os.path.join(_HERE, "..", "drawing-takeoff"),
                 os.path.expanduser("~/Downloads/drawing-takeoff"),
                 "drawing-takeoff", "../drawing-takeoff"]


def find_takeoff_engine():
    for root in TAKEOFF_ROOTS:
        cand = os.path.join(root, "scripts", "takeoff.py")
        if os.path.exists(cand):
            return cand
    return None


def build_takeoff_db(drawing_pdf):
    """Run the takeoff engine to turn a drawing PDF into a quantity DB.
    Returns the db path, or None if the engine or build is unavailable."""
    engine = find_takeoff_engine()
    if not engine or not drawing_pdf or not os.path.exists(drawing_pdf):
        return None, engine
    os.makedirs("outputs", exist_ok=True)
    db = os.path.join("outputs",
                      os.path.splitext(os.path.basename(drawing_pdf))[0] + ".db")
    if not os.path.exists(db):
        r = subprocess.run(["py", engine, "build", drawing_pdf, "--db", db],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(db):
            return None, engine
        # `build` alone captures only tag-callout counts (medium confidence).
        # The high-confidence counts a takeoff turns on - doors, panels, etc. -
        # live in the drawing's SCHEDULE tables, which the engine reads in a
        # separate pass. Skipping it silently loses whole object types (a fresh
        # build of the 89-sheet demo set finds columns but NOT its 147 doors),
        # so the audit would under-count and miss a real over-billing. Run it as
        # part of the build. It is slower (large sheets ~30s each), but a takeoff
        # DB is built once and reused, and a pricing audit must not trade away a
        # whole category of finding for speed.
        subprocess.run(["py", engine, "schedules", drawing_pdf, "--db", db,
                        "--auto"], capture_output=True, text=True)
    return db, engine


def takeoff_quantities(db):
    """Read every object type and its count+confidence from a takeoff DB.
    Returns {type: {"qty": n, "unit": "EA", "confidence": c}}."""
    engine = find_takeoff_engine()
    if not engine or not db or not os.path.exists(db):
        return {}
    r = subprocess.run(["py", engine, "query", "sql",
                        "SELECT type, COUNT(*), "
                        "MIN(confidence) FROM objects GROUP BY type",
                        "--db", db], capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s*([a-z_]+)\s*\|\s*(\d+)\s*\|\s*(\w+)", line)
        if m and m.group(1) != "type":
            out[m.group(1)] = {"qty": float(m.group(2)), "unit": "EA",
                               "confidence": m.group(3)}
    return out


def parse_takeoff_text(pages):
    """A takeoff text export: 'description  qty  UNIT' -> quantity dict."""
    out = {}
    for page in pages:
        for line in page.splitlines():
            m = re.match(rf"^(?P<desc>.+?)\s+(?P<qty>[\d,]+(?:\.\d+)?)\s*"
                         rf"(?P<unit>{UNIT})\b", line.strip(), re.I)
            if m:
                d = norm_desc(m.group("desc"))
                if len(d) >= 4:
                    out[d] = {"qty": float(m.group("qty").replace(",", "")),
                              "unit": m.group("unit").upper(),
                              "confidence": "stated"}
    return out


# units the takeoff engine counts (EA). Volume/area/length units need the
# dimensional takeoff, not an object count, so they are not compared here.
COUNT_UNITS = {"EA", "EACH"}


def material_items(page):
    """Yield (desc, qty, unit, rate, ext_or_None, raw) material/equipment lines
    from either the column format (MATERIAL_LINE) or the inline 'M =' / 'E ='."""
    for line in page.splitlines():
        s = line.strip()
        p = parse_inline(s)
        if p and p[0] in ("M", "E"):
            _, qty, unit, rate, ext, ded = p
            if "HR" in unit:
                continue
            desc = re.sub(r"\s+", " ", INLINE_TAG.match(s).group("body"))
            # deduct -> negative signed quantity so totals net correctly; ext
            # stays None so the offset-prone printed figure is never tied out
            yield desc[:45], (-qty if ded else qty), unit, rate, ext, desc
            continue
        m = MATERIAL_LINE.match(s)
        if m and m.group("unit").upper() not in ("HR", "HOUR"):
            yield (re.sub(r"\s+", " ", m.group("desc")).strip()[:45],
                   float(m.group("qty").replace(",", "")),
                   m.group("unit").upper(),
                   float(m.group("rate").replace(",", "")),
                   float(m.group("ext").replace(",", "")) if m.group("ext") else None,
                   m.group("desc"))


def audit_material(co_pages, takeoff, tsrc=""):
    findings = []
    lines, unmatched = 0, 0
    for pno, page in enumerate(co_pages, 1):
        for desc, qty, unit, rate, ext, raw in material_items(page):
            if qty == 0 or rate <= 0:
                continue
            lines += 1

            # (a) qty x unit price must tie to the extension (only when printed)
            if ext is not None and abs(qty * rate - ext) > max(ROUND_TOL, abs(ext) * 0.001):
                findings.append(("VIOLATION", pno,
                    f"'{desc}': {qty:g} {unit} x {fmt(rate)} = {fmt(qty*rate)}, "
                    f"but the line extends to {fmt(ext)} - "
                    f"discrepancy {fmt(abs(qty*rate-ext))}."))

            # (b) quantity vs the taken-off quantity
            matched = False
            if takeoff:
                nd = norm_desc(raw)
                best, info, score = None, None, 0.0
                for td, i in takeoff.items():
                    s = max(overlap(nd, td),
                            1.0 if td in nd.split() else 0.0)  # type token in desc
                    if s > score:
                        best, info, score = td, i, s
                if best and score >= 0.6:
                    matched = True
                    # compare only when the units agree: EA-vs-EA from the
                    # object-count DB, or CY/SF/LF-vs-same from a dimensional
                    # takeoff export. A CY concrete line matched to an EA object
                    # count is NOT settleable here - say so rather than guess.
                    if unit == info["unit"] or (unit in COUNT_UNITS
                                                and info["unit"] in COUNT_UNITS):
                        conf = f"{info['confidence']}-confidence takeoff"
                        if qty > info["qty"] * 1.02:
                            findings.append(("VIOLATION", pno,
                                f"'{desc}': CO bills {qty:g} {unit} but the "
                                f"{conf} shows {info['qty']:g} '{best}' "
                                f"(match {score:.0%}). Excess "
                                f"{qty-info['qty']:g} x {fmt(rate)} = "
                                f"OVERCHARGE {fmt((qty-info['qty'])*rate)}."))
                        else:
                            findings.append(("OK", pno,
                                f"'{desc}': {qty:g} {unit} <= {info['qty']:g} "
                                f"'{best}' ({conf})."))
                    else:
                        # matched an item but the CO unit is a volume/area/length
                        # the object-count takeoff cannot settle
                        findings.append(("REVIEW", pno,
                            f"'{desc}': {qty:g} {unit} - matched drawing item "
                            f"'{best}' but its {unit} volume/area needs the "
                            f"dimensional takeoff to tie out."))
            if not matched:
                unmatched += 1
                shown = fmt(ext) if ext is not None else fmt(qty * rate) + " (computed)"
                findings.append(("REVIEW", pno,
                    f"'{desc}': {qty:g} {unit} @ {fmt(rate)} = {shown} - "
                    f"no matching item in {tsrc or 'the takeoff'}; tie by hand."))
    return findings, lines, unmatched


def render_page(pdf, pno, tag):
    """Last-resort: rasterise one page so a human / vision pass can count when
    the takeoff engine could not quantify the item at all."""
    if not pdf:
        return None
    os.makedirs("outputs", exist_ok=True)
    stem = os.path.join("outputs", f"co_{tag}")
    try:
        subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", "150",
                        "-png", pdf, stem], capture_output=True, check=True)
    except Exception:
        return None
    hits = sorted(glob.glob(stem + "*.png"))
    return hits[0] if hits else None


# -----------------------------------------------------------------------------
# cap sourcing (provenance)
CAP_PHRASE = re.compile(
    r"(self-performed|lower tier|subcontractor|sub-?contractor|combined|bond|"
    r"insurance)[^.]{0,120}?(?:not[- ]to[- ]exceed|maximum|shall not exceed|"
    r"exceed)[^.]{0,40}?(\d{1,2}(?:\.\d+)?)\s*%", re.I)


def caps_from_contract(pages):
    """Read markup caps stated in a contract exhibit / rate schedule.
    Returns (caps_dict, source_note_dict)."""
    caps, src = {}, {}
    text = "\n".join(pages)
    for m in CAP_PHRASE.finditer(text):
        subj, val = m.group(1).lower(), float(m.group(2))
        key = ("sub" if "sub" in subj or "lower tier" in subj else
               "bond" if "bond" in subj or "insurance" in subj else
               "combined" if "combined" in subj else "self")
        caps.setdefault(key, val)
        src.setdefault(key, "contract")
    return caps, src


def caps_from_form(ladders):
    """Caps printed on the change-order form's own ladder rungs.

    'for Contractor' is ambiguous on these forms: on a self-performed ladder it
    is the self cap (~15%); on a subcontractor ladder it is the GC's markup on
    sub work (~5%). Disambiguate by what the rung marks up - a markup applied
    directly to the raw direct-cost base is self-performed; one applied on top
    of a subtotal that already carried a markup is the GC-on-sub cap."""
    caps, src = {}, {}
    for L in ladders:
        seen_markup = False     # has any markup already been applied in this ladder
        for r in L:
            if r["cap_pct"] is None:
                if r["kind"] == "markup":
                    seen_markup = True
                continue
            if r["kind"] == "bond":
                key = "bond"
            elif "sub" in r["label"].lower() and "contractor" in r["label"].lower():
                key = "sub_op"          # the subcontractor's own O&P
            elif seen_markup:
                key = "sub"             # GC markup stacked on already-marked work
            else:
                key = "self"            # markup on raw direct cost
            if r["kind"] == "markup":
                seen_markup = True
            caps.setdefault(key, r["cap_pct"])
            src.setdefault(key, "stated on the CO form")
    return caps, src


def caps_from_config():
    caps, src = {}, {}
    path = "inputs/co_caps.txt"
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*(self|sub_op|sub|combined|bond)\s*[:=]\s*([\d.]+)",
                         line, re.I)
            if m:
                caps[m.group(1).lower()] = float(m.group(2))
                src[m.group(1).lower()] = "inputs/co_caps.txt"
    return caps, src


# -----------------------------------------------------------------------------
def fmt(x):
    return "$-" if x is None else f"${x:,.2f}"


def main():
    if len(sys.argv) < 2:
        print("usage: py work/co_audit.py <change-order.pdf> "
              "[contract-or-rate-schedule.pdf] [takeoff.txt | drawing.pdf]")
        sys.exit(1)

    co_path = sys.argv[1]
    co_pages, co_pdf, co_ocr = load(co_path)
    if co_pages is None:
        print(f"change order not found or unreadable: {co_path}")
        sys.exit(1)

    contract_pages = None
    if len(sys.argv) > 2:
        contract_pages, _, _ = load(sys.argv[2])

    # 3rd argument feeds check 4. It can be a drawing PDF (built into the
    # takeoff quantity DB), a pre-built takeoff .db, or a takeoff text export.
    takeoff, tsrc, drawing_pdf = None, "", None
    if len(sys.argv) > 3:
        third = sys.argv[3]
        if third.lower().endswith(".pdf"):
            drawing_pdf = find_pdf(os.path.splitext(os.path.basename(third))[0]) \
                          or (third if os.path.exists(third) else None)
            db, engine = build_takeoff_db(drawing_pdf)
            if db:
                takeoff = takeoff_quantities(db)
                tsrc = f"takeoff DB built from {os.path.basename(drawing_pdf)}"
            elif not engine:
                tsrc = "(takeoff engine not found - drawing not quantified)"
        elif third.lower().endswith(".db"):
            takeoff = takeoff_quantities(third)
            tsrc = f"takeoff DB {os.path.basename(third)}"
        else:
            tk_pages, _, _ = load(third)
            takeoff = parse_takeoff_text(tk_pages) if tk_pages else None
            tsrc = f"takeoff export {os.path.basename(third)}"

    print(f"CHANGE-ORDER PRICING AUDIT")
    print(f"  change order : {os.path.basename(co_path)}  ({len(co_pages)} pages)")
    # Scan handling (no OCR, no API). Pages Claude already transcribed from the
    # rendered image were merged in load() and come back in co_ocr; pages still
    # blank need transcribing. A number read from a scan is flagged for the human.
    scan_pages = set(co_ocr)
    if scan_pages:
        pp = ", ".join(str(p) for p in sorted(scan_pages))
        print(f"  [scan] page(s) {pp} had no text layer and were read from the "
              f"rendered image by Claude - findings from them are marked [SCAN] "
              f"and must be verified against the source page (a picture-read "
              f"number is never sold as certainty).")
    pending = scanned_pages(co_pages)
    if pending:
        mostly = len(pending) > 20 or len(pending) >= max(1, len(co_pages) // 2)
        if mostly:
            print(f"  [!] {len(pending)}/{len(co_pages)} page(s) have no text layer "
                  f"- this looks like a mostly-SCANNED PDF. Request the native "
                  f"text PDF; transcribing this many pages by image is impractical.")
        else:
            sdir = scan_dir_for(co_path)
            imgs = render_scan_pages(co_pdf, pending, sdir)
            pp = ", ".join(str(p) for p in pending)
            if imgs:
                print(f"  [!] SCANNED page(s) {pp} have no text layer. This tool "
                      f"does not OCR or guess digits. Rendered to {sdir} - read "
                      f"each pNN.png and write its text to the matching pNN.txt, "
                      f"then re-run: those pages will be merged and every finding "
                      f"from them flagged [SCAN] for verification.")
            else:
                print(f"  [!] page(s) {pp} have no text layer and could not be "
                      f"rendered (need the native PDF and poppler's pdftoppm).")

    def scanflag(pg):
        return f"  [SCAN - verify against source page {pg}]" if pg in scan_pages else ""
    if contract_pages:
        print(f"  contract     : {os.path.basename(sys.argv[2])}  "
              f"({len(contract_pages)} pages)")
    if drawing_pdf:
        print(f"  drawing      : {os.path.basename(drawing_pdf)}")
    if takeoff:
        print(f"  takeoff      : {tsrc}  ({len(takeoff)} item types)")
    print()

    # ---- resolve caps, most-authoritative first -----------------------------
    ladders = parse_ladders(co_pages)
    caps = dict(DEFAULT_CAPS)
    cap_src = {k: "industry default" for k in caps}
    for source in (caps_from_config(),
                   caps_from_form(ladders),
                   caps_from_contract(contract_pages) if contract_pages else ({}, {})):
        c, s = source
        for k, v in c.items():
            caps[k] = v
            cap_src[k] = s[k]

    # if the contract states a cap that is stricter than what the FORM prints,
    # the form's own printed cap is itself a violation of the contract
    form_caps, _ = caps_from_form(ladders)
    if contract_pages:
        ccaps, _ = caps_from_contract(contract_pages)
        for k in ("self", "sub", "bond", "combined"):
            if k in form_caps and k in ccaps and form_caps[k] > ccaps[k] + PCT_TOL:
                print(f"  [!] CONTRACT CONFLICT: the CO form prints a "
                      f"{form_caps[k]:g}% {k} cap, but the contract allows only "
                      f"{ccaps[k]:g}%. Audit uses the contract's {ccaps[k]:g}%.")
                caps[k] = ccaps[k]
                cap_src[k] = "contract (form's printed cap exceeds it)"
        print()

    print(f"  caps applied : self {caps['self']:g}%  sub-own-O&P "
          f"{caps['sub_op']:g}%  GC-on-sub {caps['sub']:g}%  "
          f"combined {caps['combined']:g}%  bond {caps['bond']:g}%")
    print(f"                 (self: {cap_src['self']})")
    print()

    # Track which checks actually had the reference data to run, so the summary
    # can distinguish "clean" from "not checked" - a 0 that means "the tool
    # inspected this and it's fine" is very different from a 0 that means "no
    # rate schedule was supplied, so we couldn't look".
    ran = {"markup": False, "unit_price": False, "labor": False, "material": False}
    reason = {"markup": "", "unit_price": "", "labor": "", "material": ""}

    # ---- CHECK 1 : markup / O&P ladder --------------------------------------
    print("CHECK 1 - MARKUP / O&P CAP")
    if not ladders:
        print("  no cost ladder found - the CO may be a flat lump sum with no "
              "itemised markup. Nothing to recompute; verify the total by hand.")
        reason["markup"] = "no lettered cost ladder found in the CO"
    else:
        ran["markup"] = True
    v1 = 0
    for n, L in enumerate(ladders, 1):
        lo = min(r["page"] for r in L)
        block = next((r["label"] for r in L if r["kind"] in ("cost", "subtotal")), "")
        print(f"  ladder {n} (p{lo}):")
        finds, stack, base, n_markups = audit_markups(L, caps, cap_src)
        for level, pg, msg in finds:
            mark = {"VIOLATION": "  [X]", "REVIEW": "  [?]", "OK": "  [ ]"}[level]
            print(f"  {mark} p{pg}: {msg}{scanflag(pg)}")
            if level == "VIOLATION":
                v1 += 1
        # the stacked-markup line is only a *separate* finding when two or more
        # markup tiers were charged (genuine stacking); with a single markup
        # rung it is the same dollars the per-rung check already flagged.
        if stack is not None and n_markups >= 2:
            over = stack > caps["combined"] + PCT_TOL
            print(f"  {'[X]' if over else '[ ]'} effective stacked markup "
                  f"across {n_markups} tiers: {stack:.2f}% of {fmt(base)} base"
                  + (f"  - OVER the {caps['combined']:g}% combined cap "
                     f"({cap_src.get('combined','')})" if over else ""))
            if over:
                v1 += 1
        elif stack is not None:
            print(f"  [ ] total O&P: {stack:.2f}% of {fmt(base)} base "
                  f"(single tier)")
    print()

    # ---- CHECK 2 : unit price vs contracted rate ----------------------------
    print("CHECK 2 - UNIT PRICE vs CONTRACT RATE")
    v2 = 0
    if not contract_pages:
        print("  no contract / rate schedule supplied - pass one as the 2nd "
              "argument to check CO unit prices against agreed rates. Skipped.")
        reason["unit_price"] = "no contract / rate schedule supplied"
    else:
        rates = parse_rate_schedule(contract_pages)
        if not rates:
            print(f"  contract supplied but no unit-rate table detected in it "
                  f"(it may be a prose exhibit, not a rate schedule). Skipped.")
            reason["unit_price"] = "contract has no parseable unit-rate table"
        else:
            print(f"  {len(rates)} contract rates parsed.")
            ran["unit_price"] = True
            finds = audit_unit_prices(co_pages, rates)
            if not finds:
                print("  no CO line matched a contract rate above the 60% "
                      "description-overlap threshold.")
            for level, pg, msg in finds:
                mark = "  [X]" if level == "VIOLATION" else "  [ ]"
                print(f"  {mark} p{pg}: {msg}{scanflag(pg)}")
                if level == "VIOLATION":
                    v2 += 1
    print()

    def emit(finds, review_cap=6):
        """Print findings; violations always, REVIEW lines throttled so a large
        backup package does not bury the signal. Returns the violation count."""
        v = 0
        shown = 0
        for level, pg, msg in finds:
            if level == "REVIEW":
                if shown >= review_cap:
                    continue
                shown += 1
                print(f"  [?] p{pg}: {msg}{scanflag(pg)}")
            else:
                mark = "  [X]" if level == "VIOLATION" else "  [ ]"
                print(f"  {mark} p{pg}: {msg}{scanflag(pg)}")
                if level == "VIOLATION":
                    v += 1
        hidden = sum(1 for f in finds if f[0] == "REVIEW") - shown
        if hidden > 0:
            print(f"  [?] ... and {hidden} more labor/material line(s) to eyeball "
                  f"(full list is deterministic; re-run per page to see them).")
        return v

    # ---- CHECK 3 : labor-hour reasonableness --------------------------------
    print("CHECK 3 - LABOR-HOUR REASONABLENESS")
    lrates = labor_rates(contract_pages) if contract_pages else {}
    lfinds, lsummary = audit_labor(co_pages, lrates)
    if lsummary:
        print(f"  {lsummary}")
        if lrates:
            ran["labor"] = True
            print(f"  {len(lrates)} contract labor rate(s) available for "
                  f"comparison.")
        else:
            print("  (no contract labor rates supplied - rate inflation not "
                  "checked; arithmetic and hours surfaced for review.)")
            reason["labor"] = ("no contract labor rates supplied - only "
                               "arithmetic + hours surfaced for review")
        v3 = emit(lfinds)
    else:
        print("  no itemised labor lines (hours x rate) found to check.")
        reason["labor"] = "no itemised labor lines in the CO"
        v3 = 0
    print()

    # ---- CHECK 4 : material quantity tie-out --------------------------------
    print("CHECK 4 - MATERIAL-QTY vs DRAWING TAKEOFF")
    mfinds, mlines, unmatched = audit_material(co_pages, takeoff, tsrc)
    if mlines:
        if takeoff:
            print(f"  {mlines} material line(s) checked against the {tsrc} "
                  f"({len(takeoff)} item types).")
            ran["material"] = True
        else:
            print(f"  {mlines} material line(s); no drawing/takeoff supplied - "
                  f"pass a drawing PDF (built into the takeoff DB), a .db, or a "
                  f"takeoff export as the 3rd arg. Quantities surfaced.")
            reason["material"] = "no drawing / takeoff supplied to compare against"
        v4 = emit(mfinds)
        # last resort: only render for a human when the engine matched nothing
        if drawing_pdf and takeoff and unmatched == mlines:
            img = render_page(drawing_pdf, 1, "drawing")
            if img:
                print(f"  takeoff matched no CO line; drawing page rendered as a "
                      f"fallback for a manual/vision count: {img}")
    else:
        print("  no itemised material lines (qty x unit price) found to check.")
        reason["material"] = "no itemised material lines in the CO"
        v4 = 0
    print()

    # ---- coverage-aware summary --------------------------------------------
    total = v1 + v2 + v3 + v4
    n_ran = sum(ran.values())
    label = {"markup": "markup/O&P cap", "unit_price": "unit-price vs rate",
             "labor": "labor rate", "material": "material qty vs takeoff"}
    print("=" * 60)
    if total:
        print(f"  {total} pricing finding(s) to recover or renegotiate "
              f"({n_ran} of 4 checks had the data to run).")
    else:
        print(f"  0 findings - but only {n_ran} of 4 checks had the reference "
              f"data to run, so this is NOT a clean bill of health.")
    # spell out exactly what did and did not run
    for key in ("markup", "unit_price", "labor", "material"):
        if ran[key]:
            print(f"    [checked]     {label[key]}")
        else:
            print(f"    [NOT CHECKED] {label[key]} - {reason[key]}")
    if n_ran < 4:
        print("  Supply the missing reference doc(s) above for a full audit; "
              "until then a human must still review the unchecked items.")
    if scan_pages:
        print(f"  [scan] {len(scan_pages)} page(s) were read from a scanned image "
              f"by Claude, not a text layer. Every [SCAN]-marked finding above "
              f"must be verified against the source page before you act on it.")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
