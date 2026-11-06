"""PAPER fee and slippage. No exchange order is sent."""
from __future__ import annotations

# Conservative flat bps each side for paper accounting.
BPS = 30  # 0.30% per side


def side_fee(notional_usd: float, bps: float = BPS) -> float:
    return max(0.0, float(notional_usd)) * (float(bps) / 10_000)


def round_trip_fees(size_usd: float, entry: float, exit_: float, bps: float = BPS) -> float:
    buy = side_fee(size_usd, bps)
    sell_notional = size_usd * (exit_ / entry) if entry else size_usd
    sell = side_fee(sell_notional, bps)
    return buy + sell


def slipped_price(price: float, side: str, bps: float) -> float:
    """Adverse paper fill. Buys pay more, sells receive less. bps=0 leaves price unchanged."""
    px = float(price)
    slip = float(bps) / 10_000
    if side == "buy":
        return px * (1 + slip)
    if side == "sell":
        return px * (1 - slip)
    raise ValueError("side must be buy or sell")
