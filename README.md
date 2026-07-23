# Change-Order Pricing Audit

**Audit a contractor's change-order proposal against the contract's rate schedule and the drawings — in seconds instead of 1–3 hours per CO.**

Owner / PM-side tool. A contractor sends a change order PDF; a PM normally opens the CO, opens the contract's rate schedule, opens the drawing, and cross-checks line-by-line in Excel — hunting for inflated markups, stacked O&P, padded labor rates, and quantities that don't tie to the drawing. This tool does that pass deterministically and returns dollar figures with page anchors.

It ships as a [Claude Code](https://docs.claude.com/en/docs/claude-code) skill, but the engine is a plain Python CLI you can run on its own.

---

## Why

Every finding is a **$ overcharge tied to a page** — not an opinion. Judgment calls ("is 42 hours reasonable for this scope?") are surfaced for a human, never ruled on. For this persona a wrong "this is padded" costs more trust than staying quiet.

Three problems every honest CO auditor has to handle, baked in by design:

| Problem | How the tool handles it |
|---|---|
| **Column reflow.** `pdftotext` prints the `$ext` column out-of-row from the description; naïve tie-out fabricates ~10 false discrepancies on a clean doc. | Parsers read only rate and quantity from the line and recompute — the printed `$ext` column is never trusted. |
| **Silent invalidity.** "0 findings" can mean the CO is clean OR that no reference doc was supplied. | Coverage-aware summary: `0 findings — but only N of 4 checks had the data to run`, with a per-check `[checked] / [NOT CHECKED] + reason` breakdown. |
| **Scanned pages.** A number read from an image must never carry the same certainty as vector text. | Scan pages are rendered to PNG, Claude transcribes them into a sidecar `.txt`, the same checks run — but every finding on a scan-derived page is stamped `[SCAN - verify against source page N]`. |

---

## Install

Requires **Python 3.10+**, **Poppler** (`pdftotext`, `pdftoppm`), and one library:

```bash
pip install -r requirements.txt      # PyMuPDF, for the bundled takeoff engine
```

Poppler:
- **Windows** — `choco install poppler` (or download the binary and add to PATH)
- **macOS** — `brew install poppler`
- **Linux** — `apt install poppler-utils`

On Windows use the `py` launcher; on macOS/Linux use `python3`.

### As a Claude Code skill (recommended)

Clone the repo into your skills directory so Claude uses it automatically:

```
~/.claude/skills/co-audit/            (global)
<project>/.claude/skills/co-audit/    (per project)
```

Drop the CO, the contract, and the drawing set into one folder, open Claude Code there, and say *"audit this change order"* — Claude reads `SKILL.md`, runs the audit, and reports the overcharges.

---

## Quickstart

```bash
py co_audit.py <change-order.pdf> [contract-or-rates] [drawing.pdf | takeoff.db]
```

- **arg 1** (required): CO proposal PDF.
- **arg 2** (optional): contract exhibit, rate schedule, or bid tab. Powers Checks 2 and 3, sharpens Check 1. Pass `""` to skip.
- **arg 3** (optional): drawing PDF (built into a takeoff DB automatically) or a pre-built `.db`. Powers Check 4.

That's the whole interface.

---

## The four checks

| # | Check | Catches |
|---|---|---|
| 1 | **Markup / O&P / bond cap** | Overhead, profit, and bond above the contract's allowed percentages. Also catches *stacking* — sub-15% × GC-15% = 32.25% effective across two tiers. Reads caps from three sources in priority order: config → CO form → contract exhibit. If the contract is stricter than what the form prints, the form's cap is itself flagged as a violation. |
| 2 | **Unit-price inflation** | Each billed line-item is matched to the contract's agreed unit rate via description-token containment matching (tolerant of `CLASS 5` vs `CL 5`). Anything billed above is flagged with the overcharge in dollars. |
| 3 | **Labor-rate inflation** | Labor lines read in three formats: column layout, inline `L = qty × rate = ext`, and T&M classification tags (`JOURNEYMAN $152.35 7.00`). Rates billed above the contract's labor rates are flagged. When no rate reference is supplied, `hours × rate × total` is surfaced for human review. |
| 4 | **Material quantity vs drawing** | Drives the bundled [drawing-takeoff](drawing-takeoff/) engine: builds the drawing into a queryable SQLite DB (schedule tables at high confidence, tag callouts at medium) and flags when the CO bills more than the drawing shows. |

---

## Verified results (real change orders)

Every row below is a real overcharge caught on a real document. All six are locked in as regression tests.

| Doc | Check | Caught |
|---|---|---|
| Redwood City School District CO#4 | 1 | **$85.46** O&P + **$76.19** bond over the form's own cap |
| RWC CO#4 vs 00410 university contract | 1 | **$1,002.52** + **$1,320.32** + **$76.19** — the 15% markups the form allows violate the contract's 10% cap |
| City of Baxter MN CO#3 | 2 | **$900** — Aggregate Base CL 5 billed $45/CY vs Pratt's own $35/CY bid |
| Dinelli Plumbing T&M tags inside RWC CO#4 | 3 | **$273.45** — journeyman billed $152.35/hr vs contract $134.12/hr (p53 + p62) |
| Kingston HS drawings (real takeoff DB) | 4 | **$5,460** (160 doors billed vs 147 real) + **$5,550** (12 columns vs 9) |

It also correctly stays silent on clean documents:

| Doc | Result |
|---|---|
| Redwood City CO#1 | **0 findings** — correctly clean, at-cap |
| Otsego WWTP CO | **0 findings** — inline arithmetic ties out; the reflow guard prevents the ~10 false discrepancies a naïve tie-out would produce |

Regression suite: `bash run_co_tests.sh` from the repo root. **62 tests, all green.**

---

## Example output

```
$ py co_audit.py rwc_changeorder4.pdf rwc_contract_rates.txt

Change-order audit: rwc_changeorder4.pdf
Reference:         rwc_contract_rates.txt

[1] Markup / O&P cap
    p2  Overhead & Profit: 15.00% > form cap 10%    overcharge $85.46
    p2  Bond:                1.50% > form cap 0%    overcharge $76.19

[3] Labor-rate inflation (T&M)
    p53 Dinelli JOURNEYMAN  $152.35/hr > contract $134.12/hr    overcharge $127.61 (7.00 hr)
    p62 Dinelli JOURNEYMAN  $152.35/hr > contract $134.12/hr    overcharge $145.84 (8.00 hr)

Total overcharges caught: $435.10

Coverage:
  [1] Markup/O&P cap        [checked]
  [2] Unit-price inflation  [NOT CHECKED — no unit-price schedule in reference]
  [3] Labor-rate inflation  [checked]
  [4] Quantity vs drawing   [NOT CHECKED — no drawing supplied]
```

---

## How the drawing-takeoff engine plugs in

Check 4 shells out to the bundled [`drawing-takeoff/`](drawing-takeoff/) engine — the same skill on its own repo. On first build it runs BOTH:

1. `build` — sheet index, printed dimensions, tag-callout counts (medium confidence).
2. `schedules --auto` — schedule tables on any sheet whose text mentions "schedule" (high confidence). This is what recovers the 147 real doors on the Kingston set.

The schedules pass is slow (~30s per large sheet, so ~10 min on an 89-sheet set) but the resulting `.db` is cached under `outputs/` and reused instantly on future audits. Pre-build once, audit many times.

> **Heads up — first drawing build takes minutes, not seconds.** On a large set (~89 sheets) expect ~10 minutes for the first audit that includes a drawing. Claude will look "stuck" during that time — it isn't; the schedules pass is churning through table detection. The `.db` gets cached under `outputs/` and every subsequent audit of the same building is instant.

`co_audit.py` finds the engine automatically — the bundled path is the first entry in `TAKEOFF_ROOTS`.

---

## Known limits

- **Check 4 needs a CO that itemises counted objects** (doors, fixtures, columns). Most real COs bill lump sums, labor hours, and material unit prices — Check 4's document shape is uncommon in the wild. Checks 1–3 catch the money on the COs you actually see.
- **Check 3 arithmetic tie-out is disabled on real docs.** Every real CO we hold reflows its dollar column, so the parser sets `ext=None` by design and won't tie out against a reflowed figure. This is the reflow guard doing its job — it's why the tool stays silent on Otsego.
- **Public rate schedules are mostly paywalled or JS-gated.** The realistic sources are (a) the CO's own labor-burden backup and (b) the project's own bid tabulation (extract the awarded column, drop lump-sum items).

See [`CO_AUDIT_HANDOFF.md`](CO_AUDIT_HANDOFF.md) for full design rationale, all limitations, and remaining work.

---

## What's in the box

```
co-audit/
├── SKILL.md                    Claude Code skill definition (auto-triggers this tool)
├── co_audit.py                 the auditor (~1120 lines)
├── run_co_tests.sh             62-test regression suite
├── co_fixtures/                synthetic unit-test fixtures
├── CO_AUDIT_HANDOFF.md         full design doc for a fresh Claude session
├── requirements.txt
└── drawing-takeoff/            bundled — powers Check 4
    └── scripts/takeoff.py
```

---

## License

MIT
