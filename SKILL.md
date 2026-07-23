---
name: co-audit
description: Audit a contractor's change-order (CO) proposal PDF for pricing abuse — inflated markups, stacked O&P, unit-price inflation, padded labor rates, and quantities that don't tie to the drawing. Owner / PM-side tool. Use when a user asks to "audit", "check", or "review" a change order, CO, CO#, T&M tag, extra-work billing, or contractor's proposed pricing against a contract, rate schedule, bid tab, or drawing set. Every finding is a dollar figure with a page anchor — deterministic, not an opinion.
---

# Change-Order Pricing Audit

A PM normally spends 1–3 hours per change order cross-checking it against the
contract's rate schedule and the drawings — hunting for inflated markups,
stacked O&P, padded labor, and quantities that don't tie to the drawing. This
skill does that in seconds and returns dollar figures with page anchors.

**Deterministic.** Every finding is a $ overcharge tied to a page. Judgment
calls ("is 42 hrs reasonable for this scope?") are surfaced for a human,
never ruled on — for this persona, a wrong "this is padded" costs more trust
than staying quiet.

## What it checks

| # | Check | Needs |
|---|-------|-------|
| 1 | **Markup / O&P / bond cap** (incl. stacking across tiers) | CO alone; contract exhibit sharpens it |
| 2 | **Unit-price inflation** vs contract / bid tab | CO + rate schedule or awarded bid tab |
| 3 | **Labor-rate inflation** (column, inline `L=qty×rate=ext`, and T&M classification tags) | CO + labor rate schedule |
| 4 | **Material quantity vs drawing** | CO + drawing PDF **or** pre-built takeoff `.db` |

## How to run it

```
py co_audit.py <change-order.pdf> [contract-or-rates] [drawing.pdf | takeoff.db | takeoff.txt]
```

Run from the repo root (where `co_audit.py` sits).

- **arg 1** (required): CO proposal PDF (or a pre-extracted `.txt`).
- **arg 2** (optional): contract exhibit / rate schedule / bid tab. Powers
  Checks 2 and 3 and sharpens Check 1. Pass `""` to skip.
- **arg 3** (optional): drawing PDF (built into a takeoff DB automatically),
  a pre-built `.db`, or a takeoff `.txt` export. Powers Check 4.

The tool prints a **coverage-aware summary**: never a bare "0 findings" —
always `0 findings — but only N of 4 checks had the data to run` with a
`[checked] / [NOT CHECKED] + reason` breakdown per check. If it says a check
wasn't run, that's a sourcing gap, not a clean bill of health.

## How to use this as Claude

When a user asks to audit / check / review a change order:

1. Locate the three inputs in the current folder (some may be missing —
   that's fine, the tool reports coverage). The user typically drops the
   CO, the contract/rates, and the drawing set into one folder and says
   "audit this". Ask only for inputs you can't find.
2. Run `py co_audit.py <co> <contract> <drawing>` with what's available.
   Do not paraphrase or re-derive findings from the PDF text yourself —
   the point of the tool is that its numbers are anchored and reproducible.
3. Report each finding as **$amount, page N, one-line reason**. If the
   summary says a check wasn't run, say so — do not imply the CO is clean.
4. If a finding is marked `[SCAN - verify against source page N]`, pass
   that flag through verbatim — it means the number came from an image
   read, not vector text, and the PM must eyeball the source page.

## The drawing-takeoff engine is bundled

Check 4 shells out to the `drawing-takeoff` skill's engine, which ships
inside this repo at `drawing-takeoff/scripts/takeoff.py`. `co_audit.py`
finds it automatically — no path setup. On first build it runs BOTH
`build` (tag callouts, medium confidence) AND `schedules --auto` (schedule
tables, high confidence). The schedules pass is slow (~10 min on an
89-sheet set) but the resulting `.db` is cached and reused instantly.

## Three hard rules baked in — do not work around them

### 1. Never trust the printed `$ext` column on a text-linearised PDF

Real COs print the `$` amount in a column that `pdftotext` linearises OUT
OF ROW with the description — the ext on a text line often belongs to the
neighbouring line. Tying out against it fabricates ~10 false discrepancies
on a doc whose math is correct. The parsers read only rate and quantity
from the line and let the tool recompute. If you find yourself wanting to
"just check the total", **stop** — the reflow guard is why the tool doesn't
lie on Otsego (clean-math CO, 0 findings).

### 2. Scanned pages are Claude-vision-native — no OCR, no API

If the CO has scanned pages, the tool renders them to
`<co-name>.scan/pNN.png` and prints an instruction. **You (Claude) read
each PNG and write the transcription to `<co-name>.scan/pNN.txt`.** Then
re-run the audit — it merges the sidecars and runs the same checks, but
flags every finding on a scan-derived page with `[SCAN - verify against
source page N]`. A picture-read number must never be presented with the
same certainty as vector text.

A mostly-scanned PDF (>20 pages or >half blank) is refused up front —
request the native PDF.

### 3. Coverage-aware summary — "0 findings" is never a clean bill

Always read the `[checked] / [NOT CHECKED]` breakdown out to the user.
A CO with no rate schedule supplied can print 0 findings and still be
riddled with unit-price inflation — the tool just couldn't see it.

## Sanity table — real catches to verify against

If a fresh install produces different numbers on these, something is
wrong:

| Doc | Check | Expected finding |
|-----|-------|------------------|
| `rwc_changeorder4.pdf` alone | 1 | $85.46 O&P + $76.19 bond |
| `rwc_changeorder4.pdf` + `00410_pricing_change_orders.pdf` | 1 | $1,002.52 + $1,320.32 + $76.19 |
| `baxter_co3.pdf` + `baxter_contract_rates.txt` | 2 | $900 (Aggregate Base CL 5) |
| `rwc_changeorder4.pdf` + `rwc_contract_rates.txt` | 3 | $127.61 (p53) + $145.84 (p62) = $273.45 |
| `rwc_changeorder1.pdf` | 1 | 0 findings (clean) |
| `otsego_co.pdf` | any | 0 findings (clean; reflow guard is doing its job) |

Regression suite: `bash run_co_tests.sh` from the repo root. 62 tests,
all green.

## Requirements

- Python 3.10+
- Poppler on PATH (`pdftotext`, `pdftoppm`)
- `pip install -r requirements.txt` (installs PyMuPDF for the bundled
  drawing-takeoff engine)

See `CO_AUDIT_HANDOFF.md` for the full design rationale, limitations,
and remaining work.
