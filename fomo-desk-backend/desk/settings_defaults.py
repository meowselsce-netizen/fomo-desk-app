"""STEP 10 settings defaults (master prompt + Step 2/3 decisions).

Risk defaults from Asger / master: stop 6, trail_start 20, trail 12, timeout 3h.
Analyst: rules mode (Jev skipped). FOMO handle AsgerJepsen. Mode PAPER.
STEP 10 auditor line was truncated at max_delegated_acc*; filled as
max_delegated_accounts=10 from Auditor agent prompt ([10] wallets).
"""
from __future__ import annotations

# Flat key -> string value (stored in settings.value as text)
DEFAULTS: dict[str, str] = {
    # desk / account
    "mode": "PAPER",
    "fomo_handle": "AsgerJepsen",
    "fomo_referral": "savipww",
    "analyst_mode": "rules",
    "rpc_url": "https://api.mainnet-beta.solana.com",
    "hosting_plan": "B",
    "report_time": "07:00",
    "timezone": "Europe/Copenhagen",
    # scanner
    "scanner.min_liquidity_usd": "40000",
    "scanner.max_top10_pct": "25",
    "scanner.max_h1_change_pct": "80",
    "scanner.poll_s": "60",
    "scanner.min_pool_age_min": "10",
    "scanner.max_wait_min": "60",
    "scanner.runner_h24_pct": "300",
    # auditor
    "auditor.enabled": "true",
    "auditor.max_dev_pct": "5",
    "auditor.max_top10_pct": "25",
    "auditor.min_lp_locked_pct": "99",
    "auditor.min_pool_age_min": "10",
    "auditor.max_bundled": "5",
    "auditor.max_delegated_accounts": "10",
    # analyst gate
    "analyst.alive_6h_min": "0.70",
    "analyst.organic_min": "0.65",
    "analyst.chase_max": "0.30",
    "analyst.score_min": "70",
    "analyst.state_max_chars": "3000",
    # risk
    "risk.stop_pct": "6",
    "risk.trail_start_pct": "20",
    "risk.trail_pct": "12",
    "risk.timeout_h": "3",
    "risk.poll_s": "15",
    "risk.no_price_polls": "4",
    # executor (from acceptance tests / prompt)
    "executor.stack_usd": "200",
    "executor.min_size_usd": "47.50",
    "executor.under_min_policy": "bump",  # bump | skip
    "executor.max_liq_pct": "2",
    # api writers
    "api.prices_poll_s": "60",
    # Paper fill accounting only. These do not change scanner/auditor/analyst/risk gates.
    # fee_bps matches desk.fees.BPS. slippage_bps is the simulated adverse fill.
    "paper.fee_bps": "30",
    "paper.slippage_bps": "30",
    "paper.max_quote_age_s": "20",
    "paper.http_min_interval_s": "1.1",
    "paper.max_backoff_s": "60",
}
