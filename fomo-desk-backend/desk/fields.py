"""Field mapping: raw provider samples -> FOMO desk internal schema.

Built from Step 2 raw proofs in logs/raw/:
  - dexscreener_tokens_v1.json
  - rugcheck_report.json
  - rugcheck_insiders_graph.json
  - solana_getSlot.json
  - pumpportal_subscribeMigration.json

Never place orders. Paper mode only.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

# Chain IDs used by FOMO / desk links (master prompt).
CHAIN_ID = {
    "solana": 1399811149,
    "robinhood": 4663,
    "base": 8453,
    "bnb": 56,
}

# Internal desk fields (canonical).
INTERNAL_FIELDS = (
    "mint",
    "symbol",
    "name",
    "chain",
    "chain_id",
    "price_usd",
    "price_native",
    "liquidity_usd",
    "volume_m5",
    "volume_h1",
    "volume_h6",
    "volume_h24",
    "price_change_m5",
    "price_change_h1",
    "price_change_h6",
    "price_change_h24",
    "buys_m5",
    "sells_m5",
    "buys_h1",
    "sells_h1",
    "fdv",
    "market_cap",
    "pair_address",
    "dex_id",
    "pair_created_at_ms",
    "image_url",
    "rug_score",
    "rug_score_normalised",
    "mint_authority",
    "freeze_authority",
    "token_program",
    "creator",
    "creator_balance",
    "top_holders",
    "top_holder_pct",
    "risks",
    "markets",
    "insiders_graph",
    "mutable_metadata",
    "slot",  # from RPC getSlot heartbeats
)

# Mapping table: internal_field -> (source, raw_path, transform_name)
# source: dexscreener | rugcheck | rugcheck_insiders | rpc | pumpportal
FIELD_MAP: list[dict[str, str]] = [
    # DexScreener pair object (tokens/v1 array element)
    {"internal": "mint", "source": "dexscreener", "path": "baseToken.address", "transform": "str"},
    {"internal": "symbol", "source": "dexscreener", "path": "baseToken.symbol", "transform": "str"},
    {"internal": "name", "source": "dexscreener", "path": "baseToken.name", "transform": "str"},
    {"internal": "chain", "source": "dexscreener", "path": "chainId", "transform": "str"},
    {"internal": "chain_id", "source": "dexscreener", "path": "chainId", "transform": "chain_id_lookup"},
    {"internal": "price_usd", "source": "dexscreener", "path": "priceUsd", "transform": "float"},
    {"internal": "price_native", "source": "dexscreener", "path": "priceNative", "transform": "float"},
    {"internal": "liquidity_usd", "source": "dexscreener", "path": "liquidity.usd", "transform": "float"},
    {"internal": "volume_m5", "source": "dexscreener", "path": "volume.m5", "transform": "float"},
    {"internal": "volume_h1", "source": "dexscreener", "path": "volume.h1", "transform": "float"},
    {"internal": "volume_h6", "source": "dexscreener", "path": "volume.h6", "transform": "float"},
    {"internal": "volume_h24", "source": "dexscreener", "path": "volume.h24", "transform": "float"},
    {"internal": "price_change_m5", "source": "dexscreener", "path": "priceChange.m5", "transform": "float"},
    {"internal": "price_change_h1", "source": "dexscreener", "path": "priceChange.h1", "transform": "float"},
    {"internal": "price_change_h6", "source": "dexscreener", "path": "priceChange.h6", "transform": "float"},
    {"internal": "price_change_h24", "source": "dexscreener", "path": "priceChange.h24", "transform": "float"},
    {"internal": "buys_m5", "source": "dexscreener", "path": "txns.m5.buys", "transform": "int"},
    {"internal": "sells_m5", "source": "dexscreener", "path": "txns.m5.sells", "transform": "int"},
    {"internal": "buys_h1", "source": "dexscreener", "path": "txns.h1.buys", "transform": "int"},
    {"internal": "sells_h1", "source": "dexscreener", "path": "txns.h1.sells", "transform": "int"},
    {"internal": "fdv", "source": "dexscreener", "path": "fdv", "transform": "float"},
    {"internal": "market_cap", "source": "dexscreener", "path": "marketCap", "transform": "float"},
    {"internal": "pair_address", "source": "dexscreener", "path": "pairAddress", "transform": "str"},
    {"internal": "dex_id", "source": "dexscreener", "path": "dexId", "transform": "str"},
    {"internal": "pair_created_at_ms", "source": "dexscreener", "path": "pairCreatedAt", "transform": "int"},
    {"internal": "image_url", "source": "dexscreener", "path": "info.imageUrl", "transform": "str"},
    # RugCheck report
    {"internal": "mint", "source": "rugcheck", "path": "mint", "transform": "str"},
    {"internal": "symbol", "source": "rugcheck", "path": "tokenMeta.symbol", "transform": "str"},
    {"internal": "name", "source": "rugcheck", "path": "tokenMeta.name", "transform": "str"},
    {"internal": "rug_score", "source": "rugcheck", "path": "score", "transform": "int"},
    {"internal": "rug_score_normalised", "source": "rugcheck", "path": "score_normalised", "transform": "int"},
    {"internal": "mint_authority", "source": "rugcheck", "path": "mintAuthority", "transform": "optional_str"},
    {"internal": "freeze_authority", "source": "rugcheck", "path": "freezeAuthority", "transform": "optional_str"},
    {"internal": "token_program", "source": "rugcheck", "path": "tokenProgram", "transform": "str"},
    {"internal": "creator", "source": "rugcheck", "path": "creator", "transform": "str"},
    {"internal": "creator_balance", "source": "rugcheck", "path": "creatorBalance", "transform": "float"},
    {"internal": "top_holders", "source": "rugcheck", "path": "topHolders", "transform": "identity"},
    {"internal": "top_holder_pct", "source": "rugcheck", "path": "topHolders", "transform": "sum_holder_pct"},
    {"internal": "risks", "source": "rugcheck", "path": "risks", "transform": "identity"},
    {"internal": "markets", "source": "rugcheck", "path": "markets", "transform": "identity"},
    {"internal": "mutable_metadata", "source": "rugcheck", "path": "tokenMeta.mutable", "transform": "bool"},
    {"internal": "image_url", "source": "rugcheck", "path": "fileMeta.image", "transform": "str"},
    # RugCheck insiders graph (may be empty list)
    {"internal": "insiders_graph", "source": "rugcheck_insiders", "path": "", "transform": "identity"},
    # Solana RPC
    {"internal": "slot", "source": "rpc_getSlot", "path": "result", "transform": "int"},
    # PumpPortal migration event (when present beyond subscribe ACK)
    {"internal": "mint", "source": "pumpportal_migration", "path": "mint", "transform": "str"},
]


def _dig(obj: Any, path: str) -> Any:
    if path == "":
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        else:
            return None
    return cur


def _chain_id_lookup(v: Any) -> int | None:
    if v is None:
        return None
    return CHAIN_ID.get(str(v).lower())


def _sum_holder_pct(holders: Any) -> float | None:
    if not isinstance(holders, list):
        return None
    total = 0.0
    for h in holders:
        if isinstance(h, Mapping) and h.get("pct") is not None:
            total += float(h["pct"])
    return total


_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "str": lambda v: None if v is None else str(v),
    "optional_str": lambda v: None if v is None else str(v),
    "float": lambda v: None if v is None or v == "" else float(v),
    "int": lambda v: None if v is None or v == "" else int(v),
    "bool": lambda v: None if v is None else bool(v),
    "identity": lambda v: v,
    "chain_id_lookup": _chain_id_lookup,
    "sum_holder_pct": _sum_holder_pct,
}


def map_source(source: str, raw: Any) -> dict[str, Any]:
    """Map one provider payload into a partial internal dict."""
    out: dict[str, Any] = {}
    for row in FIELD_MAP:
        if row["source"] != source:
            continue
        raw_val = _dig(raw, row["path"])
        fn = _TRANSFORMS[row["transform"]]
        try:
            out[row["internal"]] = fn(raw_val)
        except (TypeError, ValueError):
            out[row["internal"]] = None
    return out


def merge_token(
    *,
    dexscreener_pair: Mapping[str, Any] | None = None,
    rugcheck_report: Mapping[str, Any] | None = None,
    rugcheck_insiders: Any = None,
) -> dict[str, Any]:
    """Merge provider payloads; later sources overwrite earlier on the same key."""
    merged: dict[str, Any] = {k: None for k in INTERNAL_FIELDS}
    if dexscreener_pair is not None:
        merged.update({k: v for k, v in map_source("dexscreener", dexscreener_pair).items()})
    if rugcheck_report is not None:
        merged.update({k: v for k, v in map_source("rugcheck", rugcheck_report).items()})
    if rugcheck_insiders is not None:
        merged.update({k: v for k, v in map_source("rugcheck_insiders", rugcheck_insiders).items()})
    if merged.get("chain") == "solana" and not merged.get("chain_id"):
        merged["chain_id"] = CHAIN_ID["solana"]
    return merged


def smoke_from_raw_logs(raw_dir: str = "/workspace/fomo-desk/logs/raw") -> dict[str, Any]:
    """Load Step 2 raw samples and return one merged token dict (for proof)."""
    import json
    from pathlib import Path

    raw = Path(raw_dir)
    dex = json.loads((raw / "dexscreener_tokens_v1.json").read_text())["response"]
    rug = json.loads((raw / "rugcheck_report.json").read_text())["response"]
    ins = json.loads((raw / "rugcheck_insiders_graph.json").read_text())["response"]
    pair = dex[0] if isinstance(dex, list) and dex else {}
    return merge_token(dexscreener_pair=pair, rugcheck_report=rug, rugcheck_insiders=ins)


if __name__ == "__main__":
    import json

    tok = smoke_from_raw_logs()
    # Print only non-secret summary fields
    summary = {
        "mint": tok.get("mint"),
        "symbol": tok.get("symbol"),
        "price_usd": tok.get("price_usd"),
        "liquidity_usd": tok.get("liquidity_usd"),
        "rug_score": tok.get("rug_score"),
        "rug_score_normalised": tok.get("rug_score_normalised"),
        "chain_id": tok.get("chain_id"),
        "top_holder_pct": tok.get("top_holder_pct"),
        "mapped_non_null": sum(1 for v in tok.values() if v not in (None, [], {})),
    }
    print(json.dumps(summary, indent=2))
