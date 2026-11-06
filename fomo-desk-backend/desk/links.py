"""STEP 6 · FOMO links (non-negotiable).

Every FOMO URL the desk produces goes through these helpers.
Never drop r=savipww. Never shorten or wrap a FOMO link.
"""
from __future__ import annotations

import logging

log = logging.getLogger("links")

CHAIN_ID = {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bnb": 56}
SIGNUP_URL = "https://fomo.family/r/savipww"
REFERRAL = "savipww"


def coin_url(address: str, chain: str = "solana") -> str:
    if chain not in CHAIN_ID:
        log.warning("unknown chain %r for address %s — returning SIGNUP_URL", chain, address)
        return SIGNUP_URL
    return (
        f"https://fomo.family/coin?address={address}"
        f"&chainId={CHAIN_ID[chain]}&r={REFERRAL}&source=share_link"
    )


def profile_url(handle: str) -> str:
    h = handle.lstrip("@")
    return f"https://fomo.family/profile/{h}?source=share_link&r={REFERRAL}"
