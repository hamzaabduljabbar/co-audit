REVISION DIFF - TEST FIXTURE
============================

Real revised drawing sets are project-confidential - nobody uploads both the
old and the new issue. So these two PDFs are a controlled test fixture: a small
3-sheet structural set at two revisions, with a KNOWN list of changes between
them, so the diff engine can be checked against ground truth.

  SET_revC.pdf     Revision C (the "before")
  SET_revD.pdf     Revision D (the "after")
  make_revisions.py  The generator. The docstring lists the 7 changes it bakes
                     in - that is the answer key.
  diff-report.json The machine-readable output of drawdiff on this pair.
  diff-output.svg  A snapshot of the terminal report.

The 7 changes from Rev C -> Rev D (all 7 are correctly detected):
  1. Footing F10 size   600x600 -> 750x750
  2. Footing F12 depth  500 -> 600
  3. Footing F15        added
  4. Footing F09        removed
  5. Grid dimension     24'-0" -> 26'-0"  (sheet S-01)
  6. General note 4     bearing capacity 150 kPa -> 200 kPa
  7. Door D-03          added (opening schedule)

Reproduce it:
  py example/revisions/make_revisions.py         # regenerate the two PDFs
  py scripts/drawdiff.py SET_revC.pdf SET_revD.pdf
