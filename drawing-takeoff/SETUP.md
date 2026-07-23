# Drawing Takeoff — Setup & Worked Example

Turn a construction PDF into a queryable database, then answer takeoff questions
from the database instead of feeding the raw drawings to Claude every time.

---

## 1. Requirements

- **Python 3.10+** (tested on 3.14). On Windows use the `py` launcher; on
  macOS/Linux use `python3`.
- **PyMuPDF** — the only dependency. It reads the hidden vector text layer.

```bash
py -m pip install -r requirements.txt      # Windows
python3 -m pip install -r requirements.txt # macOS/Linux
```

Verify:
```bash
py -c "import fitz; print(fitz.__doc__)"
```

## 2. Install as a Claude Code skill (optional)

Copy the whole folder into your skills directory so Claude picks it up:

```
~/.claude/skills/drawing-takeoff/        (global)
# or
<project>/.claude/skills/drawing-takeoff/  (per project)
```

The folder already contains `SKILL.md` (the trigger + instructions) and
`scripts/`. Once installed, ask Claude things like *"how many doors in this
set?"* and it will build/query the database instead of reading the PDF.

You can also run everything by hand — the skill is just a wrapper around the CLI.

## 3. Commands

```
py scripts/takeoff.py build      SET.pdf --db set.db          # once, fast; also writes drawing.md
py scripts/takeoff.py schedules  SET.pdf --db set.db --auto   # opt-in, slow; high-confidence counts
py scripts/takeoff.py summary    --db set.db                  # regenerate drawing.md
py scripts/takeoff.py check      --db set.db                  # re-run sanity checks
py scripts/takeoff.py query      count            --db set.db
py scripts/takeoff.py query      count --type door --db set.db
py scripts/takeoff.py query      list  --type door --db set.db  # each door + attributes + sheet
py scripts/takeoff.py query      sheets           --db set.db
py scripts/takeoff.py query      dims --sheet A101 --db set.db
py scripts/takeoff.py query      flags            --db set.db
py scripts/takeoff.py query      sql "SELECT ..." --db set.db
```

### drawing.md — the one-page index

`build` and `schedules` write **`drawing.md`** next to the database. It's a short
markdown summary of the whole set — object totals and, per sheet, the discipline,
scale, and what's on it (e.g. `A601 | 147 door, 330 dims`). Read it first on any
question so you go straight to the right sheet instead of scanning the PDF. It's
a few KB, not 49 MB.

---

## 4. Worked example (real 89-page architectural set)

```
$ py scripts/takeoff.py build BlissHall.pdf --db bliss.db
Built bliss.db
  pages:            89
  words stored:     91078
  objects (tags):   327
  measurements:     2346
```

Build reads every sheet's text in ~30s. Now add the high-confidence door and
panel counts from their schedule sheets:

```
$ py scripts/takeoff.py schedules BlissHall.pdf --db bliss.db --pages 12,56
  page 12 (A601): 147 rows
  page 56 (E601): 47 rows
Added 194 high-confidence schedule objects
```

Query it — each answer is a couple hundred bytes, not a 49 MB PDF:

```
$ py scripts/takeoff.py query count --db bliss.db
type          total   high medium
grid            318      0    318
door            147    147      0
panel            47     47      0
column            9      0      9

$ py scripts/takeoff.py query dims --sheet A101 --db bliss.db --limit 3
sheet     raw           metres  conf   sanity
A101      218'-1/4"      66.45  high   ok
A101      46'-0"         14.02  high   ok
A101      40'-11"        12.47  high   ok
```

Objects aren't just counted — each carries its schedule attributes and source
sheet, so you can list them the way the drawings describe them:

```
$ py scripts/takeoff.py query list --type door --db bliss.db --limit 2
110   [A601] high  Room=CLASSROOM; Panel Width=3' - 0"; Panel Height=7' - 10 1/2"; Material=HM
115   [A601] high  Room=SERVICE/STORAGE; Door Panel Type=A; Panel Width=3' - 0"; Material=HM
```

### The sanity check in action

Counts come from schedule rows, so they're **high** confidence. A distance scaled
off the drawing is **low** confidence and gets checked against the building
envelope (the 98th-percentile of the printed dimensions, ~77 m here). Feed it a
duct run that scaled to 500 m and it's flagged instead of silently trusted:

```
$ py scripts/takeoff.py query flags --db bliss.db
[WARN] M201 scaled = 500.00m  -> 500.0m > 1.5x building envelope 76.8m
```

That is the difference between a number you can bid on and one you can't.

---

## 5. Confidence model (be honest with every number)

| Source                         | Confidence | Why |
|--------------------------------|------------|-----|
| Schedule row                   | **high**   | literal row in a titled schedule table |
| Printed dimension (`218'-1/4"`)| **high**   | reading a number the designer wrote |
| Tag callout on a plan          | **medium** | pattern match, depends on labelling |
| Distance scaled from pixels    | **low**    | visual estimate; must pass sanity check |

## 6. Tuning per client set (`scripts/patterns.py`)

Different offices tag drawings differently. Everything drawing-specific lives in
one file:

- `TAG_PATTERNS` — regex for how doors/windows/columns/grids are labelled on
  plans. **Edit these first** if plan counts look wrong.
- `SCHEDULE_KEYWORDS` — words in a sheet title that identify a schedule and its
  object type.
- `DISCIPLINE_MAP`, `METRIC_SCALE_RE`, `IMPERIAL_SCALE_RE` — sheet-number and
  scale conventions.
- `MAX_PLAUSIBLE_FEET` — the ceiling for a single dimension before it's treated
  as a glued reference number rather than a real length.

After editing, rebuild (`build`) and re-run `check`.

## 7. Known limits (so you don't over-trust it)

- **Scanned / image-only PDFs have no vector text** — this workflow needs the
  text layer. If `words stored` is ~0, the set was scanned; you'd need OCR first.
- **Tag counts are only as good as the regexes.** Verify against one sheet.
- Pages with several different schedules are typed by priority and labelled
  `mixed_schedule` in a note — treat those counts as needing a human glance.
- True pixel-measuring of unlabelled distances is deliberately not automated;
  prefer the printed dimensions, which are what this tool extracts.
