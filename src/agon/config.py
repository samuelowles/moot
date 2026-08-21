"""Config loading, validation and target-derived threshold resolution.

Implements docs/gates.md §2: every performance threshold is a ratio of the
account target ``T`` and is resolved at load time — never hardcoded in a gate.
The account's configured gross margin is checked against the derived kill
floors and a WARNING is logged on inconsistency (§2 "Margin assumption"); the
runtime never silently corrects them.

The config shape is exactly examples/config.example.yaml.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from agon.models import Stage

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the config is structurally invalid. Message is actionable."""


# --- §2 defaults -------------------------------------------------------------
# Ratio-of-target defaults fitted against a live account at T = 5.64
# (docs/gates.md §2 table). Every one is overridable via `gates.ratios`
# (all names) or `guards.breaker_return_floor_ratio` (breaker only).
DEFAULT_RATIOS: dict[str, float] = {
    "graduate_return_floor": 0.71,
    "fatigue_return_floor": 0.71,
    "kill_floor_proving": 0.35,
    "kill_floor_retarget": 0.35,
    "kill_floor_scale": 0.53,
    "kill_floor_reserve": 0.53,
    "budget_up_trigger": 0.80,
    "budget_down_trigger": 0.53,
    "breaker_floor": 0.35,
}

# Ratio names that resolve a stage-keyed kill floor (docs/gates.md §2).
KILL_FLOOR_RATIO_KEYS: dict[Stage, str] = {
    Stage.PROVING: "kill_floor_proving",
    Stage.SCALE: "kill_floor_scale",
    Stage.RESERVE: "kill_floor_reserve",
    Stage.RETARGET: "kill_floor_retarget",
}

# A derived proving/retarget floor within this many ratio-points of the
# margin-implied floor (1 − gross margin, see _warn_on_margin) is consistent.
# Small tolerance so ordinary rounding does not warn.
MARGIN_CONSISTENCY_TOLERANCE = 0.025


@dataclass(frozen=True)
class AccountConfig:
    name: str = ""
    platform: str = "meta"
    allowed_account_ids: tuple[str, ...] = ()
    currency: str = "USD"
    timezone: str = "UTC"


@dataclass(frozen=True)
class MarginConfig:
    gross_margin_pct: float = 65.0
    platform_haircut_pct: float = 30.0

    @property
    def haircut_multiplier(self) -> float:
        """Multiplier applied to platform return reported as true return (§11.9)."""
        return 1.0 - self.platform_haircut_pct / 100.0


@dataclass(frozen=True)
class WindowsConfig:
    recent_days: int = 7
    trailing_days: int = 30
    attribution: str = "7d_click"


@dataclass(frozen=True)
class MarketConfig:
    code: str
    baseline_fallback: Optional[float] = None
    seed_from: Optional[str] = None
    destination_page_id: Optional[str] = None

    @property
    def is_seeded(self) -> bool:
        """A market seeded from an analogue runs kill gates only (§3)."""
        return self.seed_from is not None


@dataclass(frozen=True)
class StageEntry:
    """One (stage, market) cell of the stage map."""

    stage: Stage
    market: str
    campaign_id: str
    cohort_pattern: Optional[str] = None  # scale stage: "{yyyy}-{mm} winners"
    adset_name: Optional[str] = None  # reserve stage: "Retired winners"


@dataclass(frozen=True)
class GraduateGates:
    min_spend: float = 200.0
    min_purchases: int = 3
    min_ctr: float = 0.01  # decimal fraction — §5 note / §11.4
    min_hook: float = 0.20  # VIDEO ONLY; skipped for static — §5
    efficiency_max: float = 0.85  # × baseline — Path A
    return_cpc_ceiling: float = 1.80  # × baseline — Path B
    speculative_enabled: bool = False
    speculative_return_min: float = 8.0


@dataclass(frozen=True)
class KillGates:
    a_min_spend: float = 30.0
    b_min_spend: float = 60.0
    c_min_spend: float = 150.0
    cost_max: float = 1.30  # × baseline — §4 B
    starved_max_spend: float = 5.0
    cart_rate_floor: float = 0.015  # §4 D default 1.5%


@dataclass(frozen=True)
class FatigueGates:
    min_lifetime_purchases: int = 3
    min_age_days: int = 7
    min_spend: float = 150.0
    cost_ratio: float = 1.30  # × the ad's OWN trailing cost per cart — §6 cond. 5
    return_ratio: float = 0.60  # × the ad's OWN trailing return — §6 cond. 5


@dataclass(frozen=True)
class DemoteGates:
    """§7 thresholds. gates.md names demote_min_spend /
    demote_min_lifetime_purchases / demote_min_age — DEMOTE has its own keys;
    each defaults to the §6 fatigue value it previously borrowed."""

    min_spend: float = 150.0
    min_lifetime_purchases: int = 3
    min_age_days: int = 7


@dataclass(frozen=True)
class BudgetGates:
    step_pct: float = 25.0  # hard-capped at +30 in the write layer — §8
    down_pct: float = 30.0
    up_min_spend: float = 100.0
    down_min_spend: float = 200.0


@dataclass(frozen=True)
class BaselineGates:
    min_spend: float = 100.0
    min_population: int = 4


@dataclass(frozen=True)
class GuardsConfig:
    anomaly_guard_pct: float = 50.0
    breaker_return_floor_ratio: float = 0.35  # × target — §10


@dataclass(frozen=True)
class EnvelopeConfig:
    authorized: frozenset[str] = frozenset()
    forbidden: frozenset[str] = frozenset()

    def is_authorized(self, verb: str) -> bool:
        """True only if the verb is explicitly authorized AND not forbidden."""
        if verb in self.forbidden:
            return False
        return verb in self.authorized

    @property
    def hard_cap_pct(self) -> float:
        """The +30% budget-step hard cap is code, not config (§8)."""
        return 30.0


@dataclass(frozen=True)
class DestinationPolicy:
    require_patterns: tuple[str, ...] = ()
    forbid_patterns: tuple[str, ...] = ()
    require_tracking_params: bool = False


@dataclass(frozen=True)
class NamingPolicy:
    pattern: Optional[str] = None
    duplicate_suffix: str = " - {stage}"


@dataclass(frozen=True)
class ReportingConfig:
    sinks: tuple[str, ...] = ("stdout",)
    audit_log: str = "reports/write-audit.jsonl"


@dataclass(frozen=True)
class Config:
    """Validated account configuration with §2 thresholds resolved."""

    account: AccountConfig = field(default_factory=AccountConfig)
    target: float = 5.64
    margin: MarginConfig = field(default_factory=MarginConfig)
    windows: WindowsConfig = field(default_factory=WindowsConfig)
    markets: dict[str, MarketConfig] = field(default_factory=dict)
    stages: dict[Stage, dict[str, StageEntry]] = field(default_factory=dict)
    pixel: dict[str, Any] = field(default_factory=dict)
    gates_ratios: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RATIOS))
    graduate: GraduateGates = field(default_factory=GraduateGates)
    kill: KillGates = field(default_factory=KillGates)
    fatigue: FatigueGates = field(default_factory=FatigueGates)
    demote: DemoteGates = field(default_factory=DemoteGates)
    budget: BudgetGates = field(default_factory=BudgetGates)
    baseline: BaselineGates = field(default_factory=BaselineGates)
    guards: GuardsConfig = field(default_factory=GuardsConfig)
    envelope: EnvelopeConfig = field(default_factory=EnvelopeConfig)
    destination_policy: DestinationPolicy = field(default_factory=DestinationPolicy)
    naming: NamingPolicy = field(default_factory=NamingPolicy)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)

    # --- threshold resolution (§2) -----------------------------------------

    def ratio(self, name: str) -> float:
        """The resolved ratio-of-target for a §2 threshold name."""
        if name not in DEFAULT_RATIOS:
            raise ConfigError(
                f"unknown threshold {name!r}; expected one of: "
                f"{', '.join(sorted(DEFAULT_RATIOS))}"
            )
        return self.gates_ratios[name]

    def threshold(self, name: str, stage: Optional[Stage] = None) -> float:
        """Resolve a §2 threshold to an absolute value: ratio × target.

        ``name="kill_floor"`` requires ``stage`` and resolves through the
        per-stage ratio keys. Everything else resolves by its §2 name. The
        breaker floor honours the ``guards.breaker_return_floor_ratio`` key
        from the config file.
        """
        if name == "kill_floor":
            if stage is None:
                raise ConfigError("threshold('kill_floor') requires a stage")
            return self.ratio(KILL_FLOOR_RATIO_KEYS[stage]) * self.target
        if name not in DEFAULT_RATIOS:
            raise ConfigError(
                f"unknown threshold {name!r}; expected 'kill_floor' or one of: "
                f"{', '.join(sorted(DEFAULT_RATIOS))}"
            )
        return self.ratio(name) * self.target

    def kill_floor(self, stage: Stage) -> float:
        """Convenience wrapper for the stage-keyed kill floor (§4 C2)."""
        return self.threshold("kill_floor", stage)

    # --- stage map lookups (§9 B) -------------------------------------------

    def locate_campaign(self, campaign_id: str) -> Optional[StageEntry]:
        """Reverse-lookup a campaign id in the stage map (framework.md §4)."""
        for market_map in self.stages.values():
            for entry in market_map.values():
                if entry.campaign_id == campaign_id:
                    return entry
        return None

    def stage_entry(self, stage: Stage, market: str) -> Optional[StageEntry]:
        """The (stage, market) cell, or None where the map has a structural gap."""
        return self.stages.get(stage, {}).get(market)

    def destination_for(self, stage: Stage, market: str) -> Optional[StageEntry]:
        """Alias of :meth:`stage_entry` — reads better at duplication sites."""
        return self.stage_entry(stage, market)


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
    return value


def _parse_account(raw: dict[str, Any]) -> AccountConfig:
    ids = raw.get("allowed_account_ids") or []
    if not isinstance(ids, (list, tuple)) or not ids:
        raise ConfigError(
            "account.allowed_account_ids: must be a non-empty list — Agon refuses "
            "to write to any account not explicitly listed"
        )
    if not all(isinstance(i, str) and i.strip() for i in ids):
        raise ConfigError(
            "account.allowed_account_ids: every entry must be a non-empty string "
            "(e.g. 'act_100000000000001')"
        )
    return AccountConfig(
        name=str(raw.get("name", "")),
        platform=str(raw.get("platform", "meta")),
        allowed_account_ids=tuple(ids),
        currency=str(raw.get("currency", "USD")),
        timezone=str(raw.get("timezone", "UTC")),
    )


def _parse_markets(raw: dict[str, Any]) -> dict[str, MarketConfig]:
    markets: dict[str, MarketConfig] = {}
    for code, entry in raw.items():
        entry = _require_mapping(entry, f"markets.{code}")
        seed = entry.get("seed_from")
        if seed is not None and seed == code:
            raise ConfigError(f"markets.{code}.seed_from: a market cannot seed from itself")
        markets[code] = MarketConfig(
            code=code,
            baseline_fallback=entry.get("baseline_fallback"),
            seed_from=seed,
            destination_page_id=entry.get("destination_page_id"),
        )
    for code, market in markets.items():
        if market.seed_from is not None and market.seed_from not in markets:
            raise ConfigError(
                f"markets.{code}.seed_from: references unknown market "
                f"{market.seed_from!r}; known markets: {', '.join(sorted(markets))}"
            )
        if market.seed_from is None and market.baseline_fallback is None:
            raise ConfigError(
                f"markets.{code}: needs either baseline_fallback (a number) or "
                f"seed_from (an existing market) — a market with neither has no "
                f"baseline while below population"
            )
    return markets


def _parse_stages(raw: dict[str, Any]) -> dict[Stage, dict[str, StageEntry]]:
    stages: dict[Stage, dict[str, StageEntry]] = {}
    for key, market_map in raw.items():
        try:
            stage = Stage.from_config_key(str(key))
        except ValueError:
            raise ConfigError(
                f"stages.{key}: unknown stage {key!r}; expected one of "
                "proving/scale/reserve/retarget"
            ) from None
        market_map = _require_mapping(market_map, f"stages.{key}")
        entries: dict[str, StageEntry] = {}
        for market, entry in market_map.items():
            entry = _require_mapping(entry, f"stages.{key}.{market}")
            campaign_id = entry.get("campaign_id")
            if not campaign_id:
                raise ConfigError(
                    f"stages.{key}.{market}: campaign_id is required — the "
                    f"duplication pre-flight resolves markets through it (§9 B)"
                )
            entries[str(market)] = StageEntry(
                stage=stage,
                market=str(market),
                campaign_id=str(campaign_id),
                cohort_pattern=entry.get("cohort_pattern"),
                adset_name=entry.get("adset_name"),
            )
        stages[stage] = entries
    return stages


def _dataclass_from(cls: type, raw: Optional[dict[str, Any]], path: str) -> Any:
    """Build a gates dataclass from YAML, rejecting unknown keys with the
    accepted key list — a typo'd threshold silently reverting to default is
    exactly the drift §2 exists to prevent."""
    if raw is None:
        return cls()
    raw = _require_mapping(raw, path)
    known = set(cls.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) {sorted(unknown)}; accepted: {sorted(known)}"
        )
    return cls(**raw)


def _warn_on_margin(config: Config) -> None:
    """Log a WARNING when derived kill floors are inconsistent with the margin.

    docs/gates.md §2 states the Proving Ground/Retarget floor of 0.35 × T
    assumes roughly 65% gross margin. The implied-consistent floor ratio is
    ``1 − gross_margin`` (at 65% margin that is exactly 0.35). Below that
    margin the floor is too low and must rise; above it, the floor is
    needlessly harsh. The runtime warns — it never silently corrects.
    """
    implied = 1.0 - config.margin.gross_margin_pct / 100.0
    for stage in (Stage.PROVING, Stage.RETARGET):
        actual = config.ratio(KILL_FLOOR_RATIO_KEYS[stage])
        if abs(actual - implied) > MARGIN_CONSISTENCY_TOLERANCE:
            logger.warning(
                "config: %s kill-floor ratio %.3f is inconsistent with "
                "margin.gross_margin_pct=%.0f%% (implied ratio %.3f). Below that "
                "margin these floors are too low and must rise. Agon does not "
                "silently correct them — fix target or margin (docs/gates.md §2).",
                stage.value,
                actual,
                config.margin.gross_margin_pct,
                implied,
            )


def _validate_stage_map_markets(config: Config) -> None:
    known_markets = set(config.markets)
    for stage, entries in config.stages.items():
        for market in entries:
            if known_markets and market not in known_markets:
                raise ConfigError(
                    f"stages.{stage.value.lower()}.{market}: market {market!r} is "
                    f"not defined under `markets:` — known: {', '.join(sorted(known_markets))}"
                )


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file into a :class:`Config`.

    Raises :class:`ConfigError` with an actionable message on structural
    invalidity. Thresholds are resolved here so no downstream module ever
    hardcodes a §2 value.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {path} is not valid YAML: {exc}") from exc
    if raw is None:
        raise ConfigError(f"config file {path} is empty")
    raw = _require_mapping(raw, "config root")

    target = raw.get("target")
    if not isinstance(target, (int, float)) or target <= 0:
        raise ConfigError("target: must be a positive number — it derives every §2 threshold")

    account = _parse_account(_require_mapping(raw.get("account", {}), "account"))
    markets = _parse_markets(_require_mapping(raw.get("markets", {}), "markets"))
    stages = _parse_stages(_require_mapping(raw.get("stages", {}), "stages"))

    gates = _require_mapping(raw.get("gates", {}), "gates")
    # `gates.ratios` is optional: per-name overrides of the §2 ratio defaults.
    # Not present in examples/config.example.yaml (which runs pure defaults);
    # documented here and in the module docstring.
    ratio_overrides = gates.get("ratios") or {}
    ratio_overrides = _require_mapping(ratio_overrides, "gates.ratios")
    unknown = set(ratio_overrides) - set(DEFAULT_RATIOS)
    if unknown:
        raise ConfigError(
            f"gates.ratios: unknown threshold name(s) {sorted(unknown)}; "
            f"accepted: {sorted(DEFAULT_RATIOS)}"
        )
    ratios = dict(DEFAULT_RATIOS)
    ratios.update({k: float(v) for k, v in ratio_overrides.items()})

    guards_raw = _require_mapping(raw.get("guards", {}), "guards")
    breaker_override = guards_raw.get("breaker_return_floor_ratio")
    guards = GuardsConfig(
        anomaly_guard_pct=float(guards_raw.get("anomaly_guard_pct", 50.0)),
        breaker_return_floor_ratio=float(
            breaker_override
            if breaker_override is not None
            else DEFAULT_RATIOS["breaker_floor"]
        ),
    )
    # The breaker ratio is config-addressable under `guards:` (§10), so it
    # wins over `gates.ratios.breaker_floor` — but ONLY when that guards key
    # is actually present; an explicit `gates.ratios.breaker_floor` override
    # must survive an unconfigured guards block.
    if breaker_override is not None:
        ratios["breaker_floor"] = guards.breaker_return_floor_ratio

    margin_raw = _require_mapping(raw.get("margin", {}), "margin")
    margin = MarginConfig(
        gross_margin_pct=float(margin_raw.get("gross_margin_pct", 65.0)),
        platform_haircut_pct=float(margin_raw.get("platform_haircut_pct", 30.0)),
    )

    envelope_raw = _require_mapping(raw.get("envelope", {}), "envelope")
    envelope = EnvelopeConfig(
        authorized=frozenset(str(v) for v in envelope_raw.get("authorized", [])),
        forbidden=frozenset(str(v) for v in envelope_raw.get("forbidden", [])),
    )

    # docs/writes.md §4: the pixel is set explicitly on every ad set Agon
    # creates — a named safety property. An empty pixel id would be posted
    # verbatim, so the config refuses to load without one.
    pixel = _require_mapping(raw.get("pixel", {}), "pixel")
    pixel_id = str(pixel.get("id", "")).strip()
    if not pixel_id:
        raise ConfigError(
            "pixel.id: must be a non-empty id (e.g. '400000000000001') — every "
            "ad set Agon creates sets the pixel explicitly, and an empty one "
            "silently optimises against the wrong event (docs/writes.md §4)"
        )

    # §7 keys default to the §6 fatigue values they previously borrowed.
    fatigue = _dataclass_from(FatigueGates, gates.get("fatigue"), "gates.fatigue")
    demote_raw = gates.get("demote")
    if demote_raw is not None:
        demote_raw = _require_mapping(demote_raw, "gates.demote")
    known_demote = set(DemoteGates.__dataclass_fields__)
    unknown_demote = set(demote_raw or {}) - known_demote
    if unknown_demote:
        raise ConfigError(
            f"gates.demote: unknown key(s) {sorted(unknown_demote)}; accepted: "
            f"{sorted(known_demote)}"
        )
    demote = DemoteGates(
        min_spend=float((demote_raw or {}).get("min_spend", fatigue.min_spend)),
        min_lifetime_purchases=int(
            (demote_raw or {}).get("min_lifetime_purchases", fatigue.min_lifetime_purchases)
        ),
        min_age_days=int((demote_raw or {}).get("min_age_days", fatigue.min_age_days)),
    )

    policy_raw = _require_mapping(raw.get("policy", {}), "policy")
    dest_raw = _require_mapping(policy_raw.get("destination", {}), "policy.destination")
    naming_raw = _require_mapping(policy_raw.get("naming", {}), "policy.naming")

    reporting_raw = _require_mapping(raw.get("reporting", {}), "reporting")
    windows_raw = _require_mapping(raw.get("windows", {}), "windows")

    config = Config(
        account=account,
        target=float(target),
        margin=margin,
        windows=WindowsConfig(
            recent_days=int(windows_raw.get("recent_days", 7)),
            trailing_days=int(windows_raw.get("trailing_days", 30)),
            attribution=str(windows_raw.get("attribution", "7d_click")),
        ),
        markets=markets,
        stages=stages,
        pixel=pixel,
        gates_ratios=ratios,
        graduate=_dataclass_from(GraduateGates, gates.get("graduate"), "gates.graduate"),
        kill=_dataclass_from(KillGates, gates.get("kill"), "gates.kill"),
        fatigue=fatigue,
        demote=demote,
        budget=_dataclass_from(BudgetGates, gates.get("budget"), "gates.budget"),
        baseline=_dataclass_from(BaselineGates, gates.get("baseline"), "gates.baseline"),
        guards=guards,
        envelope=envelope,
        destination_policy=DestinationPolicy(
            require_patterns=tuple(dest_raw.get("require_patterns", [])),
            forbid_patterns=tuple(dest_raw.get("forbid_patterns", [])),
            require_tracking_params=bool(dest_raw.get("require_tracking_params", False)),
        ),
        naming=NamingPolicy(
            pattern=naming_raw.get("pattern"),
            duplicate_suffix=str(naming_raw.get("duplicate_suffix", " - {stage}")),
        ),
        reporting=ReportingConfig(
            sinks=tuple(str(s) for s in reporting_raw.get("sinks", ("stdout",))),
            audit_log=str(reporting_raw.get("audit_log", "reports/write-audit.jsonl")),
        ),
    )

    _validate_stage_map_markets(config)
    _warn_on_margin(config)
    return config
