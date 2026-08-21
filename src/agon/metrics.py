"""Insight-row parsing — the traps of docs/gates.md §11, implemented.

Every helper here exists because the naive version produced a wrong decision in
production. The docstring on each one names the failure it prevents.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from agon.models import Metrics

# Action-type lookup orders (§11.3): the omni_ prefixed keys are the
# cross-channel rollups Meta prefers; the bare keys are the fallbacks still
# returned by some placements and older report versions.
PURCHASE_KEYS = ("omni_purchase", "purchase")
CART_KEYS = ("omni_add_to_cart", "add_to_cart")
VALUE_KEYS = ("omni_purchase_value", "purchase_value")


def to_float(value: Any) -> Optional[float]:
    """Cast a platform metric to float, or return None — never 0 — when absent.

    Prevents: every numeric field on a Meta insights row arrives as a string
    (``"123.45"``); an empty string or missing key means *not reported*, not
    zero. Treating it as zero fabricates spend, clicks or value the platform
    never claimed (§11.1, §11.2).
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; never a metric
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> Optional[int]:
    """Cast a platform metric to int, or return None when absent.

    Prevents: the same string-casting trap as :func:`to_float`, for the count
    fields (impressions, clicks, actions). ``"0"`` is a real zero and is kept;
    ``""``/``None`` is absence and must stay absence (§11.1).
    """
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def extract_action(
    actions: Optional[Sequence[dict[str, Any]]], keys: Sequence[str]
) -> Optional[int]:
    """Pull an action count out of a sparse ``actions`` array.

    Prevents: action arrays are sparse, keyed by ``action_type`` — a missing
    ``purchase`` entry means *absent from this response*, not zero
    (§11.2). Iterating the list and defaulting to 0 turns absence into a
    fabricated zero, which is exactly how an ad with unreported conversions
    gets killed by limb A. Returns ``None`` when no listed key is present.
    """
    if not actions:
        return None
    lookup = {entry.get("action_type"): entry.get("value") for entry in actions}
    for key in keys:
        if key in lookup:
            return to_int(lookup[key])
    return None


def extract_action_value(
    action_values: Optional[Sequence[dict[str, Any]]], keys: Sequence[str]
) -> Optional[float]:
    """Pull a conversion value out of the sparse ``action_values`` array.

    Prevents: the same sparse-array trap as :func:`extract_action`, applied to
    the money side. Value must come from the *matching* key of the value array
    (§11.3) — pairing ``omni_purchase`` counts with a bare ``purchase_value``
    mixes attribution scopes.
    """
    if not action_values:
        return None
    lookup = {entry.get("action_type"): entry.get("value") for entry in action_values}
    for key in keys:
        if key in lookup:
            return to_float(lookup[key])
    return None


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    """Return the first key whose value is present (not None).

    Prevents: ``row.get(a) or row.get(b)`` silently discards a legitimate 0 —
    ``0 or x`` evaluates to ``x`` — turning a real zero-view count into
    whatever the fallback key holds.
    """
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def parse_insights_row(row: dict[str, Any]) -> Metrics:
    """Build a :class:`Metrics` from one platform insights row.

    Field order follows docs/gates.md §1. ``outbound_clicks_ctr`` is stored
    exactly as delivered — a decimal fraction (``0.0114`` == 1.14%) — and is
    never rescaled (§11.4). Every absent field stays ``None``; no default of
    zero is introduced anywhere in this function.
    """
    return Metrics(
        spend=to_float(row.get("spend")),
        impressions=to_int(row.get("impressions")),
        clicks=to_int(row.get("clicks")),
        outbound_clicks=to_int(row.get("outbound_clicks")),
        # §11.4: already a fraction. Rescaling here silently moves the 1% CTR
        # floor by two orders of magnitude.
        outbound_ctr=to_float(row.get("outbound_clicks_ctr")),
        cpm=to_float(row.get("cpm")),
        frequency=to_float(row.get("frequency")),
        purchases=extract_action(row.get("actions"), PURCHASE_KEYS),
        purchase_value=extract_action_value(row.get("action_values"), VALUE_KEYS),
        carts=extract_action(row.get("actions"), CART_KEYS),
        video_3s=to_int(_first_present(row, "video_3s_views", "video_p25_watched_actions")),
        thruplays=to_int(row.get("video_thruplay_watched_actions")),
    )
