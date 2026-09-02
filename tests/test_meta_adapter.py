"""MetaAdapter behaviour with a stubbed transport — no network, ever.

Covers the §10 breaker-2 wiring (a failing insights call is a gap, not a
crash), the account-allowlist prefix normalisation (writes.md §6), the
§11.6 pagination backstops, and the fields the live insights request
carries (§11.2/§11.5)."""

from __future__ import annotations

import json

import pytest
from conftest import FIXTURES, RUN_NOW

from moot.adapters.base import (
    AdPlatformAdapter,
    IncompletePullError,
    WriteRefusedError,
)
from moot.adapters.fixture import FixtureAdapter
from moot.adapters.meta import MAX_PAGES, MetaAdapter
from moot.pipeline import Pipeline
from moot.report import render_report

ACCOUNT = "act_100000000000001"

CAMPAIGN_ROW = {
    "id": "120000000000001",
    "name": "NZ - Proving Ground",
    "status": "ACTIVE",
    "effective_status": "ACTIVE",
    "account_id": ACCOUNT,
    "daily_budget": "40000",   # minor units — $400.00, as Graph sends it
}
ADSET_ROW = {
    "id": "as_nz_p1",
    "name": "NZ concept alpha",
    "status": "ACTIVE",
    "effective_status": "ACTIVE",
    "campaign_id": "120000000000001",
    "daily_budget": "2000",    # minor units — $20.00, as Graph sends it
}
AD_ROW = {
    "id": "ad_graduate_a",
    "name": "NZ-20260809-VID-winner",
    "status": "ACTIVE",
    "effective_status": "ACTIVE",
    "adset_id": "as_nz_p1",
    "campaign_id": "120000000000001",
    "creative": {
        "effective_object_story_id": "100000000000001_p_g1",
        "url_tags": "utm_source=meta",
    },
}


class _StubResponse:
    def __init__(self, payload):
        self.status_code = 200
        self.content = b"{}"
        self._payload = payload

    def json(self):
        return self._payload


class _StubSession:
    """Serves canned entity lists by URL suffix; insights fail on demand."""

    def __init__(self, fail_insights: bool = False):
        self.fail_insights = fail_insights
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **_kwargs):
        self.calls.append((url, dict(params or {})))
        listings = {
            "/campaigns": [CAMPAIGN_ROW],
            "/adsets": [ADSET_ROW],
            "/ads": [AD_ROW],
        }
        for suffix, rows in listings.items():
            if url.endswith(suffix):
                return _StubResponse({"data": rows})
        if url.endswith("/insights"):
            if self.fail_insights:
                # Graph-style error payload (e.g. error 100 on a bad field).
                return _StubResponse({"error": {"message": "(#100) invalid field"}})
            return _StubResponse({"data": []})
        # Direct entity reads: GET /{id}
        for row in (CAMPAIGN_ROW, ADSET_ROW, AD_ROW):
            if url.endswith("/" + row["id"]):
                return _StubResponse(row)
        return _StubResponse({"data": []})


@pytest.fixture(autouse=True)
def token(monkeypatch):
    """Every MetaAdapter transport call needs a bearer token present — the
    adapter refuses to guess credentials, so the whole module runs with one."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtesttoken0000000000000")


class TestInsightsFailure:
    def test_failing_insights_is_a_gap_not_a_crash(self):
        """§10 breaker 2: one failing insights call must surface as
        pull_complete=False — never an AdapterError past the pipeline."""
        adapter = MetaAdapter(
            allowed_account_ids=(ACCOUNT,), session=_StubSession(fail_insights=True)
        )
        snapshot = adapter.fetch_entities()
        assert snapshot.pull_complete is False
        assert snapshot.errors and any("insights" in e for e in snapshot.errors)
        # The entity still exists; its windows are absent, not zero-filled.
        assert [c.id for c in snapshot.campaigns] == ["120000000000001"]
        assert snapshot.campaigns[0].recent is None

    def test_failing_insights_yields_report_not_exception(self, config):
        """The documented report-only run: writes_allowed=False and a
        rendered report, not a traceback."""
        adapter = MetaAdapter(
            allowed_account_ids=(ACCOUNT,), session=_StubSession(fail_insights=True)
        )
        result = Pipeline(adapter, config).run(now=RUN_NOW)
        assert result.guard.writes_allowed is False
        assert result.guard.urgent is True
        report = render_report(result)
        assert "# Moot run report" in report
        assert "URGENT" in report

    def test_healthy_pull_completes(self):
        adapter = MetaAdapter(
            allowed_account_ids=(ACCOUNT,), session=_StubSession(fail_insights=False)
        )
        snapshot = adapter.fetch_entities()
        assert snapshot.pull_complete is True
        assert snapshot.errors == []
        assert snapshot.ads[0].post_id == "p_g1"  # page prefix stripped


class TestStatusWriteAccountResolution:
    """§6: every status write resolves its owning account first — and the
    ad-set path must request ``account_id`` directly, not through an ``adset``
    sub-field an ad set does not have. The display string "ad set" once
    reached ``_account_of`` and made every ad-set status write fail closed."""

    def _adapter_with_transport(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        seen: dict[str, object] = {}
        adapter._get = lambda path, params: (
            seen.update(path=path, fields=params["fields"]) or {"account_id": ACCOUNT}
        )
        adapter._post = lambda path, params: seen.update(posted=path, **params) or {}
        return adapter, seen

    def test_adset_status_write_requests_account_id_directly(self):
        adapter, seen = self._adapter_with_transport()
        adapter.set_status("as_nz_p1", "adset", "ACTIVE",
                           dry_run=False, validate_only=False)
        assert seen["fields"] == "account_id"
        assert seen["status"] == "ACTIVE"

    def test_campaign_status_write_requests_account_id_directly(self):
        adapter, seen = self._adapter_with_transport()
        adapter.set_status("120000000000001", "campaign", "PAUSED",
                           dry_run=False, validate_only=False)
        assert seen["fields"] == "account_id"

    def test_ad_status_write_resolves_through_its_adset(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        seen: dict[str, object] = {}
        adapter._get = lambda _path, params: (
            seen.update(fields=params["fields"])
            or {"adset": {"account_id": ACCOUNT}}
        )
        adapter._post = lambda _path, params: seen.update(**params) or {}
        adapter.set_status("ad_graduate_a", "ad", "PAUSED",
                           dry_run=False, validate_only=False)
        assert seen["fields"] == "adset{account_id}"


class TestAccountAllowlist:
    def test_bare_graph_id_matches_prefixed_allowlist(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        adapter._check_account("100000000000001")  # Graph's bare form — no raise

    def test_prefixed_id_matches_bare_allowlist(self):
        adapter = MetaAdapter(allowed_account_ids=("100000000000001",))
        adapter._check_account(ACCOUNT)

    def test_prefixed_id_matches_prefixed_allowlist(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        adapter._check_account(ACCOUNT)

    def test_other_account_still_refused(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        with pytest.raises(WriteRefusedError):
            adapter._check_account("act_999999999999999")
        with pytest.raises(WriteRefusedError):
            adapter._check_account("999999999999999")

    def test_empty_allowlist_refuses_everything(self):
        adapter = MetaAdapter(allowed_account_ids=())
        with pytest.raises(WriteRefusedError):
            adapter._check_account(ACCOUNT)


class TestPaginationBackstops:
    def test_constant_cursor_raises_rather_than_loops(self):
        """§11.6: Meta returns a stable cursors.after at list ends; a
        non-advancing cursor chain must raise, not hang a headless run."""
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        calls = {"n": 0}

        def constant_cursor(_path, _params):
            calls["n"] += 1
            return {
                "data": [{"id": f"row{calls['n']}"}],
                "paging": {"cursors": {"after": "stale"}, "next": "https://x"},
            }

        adapter._get = constant_cursor
        with pytest.raises(IncompletePullError, match="repeated"):
            adapter._get_all_pages(f"{ACCOUNT}/ads", {})
        assert calls["n"] == 2  # detected on the second sighting, no loop

    def test_page_ceiling_raises(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        calls = {"n": 0}

        def fresh_cursors_forever(_path, _params):
            calls["n"] += 1
            return {
                "data": [{"id": f"row{calls['n']}"}],
                "paging": {"cursors": {"after": f"c{calls['n']}"}},
            }

        adapter._get = fresh_cursors_forever
        with pytest.raises(IncompletePullError, match="page ceiling"):
            adapter._get_all_pages(f"{ACCOUNT}/ads", {})
        assert calls["n"] == MAX_PAGES

    def test_terminating_pagination_still_works(self):
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        pages = [
            {"data": [{"id": "a"}], "paging": {"cursors": {"after": "c1"}}},
            {"data": [{"id": "b"}], "paging": {"cursors": {}}},
        ]
        adapter._get = lambda _path, _params: pages.pop(0)
        assert [r["id"] for r in adapter._get_all_pages(f"{ACCOUNT}/ads", {})] == ["a", "b"]


class TestLiveFieldRequests:
    def test_insights_requests_real_video_fields(self):
        """video_3s_views is not a Graph field; requesting it fails every
        live insights call. The real field is video_3_sec_watched_actions."""
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        captured = {}

        def capture(_path, params):
            captured.update(params)
            return []

        adapter._get_all_pages = capture
        adapter._insights("120000000000001", "recent")
        assert "video_3_sec_watched_actions" in captured["fields"]
        assert "video_thruplay_watched_actions" in captured["fields"]
        assert "video_3s_views" not in captured["fields"]

    def test_list_ads_in_campaign_enumerates_archived(self):
        """§9 A2: "all statuses" includes ARCHIVED, which Graph omits by
        default — a post surviving only on an archived copy must still be
        found by the idempotency check."""
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        captured = {}

        def capture(_path, params):
            captured.update(params)
            return []

        adapter._get_all_pages = capture
        adapter.list_ads_in_campaign("120000000000004")
        assert "ARCHIVED" in json.loads(captured["effective_status"])
        assert "ACTIVE" in json.loads(captured["effective_status"])
        assert "PAUSED" in json.loads(captured["effective_status"])

    def test_named_preset_only(self):
        """§11.7: never combine an explicit range with a preset — the adapter
        only ever sends time_preset."""
        adapter = MetaAdapter(allowed_account_ids=(ACCOUNT,))
        captured = {}
        adapter._get_all_pages = lambda _path, params: captured.update(params) or []
        adapter._insights("120000000000001", "recent")
        assert captured["time_preset"] == "last_7d"
        assert "time_range" not in captured


class TestProtocolConformance:
    def test_meta_satisfies_the_adapter_protocol(self):
        assert isinstance(MetaAdapter(allowed_account_ids=(ACCOUNT,)), AdPlatformAdapter)

    def test_fixture_satisfies_the_adapter_protocol(self):
        assert isinstance(FixtureAdapter(FIXTURES), AdPlatformAdapter)

    def test_fixture_read_backs_resolve(self):
        """The mechanism-9 read-back targets exist on the fixture backend."""
        adapter = FixtureAdapter(FIXTURES)
        assert adapter.get_adset("as_nz_retired").name == "Retired winners"
        assert adapter.get_campaign("120000000000004").daily_budget == 400


class TestGetters:
    def test_get_adset_reads_status(self):
        adapter = MetaAdapter(
            allowed_account_ids=(ACCOUNT,), session=_StubSession()
        )
        assert adapter.get_adset("as_nz_p1").effective_status == "ACTIVE"

    def test_get_campaign_reads_budget(self):
        adapter = MetaAdapter(
            allowed_account_ids=(ACCOUNT,), session=_StubSession()
        )
        assert adapter.get_campaign("120000000000001").daily_budget == 400.0


class TestBudgetCurrencyUnits:
    """Graph speaks minor units (cents); Moot speaks major units everywhere else.

    Getting this boundary wrong is silent and expensive in both directions, so
    it is pinned from both sides. See MINOR_UNITS_PER_MAJOR in adapters/meta.py.
    """

    def test_read_converts_minor_to_major(self):
        from moot.adapters.meta import _budget_to_major

        assert _budget_to_major("10000") == 100.0   # $100.00 on the wire as cents
        assert _budget_to_major(13000) == 130.0
        assert _budget_to_major(None) is None       # absent stays absent
        assert _budget_to_major("nonsense") is None

    def test_write_converts_major_to_minor_integer(self):
        from moot.adapters.meta import _budget_to_minor

        # $130 must go out as 13000, not "130.00" — which Graph reads as $1.30
        # and which would quietly throttle the campaign to nothing.
        assert _budget_to_minor(130.0) == 13000
        assert _budget_to_minor(99.99) == 9999
        assert _budget_to_minor(0.5) == 50
        assert isinstance(_budget_to_minor(130.0), int)

    def test_round_trip_is_stable(self):
        from moot.adapters.meta import _budget_to_major, _budget_to_minor

        for major in (12.34, 100.0, 130.0, 1999.95):
            assert _budget_to_major(_budget_to_minor(major)) == major
