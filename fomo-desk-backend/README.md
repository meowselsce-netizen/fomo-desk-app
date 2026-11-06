# FOMO desk PAPER runner

One asyncio process runs Scanner, Auditor, Analyst (rules only), Executor, and Risk. Fills are simulated in SQLite. There is no order API, no signing, and no wallet code. `LIVE` is rejected.

The static Pages site at the repository root is separate. Do not point this database at that `data/` directory. Those files are an encrypted snapshot, not this runner's book.

Strategy gates stay at the values in `docs/strategy_defaults_sanitized.md`. `paper.fee_bps` (30) and `paper.slippage_bps` (30) are fill accounting only. A missing safety field fails closed. If no coin qualifies, the report says so and lists the reasons. That is not a license to loosen a gate.

## Install

From this directory (a virtualenv is enough; git is not required):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests
```

## New isolated book

`var/paper/desk.sqlite` is created on first use and is gitignored.

```bash
cd fomo-desk-backend
. .venv/bin/activate
python -m desk.paper --db var/paper/desk.sqlite baseline
python -m desk.paper --db var/paper/desk.sqlite health
python -m desk.paper --db var/paper/desk.sqlite smoke-public
python -m desk.paper --db var/paper/desk.sqlite report
```

`baseline` only creates an empty PAPER run. `health` prints mode, WAL, and heartbeats. It does not print private environment names or values. `smoke-public` performs a few read-only GETs (DexScreener, one RugCheck report, a 3-second PumpPortal listen). It writes no positions and sends no Telegram message. If it exits non-zero, do not start the loop.

`report` separates this run from legacy rows (`run_id` empty). Legacy or smoke rows are not actual results. Simulated PnL is not a profit claim.

## One cycle, then the loop

```bash
python -m desk.paper --db var/paper/desk.sqlite once
python -m desk.paper --db var/paper/desk.sqlite report
```

Start the continuous loop only after `pytest`, `health` (mode `PAPER`), and `smoke-public` (exit 0) look right:

```bash
python -m desk.paper --db var/paper/desk.sqlite run
```

Stop it with Ctrl-C. The same database and run id are reused on the next start, so an open simulated position is not filled a second time. A second process exits immediately because of the lock file next to the database.

The desk calls public DexScreener and RugCheck over HTTPS and applies the saved rate limit and backoff. It does not send Telegram messages and it does not publish the Pages snapshot. Analyst mode stays `rules` (no paid API). Solana coin links use chain id `1399811149` and `r=savipww`.

A read-only sample of RugCheck included mint and freeze authority, token supply, LP lock percent, and holder percents. It did not include a dev-sold flag or a delegated-account count. Those checks fail closed (`no data: dev sold`, `no data: delegations`). The loop will keep rejecting a coin until a report actually contains the field. Do not treat a night of rejects as a broken gate, and do not expect a simulated entry from incomplete reports. `report` lists the reasons. Simulated PnL is not a profit claim.
