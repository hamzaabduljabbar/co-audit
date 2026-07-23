-- Structured store for a construction drawing set.
-- Built once from the PDF; queried many times for pennies of tokens.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sheets (
    page         INTEGER PRIMARY KEY,   -- 0-based page index in the PDF
    sheet_number TEXT,                  -- e.g. A-101, S2.1  (from title block)
    title        TEXT,
    discipline   TEXT,                  -- Architectural, Structural, ...
    scale_label  TEXT,                  -- e.g. 1:100 or 1/4"=1'-0"
    scale_ratio  REAL,                  -- real units per paper unit
    width_pt     REAL,
    height_pt    REAL,
    word_count   INTEGER
);

CREATE TABLE IF NOT EXISTS words (
    id    INTEGER PRIMARY KEY,
    page  INTEGER,
    text  TEXT,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    FOREIGN KEY(page) REFERENCES sheets(page)
);

CREATE TABLE IF NOT EXISTS objects (
    id            INTEGER PRIMARY KEY,
    type          TEXT,     -- door, window, column, grid, beam, ...
    tag           TEXT,     -- the label matched / schedule row id
    page          INTEGER,
    sheet_number  TEXT,
    source_method TEXT,     -- 'schedule' | 'tag_pattern'
    confidence    TEXT,     -- high | medium | low
    attributes    TEXT,     -- JSON of the schedule row: {"Width":"600mm", ...}
    note          TEXT,
    FOREIGN KEY(page) REFERENCES sheets(page)
);

CREATE TABLE IF NOT EXISTS measurements (
    id            INTEGER PRIMARY KEY,
    description   TEXT,
    raw_text      TEXT,
    value_m       REAL,
    page          INTEGER,
    sheet_number  TEXT,
    method        TEXT,     -- 'annotation' | 'scaled'
    confidence    TEXT,     -- high | medium | low
    sanity_status TEXT,     -- ok | warn | fail | unchecked
    sanity_note   TEXT,
    FOREIGN KEY(page) REFERENCES sheets(page)
);

CREATE INDEX IF NOT EXISTS idx_objects_type ON objects(type);
CREATE INDEX IF NOT EXISTS idx_words_page   ON words(page);
CREATE INDEX IF NOT EXISTS idx_meas_status  ON measurements(sanity_status);
