"""
Generate two revisions (Rev C and Rev D) of a small synthetic structural set,
with a KNOWN list of changes between them so the diff engine can be verified
against ground truth. Not a real project - a controlled test fixture.

Changes baked in from Rev C -> Rev D (this is the answer key):
  1. Footing F10 size 600x600 -> 750x750  (schedule attribute change)
  2. Footing F12 depth 500 -> 600           (schedule attribute change)
  3. Footing F15 ADDED                       (new schedule row / object)
  4. Footing F09 REMOVED                      (deleted schedule row / object)
  5. Grid dimension 24'-0" -> 26'-0"          (printed dimension change)
  6. General note 4 text changed             (note revision)
  7. Door D-03 ADDED to opening schedule      (new object, different type)
"""
import fitz  # PyMuPDF

HELV = "helv"
HELV_B = "hebo"


def _titleblock(page, sheet_no, title, rev):
    w = page.rect.width
    h = page.rect.height
    # border
    page.draw_rect(fitz.Rect(20, 20, w - 20, h - 20), color=(0, 0, 0), width=1)
    # title block bottom-right
    tb = fitz.Rect(w - 260, h - 110, w - 20, h - 20)
    page.draw_rect(tb, color=(0, 0, 0), width=1)
    page.insert_text((w - 250, h - 88), "AUTOCONST STRUCTURAL", fontname=HELV_B, fontsize=9)
    page.insert_text((w - 250, h - 70), title, fontname=HELV, fontsize=8)
    page.insert_text((w - 250, h - 45), f"SHEET  {sheet_no}", fontname=HELV_B, fontsize=11)
    page.insert_text((w - 250, h - 30), f"REV  {rev}", fontname=HELV_B, fontsize=11)


def _foundation_plan(page, grid_dim, rev):
    _titleblock(page, "S-01", "FOUNDATION PLAN", rev)
    page.insert_text((40, 60), "FOUNDATION PLAN", fontname=HELV_B, fontsize=14)
    page.insert_text((40, 80), "SCALE: 1:100", fontname=HELV, fontsize=9)
    # a couple of grid bubbles + a dimension line
    page.insert_text((120, 140), "1", fontname=HELV_B, fontsize=10)
    page.insert_text((360, 140), "2", fontname=HELV_B, fontsize=10)
    # printed dimension between grids (this changes between revs)
    page.insert_text((225, 160), grid_dim, fontname=HELV, fontsize=10)
    page.insert_text((120, 300), "8'-6\"", fontname=HELV, fontsize=10)
    page.insert_text((360, 300), "12'-0\"", fontname=HELV, fontsize=10)
    # footing tags on plan
    page.insert_text((130, 220), "F10", fontname=HELV, fontsize=9)
    page.insert_text((370, 220), "F12", fontname=HELV, fontsize=9)


def _footing_schedule(page, rows, rev):
    _titleblock(page, "S-02", "FOOTING SCHEDULE", rev)
    page.insert_text((40, 60), "FOOTING SCHEDULE", fontname=HELV_B, fontsize=14)
    # header
    x = [40, 130, 260, 360, 520]
    y = 110
    headers = ["MARK", "SIZE", "DEPTH", "REINFORCEMENT", "CONCRETE"]
    for xi, hdr in zip(x, headers):
        page.insert_text((xi, y), hdr, fontname=HELV_B, fontsize=9)
    page.draw_line(fitz.Point(40, y + 6), fitz.Point(700, y + 6), width=0.8)
    yy = y + 26
    for r in rows:
        for xi, val in zip(x, r):
            page.insert_text((xi, yy), val, fontname=HELV, fontsize=9)
        yy += 22


def _opening_schedule(page, doors, rev):
    _titleblock(page, "S-03", "OPENING SCHEDULE & NOTES", rev)
    page.insert_text((40, 60), "OPENING SCHEDULE", fontname=HELV_B, fontsize=14)
    x = [40, 130, 300, 480]
    y = 110
    for xi, hdr in zip(x, ["MARK", "SIZE", "TYPE", "REMARKS"]):
        page.insert_text((xi, y), hdr, fontname=HELV_B, fontsize=9)
    page.draw_line(fitz.Point(40, y + 6), fitz.Point(700, y + 6), width=0.8)
    yy = y + 26
    for d in doors:
        for xi, val in zip(x, d):
            page.insert_text((xi, yy), val, fontname=HELV, fontsize=9)
        yy += 22
    # general notes
    page.insert_text((40, 360), "GENERAL NOTES", fontname=HELV_B, fontsize=12)
    ny = 384
    for i, note in enumerate(NOTES[rev], 1):
        page.insert_text((40, ny), f"{i}. {note}", fontname=HELV, fontsize=9)
        ny += 20


# ---- Rev C data ----
FOOTINGS_C = [
    ["F09", "450x450", "400", "6-T12 E/W",    "25 MPa"],
    ["F10", "600x600", "500", "8-T16 E/W",    "32 MPa"],
    ["F12", "900x900", "500", "10-T20 E/W",   "32 MPa"],
]
DOORS_C = [
    ["D-01", "0.9x2.1", "STEEL", "EXTERNAL"],
    ["D-02", "0.9x2.1", "TIMBER", "INTERNAL"],
]

# ---- Rev D data (with the 7 known changes applied) ----
FOOTINGS_D = [
    # F09 removed
    ["F10", "750x750", "500", "8-T16 E/W",    "32 MPa"],   # size changed
    ["F12", "900x900", "600", "10-T20 E/W",   "32 MPa"],   # depth changed
    ["F15", "1200x1200", "700", "12-T25 E/W", "40 MPa"],   # added
]
DOORS_D = [
    ["D-01", "0.9x2.1", "STEEL", "EXTERNAL"],
    ["D-02", "0.9x2.1", "TIMBER", "INTERNAL"],
    ["D-03", "1.2x2.1", "STEEL", "FIRE RATED 60MIN"],       # added
]

NOTES = {
    "C": [
        "ALL CONCRETE TO BE 32 MPa UNLESS NOTED OTHERWISE.",
        "COVER TO REINFORCEMENT 40mm TO EARTH FACES.",
        "DO NOT SCALE FROM DRAWINGS.",
        "FOUNDATIONS DESIGNED FOR BEARING CAPACITY 150 kPa.",
    ],
    "D": [
        "ALL CONCRETE TO BE 32 MPa UNLESS NOTED OTHERWISE.",
        "COVER TO REINFORCEMENT 40mm TO EARTH FACES.",
        "DO NOT SCALE FROM DRAWINGS.",
        "FOUNDATIONS DESIGNED FOR BEARING CAPACITY 200 kPa.",  # changed 150->200
    ],
}


def build(path, grid_dim, footings, doors, rev):
    doc = fitz.open()
    p1 = doc.new_page(width=760, height=560)
    _foundation_plan(p1, grid_dim, rev)
    p2 = doc.new_page(width=760, height=560)
    _footing_schedule(p2, footings, rev)
    p3 = doc.new_page(width=760, height=560)
    _opening_schedule(p3, doors, rev)
    doc.save(path)
    doc.close()
    print("wrote", path)


if __name__ == "__main__":
    build("SET_revC.pdf", "24'-0\"", FOOTINGS_C, DOORS_C, "C")
    build("SET_revD.pdf", "26'-0\"", FOOTINGS_D, DOORS_D, "D")
    print("done")
