# Change-Order Pricing Audit — Handoff

> Read this first. It tells a fresh Claude session exactly what this tool is,
> where every file lives, what works, what's left, and how to run it.
> Last updated: 2026-07-23.

---

## 1. What this tool is

An **owner / PM-side** tool that audits a contractor's change-order (CO) proposal
for pricing abuse. The PM's pain today: they open the CO PDF, open the contract's
rate schedule, open the drawing, and compare line-by-line in Excel — 1–3 hours per
CO, dozens of COs per job — hunting for inflated markups, stacked O&P, inflated
unit prices, padded labor, and quantities that don't tie to the drawing.

This is a **new customer segment** (owner's rep / project manager), distinct from
the RFP-analysis tools in this same repo (`amendment_diff.py`, `compliance_matrix.py`,
`lm_crosswalk.py`), which target the proposal/estimator side.

The tool is **deterministic**: every finding is a dollar figure with a page anchor,
not an opinion. Judgment calls (is 42 hrs reasonable for this scope?) are
**surfaced for a human, never ruled on** — because a wrong "this is padded" costs
more trust than staying quiet, for this persona.

---

## 2. Where everything lives

**Main script**
```
C:\Users\Hamza\Downloads\client prototype\work\co_audit.py      (~1120 lines)
```

**Regression suite (62 tests, all green)**
```
C:\Users\Hamza\Downloads\client prototype\work\run_co_tests.sh
```
Run it from the project root:
```
cd "C:\Users\Hamza\Downloads\client prototype"
bash work/run_co_tests.sh
```

**Synthetic unit-test fixtures** (small hand-made inputs that exercise the logic;
NOT the proof it works on real docs — that's the real docs below):
```
work\co_fixtures\synthetic_co.txt     - stacking abuse + unit-price inflation
work\co_fixtures\rate_schedule.txt    - contract unit rates for the above
work\co_fixtures\synthetic_co2.txt    - labor rate inflation + padded extension
work\co_fixtures\rate_schedule2.txt   - labor + material rates for the above
work\co_fixtures\takeoff.txt          - dimensional takeoff (CY/SF) for check 4
work\co_fixtures\co_doors.txt         - CO billing doors/columns, for the takeoff-DB test
work\co_fixtures\scan_co.txt          - scanned-page fixture: p1 has text, p2 blank
work\co_fixtures\scan_co.scan\p02.txt - sidecar transcription for p2 (scan path)
work\co_fixtures\scan_rates.txt       - contract rates for the scan test
```

**REAL test documents** (this is the important set — everything is proven here):
```
C:\Users\Hamza\Downloads\co_audit_docs\
  00410_pricing_change_orders.pdf/.txt   - university contract exhibit stating markup
                                           CAPS (10% self, 5% sub, 0% bond). Contract side.
  rwc_changeorder1.pdf/.txt              - Redwood City School District CO#1, electrical,
                                           $11,009. CLEAN (at-cap).
  rwc_changeorder4.pdf/.txt              - RWC 85-page CO#4 package. HAS real overcharges
                                           for BOTH Check 1 (markup) and Check 3 (labor).
  rwc_contract_rates.txt                 - Dinelli Plumbing labor rates ($134.12/hr JW,
                                           $175.76/hr foreman) extracted from CO#4's own
                                           labor-burden sheet. The Check 3 reference.
  otsego_co.pdf/.txt                     - Otsego MN municipal CO (water treatment).
                                           Native-text, inline "L/M = qty x rate = ext"
                                           format. Math is CLEAN (tool must flag nothing).
  baxter_co3.pdf/.txt                    - City of Baxter MN CO#3 (Wells 5&6 raw water main).
                                           Real civil unit-price CO. HAS a real Check 2
                                           overcharge (aggregate base $45/CY vs bid $35/CY).
  baxter_bidtab.pdf/.txt                 - Baxter's official bid tabulation showing all
                                           bidders' unit prices side-by-side.
  baxter_contract_rates.txt              - Pratt's AWARDED column extracted from the bid
                                           tab (lump-sum items omitted). The Check 2 reference.
```

**The drawing-takeoff engine** (Check 4 depends on this — separate tool that already
existed in the repo, also on GitHub at github.com/hamzaabduljabbar/autoConst-drawing-takeoff-claude):
```
C:\Users\Hamza\Downloads\drawing-takeoff\scripts\takeoff.py
C:\Users\Hamza\Downloads\drawing-takeoff\demo.db   - real DB built from a real 89-sheet
                                                     Kingston HS drawing set (147 doors,
                                                     9 columns, 314 panels, etc.)
```
`co_audit.py` finds this engine automatically (`TAKEOFF_ROOTS` in the script) and shells
out to `py takeoff.py build ... --db ...` followed by `schedules ... --auto` (see §5.3).

**Candidate drawings on disk** (for future Check 4 work — see §7):
```
~/Downloads/Architectural-Structural-Holabird-Bid-Set-Drawings.pdf   - 96-page real
                                                                       vector set with
                                                                       DOOR / COLUMN /
                                                                       EQUIPMENT /
                                                                       LIGHTING FIXTURE
                                                                       schedules. No
                                                                       matching CO yet.
```

---

## 3. How to run it

```
py work/co_audit.py <change-order.pdf> [contract-or-rate-schedule] [drawing.pdf | takeoff.db | takeoff.txt]
```
- **arg 1** (required): the CO proposal PDF (or a pre-extracted .txt).
- **arg 2** (optional): the contract / rate schedule — powers Check 2 (unit price)
  and Check 3 (labor rate) and the markup-cap override for Check 1.
- **arg 3** (optional): feeds Check 4. A **drawing PDF** (built into the takeoff
  DB automatically), a pre-built **.db**, or a **takeoff .txt** export.

### Canonical demo commands (all real docs)
```
# Check 1: overcharges caught against the CO's own form ($85.46 O&P + $76.19 bond):
py work/co_audit.py ~/Downloads/co_audit_docs/rwc_changeorder4.pdf

# Check 1 provenance killer: same CO vs the stricter 00410 contract — overcharges
# balloon to ~$2,399 (the 15% markups the form "allows" violate the contract's 10% cap):
py work/co_audit.py ~/Downloads/co_audit_docs/rwc_changeorder4.pdf ~/Downloads/co_audit_docs/00410_pricing_change_orders.pdf

# Check 2: real unit-price inflation on Baxter CO#3 — aggregate base billed $45/CY
# vs the contractor's own $35/CY bid = $900 overcharge:
py work/co_audit.py ~/Downloads/co_audit_docs/baxter_co3.pdf ~/Downloads/co_audit_docs/baxter_contract_rates.txt

# Check 3: real labor-rate inflation on RWC CO#4 Dinelli T&M tags — journeyman
# billed $152.35/hr vs contract $134.12/hr = $127.61 (p53) + $145.84 (p62):
py work/co_audit.py ~/Downloads/co_audit_docs/rwc_changeorder4.pdf ~/Downloads/co_audit_docs/rwc_contract_rates.txt

# Check 4: real Kingston takeoff DB — synthetic CO bills 160 doors vs 147 in drawings
# ($5,460 door overcharge + $5,550 column overcharge). CO side is still synthetic.
py work/co_audit.py work/co_fixtures/co_doors.txt "" ~/Downloads/drawing-takeoff/demo.db

# Check 4 from raw drawing PDF (builds the takeoff DB itself, ~10 min first time,
# then cached — see §5.3):
py work/co_audit.py work/co_fixtures/co_doors.txt "" ~/Downloads/100643PLANSC.pdf

# Otsego native-text CO whose math is clean — tool correctly flags NOTHING:
py work/co_audit.py ~/Downloads/co_audit_docs/otsego_co.pdf
```

---

## 4. The four checks — HONEST status

| # | Check | Code + tests | Proven on a REAL doc? | Real finding |
|---|-------|--------------|-----------------------|--------------|
| 1 | **Markup / O&P cap** | done | **YES** | RWC CO#4: $85.46 O&P + $76.19 bond (vs form); $1,002.52 + $1,320.32 + $76.19 (vs 00410 contract) |
| 2 | **Unit-price inflation** | done | **YES** | Baxter CO#3: $900 (Aggregate Base CL 5, $45/CY billed vs $35/CY awarded bid rate) |
| 3 | **Labor rate** | done | **YES** | RWC CO#4 Dinelli T&M tags: $127.61 (p53, 7 hr) + $145.84 (p62, 8 hr) = $273.45 |
| 4 | **Material qty vs drawing** | done | **HALF** | Engine + DB are real (Kingston: 147 doors, 9 columns); CO side (`co_doors.txt`) is synthetic |

### What is genuinely proven on real data

- **Check 1 — RWC CO#4** vs own form: `$85.46` O&P overcharge + `$76.19` bond overcharge.
- **Check 1 — RWC CO#4** vs 00410 contract: `$1,002.52` + `$1,320.32` + `$76.19` (the 15%
  markups the form "allows" violate the contract's 10% cap).
- **Check 1 — RWC CO#1**: 0 findings, correctly clean.
- **Check 1 — Otsego CO**: 0 findings, correctly clean (its arithmetic ties out).
- **Check 2 — Baxter CO#3** vs Pratt's own awarded bid: **`$900`** (Aggregate Base CL 5,
  `$45/CY` billed vs the `$35/CY` the contractor itself bid). Reference hand-extracted
  from the official multi-column bid tabulation into `baxter_contract_rates.txt`.
- **Check 3 — RWC CO#4** vs Dinelli's own attested rates: **`$273.45`** — journeyman
  billed `$152.35/hr` vs contract `$134.12/hr`, caught on p53 (7 hr) and p62 (8 hr).
  Self-contained: the rate reference (`rwc_contract_rates.txt`) is built from the labor-
  burden sheet inside CO#4's own 85-page package.
- **Check 4 engine** — driven live: fresh build of the raw Kingston 89-sheet PDF
  (`100643PLANSC.pdf`) yields the same counts as the pre-built `demo.db` (147 doors, 314
  panels, 130 mixed_schedule, 19 penetration, 9 columns, 318 grids, 3 rooms). Catches
  $5,460 + $5,550 against the synthetic `co_doors.txt`.

### What is still synthetic (limitations, not bugs — see §7)

- Check 3 **padded extension** (hrs × rate ≠ printed ext): only synthetic. Every real
  CO we hold reflows its dollar column, so the tool deliberately sets `ext=None` and
  never ties out against a reflowed figure (see §5.1). This is not a code gap — it's
  sourcing (see §7).
- Check 4 **CO side**: the taken-off DB is real but the CO billing counted items
  (`co_doors.txt`) is synthetic. Real COs bill lump sums and scope, not "N doors" —
  we tried four real COs this session (RWC, Baxter, Orlando airport BP-S00198, SSM
  WWTP, Warren Central electrical) and none bills counted objects. This is a real
  finding about the product's addressable market (see §7).
- Stacking (32.25% effective across 2 tiers) — synthetic fixture only.

---

## 5. Three hard lessons already baked in (do NOT regress these)

### 5.1 Column-offset reflow

Real COs (Otsego, Baxter, Dinelli T&M tags) print the `$` amount in a column that
pdftotext linearises OUT OF ROW with the description — the ext on a text line often
belongs to the neighbouring line. Tying out against it FABRICATES ~10 false
discrepancies on a doc whose math is correct. The inline parser therefore
**recomputes ext from qty × rate** (both reliably on the same line) and NEVER trusts
the printed ext. See `parse_inline()` — `ext` is always `None` by design. Arithmetic
tie-out only runs on clean single-row COLUMN format (e.g. an Excel/CSV export).

The T&M-tag reader (`TM_LABOR`) and the numbered-pay-item reader (`CO_ITEM`) both
follow this rule: they read only rate and quantity from the line, ignore the offset
`$ext` column, and let the caller recompute.

### 5.2 Coverage-aware summary

A bare "0 findings" is dangerous — it can mean "clean" OR "no reference doc was
supplied so we couldn't look." The summary now prints
`0 findings - but only N of 4 checks had the data to run` plus a
`[checked] / [NOT CHECKED] + reason` breakdown per check. **Never revert to a bare count.**

### 5.3 Takeoff engine needs BOTH `build` and `schedules --auto`

The drawing-takeoff engine has two stages. `build` reads tag-callouts (medium
confidence, e.g. columns tagged C-1, C-2). `schedules --auto` reads schedule tables
(high confidence, e.g. the DOOR SCHEDULE giving all 147 doors). **`build` alone
silently loses whole object types** — a fresh build of the Kingston set found 9
columns but missed all 147 doors, because doors live only in the schedule.
`co_audit.py`'s `build_takeoff_db()` now runs both. The schedules pass is slow
(~30s/large sheet, so ~10 min on the 89-sheet Kingston set) but only runs once per
drawing PDF; the resulting `.db` is reused instantly on future audits. For fast
repeated audits, pre-build the `.db` once with the engine and hand that to `co_audit`
as arg 3.

---

## 6. Known limitations (by design, documented)

### 6.1 Scanned CO handling — Claude-vision-native, no OCR, no API

A scanned page has no text layer. The tool does NOT OCR it and does NOT call the
Anthropic API. Because this runs inside **Claude Code**, Claude reads the rendered
page image natively (the same as a pasted screenshot) and writes the text to a
sidecar file. Flow:

1. `co_audit.py` detects scanned pages (`scanned_pages()`), renders each to
   `<co-name>.scan/pNN.png` via `pdftoppm` (`render_scan_pages()`), and prints an
   instruction telling Claude to transcribe them.
2. Claude reads each `pNN.png` and writes its text to `<co-name>.scan/pNN.txt`.
3. Re-run: `load()` merges each `pNN.txt` into that page and returns the page
   numbers as **scan-derived** (in `co_ocr`).
4. The SAME deterministic checks run — but every finding on a scan-derived page is
   printed with `[SCAN - verify against source page N]` (the page number is
   included so the PM knows which image to compare), plus a top note and a summary
   caveat. A picture-read number is NEVER presented with the same certainty as a
   vector-text number.

A mostly-scanned PDF (>20 pages or >half blank) is still refused up front —
transcribing that many by image is impractical; request the native PDF.

Proven by 5 regression tests using `work/co_fixtures/scan_co.txt` +
`scan_co.scan/p02.txt` (a blank p2 whose labor tag exists only in the sidecar): the
$127.61 overcharge is caught AND flagged `[SCAN - verify against source page 2]`.

The old rapidocr `ocr_scanned=True` path was **removed** — OCR misreads digits, which
a pricing tool must not launder into false certainty.

### 6.2 Check 4 counts vs volumes

The takeoff engine counts objects (EA — doors, columns). It cannot settle a
volume/area (CY concrete, SF) — those need a dimensional takeoff. The tool compares
only when units agree and says so otherwise.

### 6.3 Reference-doc sourcing

Public unit-price schedules (state DOTs) and prevailing-wage tables are mostly
paywalled or JS-gated. The realistic source is the **CO's own backup** (that's where
the Dinelli labor rates came from) or the project's own bid tabulation (that's how
`baxter_contract_rates.txt` was built — extract the awarded column, drop lump-sum
items, normalise units).

---

## 7. Where to start next (pending work — THIS IS THE REAL REMAINING JOB)

Three items in priority order:

### 7.1 Check 4 on a real CO (highest value)

The engine, the fresh-build integration, and the real Kingston takeoff DB all work.
The single missing piece is **a real CO that itemises counted objects** (doors,
fixtures, columns) for a building whose drawings we have. This is genuinely rare —
we tried four real COs this session and none bills counted objects. Real COs bill
lump sums, labor hours, and material unit prices; Check 4's document shape is
uncommon in the wild.

Two paths:

- **Path A** — get a real CO for the Kingston HS building (which is what `demo.db`
  is built from). Kingston City School District uses BoardDocs (same source as our
  real RWC docs). Their real COs (from contractors Rycon and Moses Electrical) are
  reachable but bill lump-sum scope, not counted objects. Worth another look for a
  door-hardware or lighting-replacement CO that might count items.
- **Path B** — pair the Holabird Academy drawings already on disk (see §2) with any
  real CO for that project. Holabird has DOOR SCHEDULE (7), COLUMN SCHEDULE (1),
  EQUIPMENT SCHEDULE (8), LIGHTING FIXTURE SCHEDULE (1) — all engine-countable.
  Ideal Check 4 target if you can source a matching CO.
- **Path C** — reframe Check 4. The takeoff skill already ships a revision-diff
  feature (`SET_revC.pdf → SET_revD.pdf`). The realistic owner-side check might not
  be "count the doors billed" but "the CO says it added 6 doors — does the drawing
  revision actually show that delta?" That matches how real COs work.

### 7.2 Check 3 arithmetic tie-out on real data

Blocked by sourcing, not code. Every real CO we hold reflows its dollar column, so
the tool correctly sets `ext=None` and won't tie out against it. A real tie-out
catch needs a CO in clean single-row COLUMN format (Excel/CSV export) with a
genuine math error — rare, because COs are built in spreadsheets where `ext=qty*rate`
is a formula that always ties even when the rate/qty is inflated. Probed all real
docs (see `labor_items` / `material_items`): zero same-line ext values exist.
Forcing this would mean fabricating a doc — do not.

Most realistic fix: use a CO from Hamza's own AutoConst pipeline (clean Excel line
items) paired with its own contract/drawing.

### 7.3 Repo + README + requirements.txt

- This tool should join the three RFP tools on GitHub with a **README written for
  the PM persona** (not a developer) and a **SKILL.md**. Non-technical users: clone
  → drop CO + contract + drawing in a folder → run.
- `requirements.txt` must include `openpyxl` (Excel export, used by the RFP
  compliance_matrix), `pymupdf` (takeoff engine), and note `poppler` (`pdftotext` /
  `pdftoppm`) as a system dependency.

---

## 8. What Claude added this session (2026-07-23)

Full history in git; here is what the current code contains that was NOT in the
previous handoff:

1. **Check 3 real** — `TM_LABOR` regex reads the classification-first T&M-tag
   format (`JOURNEYMAN  $152.35  7.00`, rate before hours, no `HR` token, ext in
   offset column). `best_contract_match()` uses containment scoring so a bare
   `JOURNEYMAN` line ties to the contract's `Journeyman Plumber` entry (Jaccard
   scored 0.5 and missed it). Applied to both Check 2 and Check 3. Prefers the
   closest match (fewest extra tokens) so `Journeyman` ties to straight-time, not
   overtime — the conservative pick.
2. **Check 2 real** — `CO_ITEM` regex, a numbered-pay-item fallback for civil COs
   where the unit-of-measure reflows onto a neighbouring line. `Aggregate Base, CL 5`
   from Pratt's awarded bid tab ties to `AGGREGATE BASE CLASS 5` on the CO via
   containment matching. Lump-sum items in the CO correctly match nothing and stay
   silent (no false positives).
3. **Check 4 engine wired end-to-end** — `build_takeoff_db()` now runs BOTH the
   engine's `build` step (tag counts, medium confidence) AND `schedules --auto`
   (schedule tables, high confidence). Previously it only ran `build` and silently
   missed doors/panels. Live-verified against the raw 89-sheet Kingston PDF.
4. **Scanned-CO handling — Claude-vision-native** — no OCR, no API. See §6.1.
   Added `scanned_pages()`, `scan_dir_for()`, `render_scan_pages()`, `scanflag()`.
   Every scan-derived finding is flagged with the source page number.
5. **New real docs on disk**:
   - `~/Downloads/co_audit_docs/baxter_co3.pdf` + `.txt` (real Check 2 target)
   - `~/Downloads/co_audit_docs/baxter_bidtab.pdf` + `.txt` (multi-column bid tab)
   - `~/Downloads/co_audit_docs/baxter_contract_rates.txt` (Pratt's awarded column,
     hand-extracted, lump-sum items omitted)
   - `~/Downloads/co_audit_docs/rwc_co_roycloud.pdf` + `.txt` (Fremont Millworks
     casework CO — another Check 1 example)
6. **Test count** 46 → **62 tests, all green**. New blocks: real Check 2 (Baxter),
   real Check 3 (RWC Dinelli), scan handling.

---

## 9. Sibling tools in this repo (context, already built)

- `work/amendment_diff.py`   - diffs a federal solicitation vs its SF30 amendments.
- `work/compliance_matrix.py`- extracts binding requirements → formatted .xlsx.
- `work/lm_crosswalk.py`     - maps Section L instructions to Section M eval factors.
- `work/run_tests.sh`        - 34-test suite for the three RFP tools (separate from
                               run_co_tests.sh).

The CO audit tool is the 4th tool, built for a different (owner-side) persona.
