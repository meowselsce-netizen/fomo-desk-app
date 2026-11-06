"""Outbound push helper. Buttons/body URLs must come from links.py only."""
from __future__ import annotations

from typing import Any

from desk.links import SIGNUP_URL, coin_url, profile_url


def buy_push(mint: str, text: str, chain: str = "solana") -> dict[str, Any]:
    url = coin_url(mint, chain)
    return {
        "text": text,
        "button": {"label": "Buy on FOMO", "url": url},
        "fallback_line": url,  # for channels without buttons
    }


def sell_push(mint: str, text: str, chain: str = "solana") -> dict[str, Any]:
    url = coin_url(mint, chain)
    return {
        "text": text,
        "button": {"label": "Sell on FOMO", "url": url},
        "fallback_line": url,
    }


def invite_push(text: str = "Invite a friend") -> dict[str, Any]:
    return {
        "text": text,
        "button": {"label": "Join FOMO", "url": SIGNUP_URL},
        "fallback_line": SIGNUP_URL,
    }


def profile_push(handle: str, text: str = "Your FOMO profile") -> dict[str, Any]:
    url = profile_url(handle)
    return {
        "text": text,
        "button": {"label": "Open profile", "url": url},
        "fallback_line": url,
    }
