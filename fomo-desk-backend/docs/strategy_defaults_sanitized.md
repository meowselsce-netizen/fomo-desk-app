# PAPER desk strategy defaults (sanitized)
From `desk/settings_defaults.py`. Continuous PAPER loop must keep mode=PAPER.
- `mode` = `PAPER`
- `fomo_handle` = `AsgerJepsen`
- `fomo_referral` = `savipww`
- `analyst_mode` = `rules`
- `rpc_url`: *(public RPC endpoint via env/default — use documented public Solana RPC)*
- `hosting_plan` = `B`
- `report_time` = `07:00`
- `timezone` = `Europe/Copenhagen`
- `scanner.min_liquidity_usd` = `40000`
- `scanner.max_top10_pct` = `25`
- `scanner.max_h1_change_pct` = `80`
- `scanner.poll_s` = `60`
- `scanner.min_pool_age_min` = `10`
- `scanner.max_wait_min` = `60`
- `scanner.runner_h24_pct` = `300`
- `auditor.enabled` = `true`
- `auditor.max_dev_pct` = `5`
- `auditor.max_top10_pct` = `25`
- `auditor.min_lp_locked_pct` = `99`
- `auditor.min_pool_age_min` = `10`
- `auditor.max_bundled` = `5`
- `auditor.max_delegated_accounts` = `10`
- `analyst.alive_6h_min` = `0.70`
- `analyst.organic_min` = `0.65`
- `analyst.chase_max` = `0.30`
- `analyst.score_min` = `70`
- `analyst.state_max_chars` = `3000`
- `risk.stop_pct` = `6`
- `risk.trail_start_pct` = `20`
- `risk.trail_pct` = `12`
- `risk.timeout_h` = `3`
- `risk.poll_s` = `15`
- `risk.no_price_polls` = `4`
- `executor.stack_usd` = `200`
- `executor.min_size_usd` = `47.50`
- `executor.under_min_policy` = `bump`
- `executor.max_liq_pct` = `2`
- `api.prices_poll_s` = `60`

All FOMO URLs must keep `r=savipww` via `desk/links.py`.

Paper fill accounting, not strategy gates: `paper.fee_bps` = `30`, `paper.slippage_bps` = `30`, `paper.max_quote_age_s` = `20`. A quote older than that is not filled.
