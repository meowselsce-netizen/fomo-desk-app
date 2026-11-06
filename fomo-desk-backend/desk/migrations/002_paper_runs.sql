-- Paper runs are isolated from legacy/demo rows (NULL run_id).
ALTER TABLE scans ADD COLUMN run_id TEXT;
ALTER TABLE audits ADD COLUMN run_id TEXT;
ALTER TABLE verdicts ADD COLUMN run_id TEXT;
ALTER TABLE signals ADD COLUMN run_id TEXT;
ALTER TABLE positions ADD COLUMN run_id TEXT;
ALTER TABLE activity ADD COLUMN run_id TEXT;
ALTER TABLE prices ADD COLUMN run_id TEXT;
ALTER TABLE balance ADD COLUMN run_id TEXT;

CREATE TABLE IF NOT EXISTS paper_runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  kind TEXT NOT NULL, -- paper
  note TEXT
);

CREATE TABLE IF NOT EXISTS opened_events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  mint TEXT NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  ok INTEGER NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS cash_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  mint TEXT,
  kind TEXT NOT NULL, -- seed|open|close
  amount_usd REAL NOT NULL,
  position_id INTEGER,
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_scans_run ON scans(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_positions_run ON positions(run_id, opened_at);
CREATE INDEX IF NOT EXISTS idx_activity_run ON activity(run_id, ts);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON cash_ledger(run_id, id);

-- One open simulated position per mint inside a real paper run.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_paper_position
  ON positions(run_id, mint)
  WHERE closed_at IS NULL AND run_id IS NOT NULL;
