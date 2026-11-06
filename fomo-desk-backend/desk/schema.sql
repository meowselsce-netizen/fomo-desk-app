-- FOMO desk Step 4 · initial schema (SQLite WAL)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY,
  name TEXT,
  symbol TEXT,
  pool TEXT,
  migrated_at TEXT,
  creator TEXT,
  creator_balance_at_migration REAL,
  holders INTEGER
);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  ts TEXT NOT NULL,
  liquidity_usd REAL,
  top10_pct REAL,
  h1_change_pct REAL,
  age_min REAL,
  verdict TEXT,
  reason TEXT,
  FOREIGN KEY (mint) REFERENCES coins(mint)
);

CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  ts TEXT NOT NULL,
  layer INTEGER NOT NULL,
  name TEXT NOT NULL,
  value_text TEXT,
  pass INTEGER, -- 0/1/NULL
  source TEXT,
  FOREIGN KEY (mint) REFERENCES coins(mint)
);

CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  ts TEXT NOT NULL,
  alive_6h REAL,
  organic REAL,
  flush REAL,
  chase REAL,
  score INTEGER,
  pass INTEGER,
  reason TEXT,
  input_tokens INTEGER,
  FOREIGN KEY (mint) REFERENCES coins(mint)
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  ts TEXT NOT NULL,
  price REAL,
  size_usd REAL,
  stack_pct REAL,
  link TEXT,
  mode TEXT,
  status TEXT,
  FOREIGN KEY (mint) REFERENCES coins(mint)
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  entry_price REAL,
  size_usd REAL,
  peak_price REAL,
  stop_price REAL,
  trail_active INTEGER DEFAULT 0,
  closed_at TEXT,
  exit_price REAL,
  exit_reason TEXT,
  pnl_usd REAL,
  fees_usd REAL,
  mode TEXT,
  stop_pct REAL,
  FOREIGN KEY (mint) REFERENCES coins(mint)
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL, -- bought|sold|stopped|rejected
  mint TEXT,
  agent TEXT,
  title TEXT,
  subtitle TEXT,
  value TEXT
);

CREATE TABLE IF NOT EXISTS prices (
  mint TEXT NOT NULL,
  ts TEXT NOT NULL,
  price REAL NOT NULL,
  volume_m5 REAL,
  PRIMARY KEY (mint, ts)
);

CREATE TABLE IF NOT EXISTS balance (
  ts TEXT PRIMARY KEY,
  value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  key TEXT NOT NULL,
  old TEXT,
  new TEXT,
  who TEXT
);

CREATE TABLE IF NOT EXISTS skips (
  mint TEXT PRIMARY KEY,
  until TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeats (
  agent TEXT PRIMARY KEY,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subs (
  endpoint TEXT PRIMARY KEY,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scans_mint_ts ON scans(mint, ts);
CREATE INDEX IF NOT EXISTS idx_audits_mint_ts ON audits(mint, ts);
CREATE INDEX IF NOT EXISTS idx_verdicts_mint_ts ON verdicts(mint, ts);
CREATE INDEX IF NOT EXISTS idx_signals_mint_ts ON signals(mint, ts);
CREATE INDEX IF NOT EXISTS idx_positions_mint ON positions(mint, opened_at);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts DESC);
CREATE INDEX IF NOT EXISTS idx_prices_mint_ts ON prices(mint, ts);
CREATE INDEX IF NOT EXISTS idx_balance_ts ON balance(ts);
