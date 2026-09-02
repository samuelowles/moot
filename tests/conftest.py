"""Shared test fixtures and builders.

All tests run against the fixture adapter — no network, ever. The demo
directory mirrors examples/config.example.yaml: two mature markets (NZ, AU),
one seeded market (US), and a full stage ladder.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Pin THIS checkout's src/ ahead of any editable install of `moot` that may
# point at another worktree — the suite must test the tree it lives in.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from moot.adapters.fixture import FixtureAdapter
from moot.baselines import MarketBaseline, compute_baselines
from moot.config import load_config
from moot.gates.base import GateContext
from moot.models import Ad, CreativeType, Metrics, Stage

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
CONFIG_PATH = TESTS_DIR.parent / "examples" / "config.example.yaml"
RUN_NOW = datetime(2026, 8, 21, 9, 0, 0)


@pytest.fixture(scope="session")
def config():
    return load_config(CONFIG_PATH)


@pytest.fixture()
def adapter() -> FixtureAdapter:
    return FixtureAdapter(FIXTURES)


@pytest.fixture()
def snapshot(adapter):
    return adapter.fetch_entities()


def fixture_adapter(subdir: str) -> FixtureAdapter:
    return FixtureAdapter(FIXTURES / subdir)


def make_ad(
    ad_id: str = "ad_x",
    *,
    stage: Stage = Stage.PROVING,
    market: str = "NZ",
    creative: CreativeType = CreativeType.STATIC,
    status: str = "ACTIVE",
    age_days: int = 20,
    post_id: str | None = "p_x",
    campaign_id: str = "120000000000001",
    recent: Metrics | None = None,
    trailing: Metrics | None = None,
    lifetime: Metrics | None = None,
) -> Ad:
    """A delivering ad with explicit windows; defaults satisfy no gate."""
    return Ad(
        id=ad_id,
        name=f"ad {ad_id}",
        status=status,
        effective_status=status,
        adset_id="as_x",
        campaign_id=campaign_id,
        market=market,
        stage=stage,
        creative_type=creative,
        post_id=post_id,
        age_days=age_days,
        recent=recent,
        trailing=trailing,
        lifetime=lifetime,
    )


def make_ctx(config, market: str = "NZ", ads: tuple[Ad, ...] = (),
             baselines: dict | None = None) -> GateContext:
    """A context with computed-or-default baselines for the market."""
    if baselines is None:
        baselines = {market: MarketBaseline(market=market, value=10.0, source="computed")}
    return GateContext(
        config=config,
        baselines=baselines,
        market=market,
        now=RUN_NOW,
        market_ads=ads,
    )


def baseline_run(config, adsets):
    return compute_baselines(adsets, config)
