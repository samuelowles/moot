"""The write safety layer: dry-run default, read-only env, envelope,
budget cap, audit JSONL, verification. Fixture adapter only — no network."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from conftest import FIXTURES

from moot.adapters.base import AdapterError, PostIdMismatchError
from moot.adapters.fixture import FixtureAdapter
from moot.config import EnvelopeConfig
from moot.guards import GuardVerdict
from moot.models import Action
from moot.writes import (
    ALLOWED_VERBS,
    BUDGET_INCREASE_HARD_CAP_PCT,
    DISPATCHED,
    DRY_RUN,
    FAILED,
    FAILED_VERIFY,
    PROPOSED,
    SKIPPED_DEPENDENCY,
    SKIPPED_READ_ONLY,
    dispatch,
    read_only_env,
)

ALLOWED = GuardVerdict(writes_allowed=True)
BLOCKED = GuardVerdict(writes_allowed=False, reasons=["CIRCUIT BREAKER 2: partial pull"])


def run_dispatch(adapter, config, actions, *, confirm_write=False, audit=None):
    return dispatch(
        actions, adapter, config, ALLOWED,
        confirm_write=confirm_write, audit_path=audit,
    )


class TestDryRunDefault:
    def test_dry_run_dispatches_nothing(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")])
        assert adapter.writes == []
        assert all(o.outcome == DRY_RUN for o in result.outcomes)
        assert result.dispatched_count == 0

    def test_confirm_write_dispatches(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        assert adapter.writes, "confirm_write with allowed guards must dispatch"
        assert result.dispatched_count == 1

    def test_guard_blocks_override_confirm(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = dispatch(
            [Action(verb="ad.pause", target_id="ad_kill_a")], adapter, config, BLOCKED,
            confirm_write=True,
        )
        assert adapter.writes == []
        assert all(o.outcome == "skipped-guard" for o in result.outcomes)


class TestReadOnlyEnv:
    def test_read_only_forces_propose_only_over_confirm_write(self, config, monkeypatch):
        """MOOT_READ_ONLY=1 beats --confirm-write: nothing dispatches."""
        monkeypatch.setenv("MOOT_READ_ONLY", "1")
        adapter = FixtureAdapter(FIXTURES)
        result = dispatch(
            [Action(verb="ad.pause", target_id="ad_kill_a")], adapter, config, ALLOWED,
            confirm_write=True,
        )
        assert adapter.writes == []
        assert all(o.outcome == SKIPPED_READ_ONLY for o in result.outcomes)
        assert result.dispatched_count == 0

    def test_read_only_env_parsed(self, monkeypatch):
        monkeypatch.setenv("MOOT_READ_ONLY", "1")
        assert read_only_env() is True
        monkeypatch.setenv("MOOT_READ_ONLY", "")
        assert read_only_env() is False


class TestEnvelope:
    def test_out_of_envelope_downgraded_not_executed(self, config):
        """An EXECUTABLE verb left outside the config envelope is downgraded
        to a proposal (mechanism 5). Verbs with no executor at all (e.g.
        adset.budget_increase, campaign.create) are refused by the allowlist
        instead — see TestVerbAllowlist."""
        adapter = FixtureAdapter(FIXTURES)
        narrow = replace(
            config,
            envelope=EnvelopeConfig(authorized=frozenset({"ad.pause"})),
        )
        sneaky = Action(verb="ad.activate", target_id="ad_kill_a", authorized=True)
        result = run_dispatch(adapter, narrow, [sneaky], confirm_write=True)
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED
        assert "envelope" in result.outcomes[0].detail

    def test_forbidden_verb_downgraded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        narrow = replace(
            config,
            envelope=EnvelopeConfig(
                authorized=frozenset({"ad.pause", "ad.activate"}),
                forbidden=frozenset({"ad.activate"}),
            ),
        )
        result = run_dispatch(
            adapter, narrow,
            [Action(verb="ad.activate", target_id="ad_kill_a", authorized=True)],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED

    def test_unauthorized_action_flag_downgraded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="ad.pause", target_id="a", authorized=False)],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == PROPOSED


class TestBudgetCap:
    def test_budget_step_clamped_to_thirty_percent(self, config):
        """§8: the cap is in CODE. A plan asking +50% dispatches +30%."""
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 50.0, "current_daily_budget": 400.0, "new_daily_budget": 600.0},
        )
        run_dispatch(adapter, config, [action], confirm_write=True)
        assert adapter.writes
        budget_write = adapter.writes[0]
        assert budget_write["daily_amount"] == pytest.approx(400 * 1.30)
        assert BUDGET_INCREASE_HARD_CAP_PCT == 30.0

    def test_clamp_note_recorded(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 45.0, "current_daily_budget": 100.0, "new_daily_budget": 145.0},
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        assert "clamped" in result.outcomes[0].detail

    def test_step_within_cap_untouched(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params={"pct": 25.0, "current_daily_budget": 400.0, "new_daily_budget": 500.0},
        )
        run_dispatch(adapter, config, [action], confirm_write=True)
        assert adapter.writes[0]["daily_amount"] == pytest.approx(500.0)


class TestAudit:
    def test_audit_jsonl_written_for_dry_runs(self, config, tmp_path):
        audit = tmp_path / "audit.jsonl"
        run_dispatch(
            FixtureAdapter(FIXTURES), config,
            [Action(verb="ad.pause", target_id="ad_kill_a")],
            audit=audit,
        )
        lines = audit.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["verb"] == "ad.pause"
        assert entry["outcome"] == DRY_RUN
        assert entry["confirm_write"] is False

    def test_audit_records_every_action_including_skips(self, config, tmp_path, monkeypatch):
        monkeypatch.setenv("MOOT_READ_ONLY", "1")
        audit = tmp_path / "audit.jsonl"
        actions = [
            Action(verb="ad.pause", target_id="a1"),
            Action(verb="campaign.budget_decrease", target_id="c1"),
        ]
        run_dispatch(FixtureAdapter(FIXTURES), config, actions, audit=audit,
                     confirm_write=True)
        entries = [json.loads(line) for line in
                   audit.read_text(encoding="utf-8").strip().splitlines()]
        assert len(entries) == 2
        assert all(e["outcome"] == SKIPPED_READ_ONLY for e in entries)

    def test_no_audit_file_no_crash(self, config):
        result = run_dispatch(
            FixtureAdapter(FIXTURES), config, [Action(verb="ad.pause", target_id="a")]
        )
        assert result.audit_path is None
        assert result.outcomes[0].outcome == DRY_RUN


class TestNoDelete:
    def test_delete_verb_refused(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.delete", target_id="x")],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == "failed"
        assert "no delete verb" in result.outcomes[0].detail


class TestVerification:
    def test_pause_verified_by_re_read(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        verify = result.outcomes[0].verify
        assert verify and verify["verified"] is True
        assert adapter.get_ad("ad_kill_a").effective_status == "PAUSED"

    def test_duplicate_chain_verifies_post_id(self, config):
        adapter = FixtureAdapter(FIXTURES)
        create = Action(
            verb="adset.create_cohort", target_id="120000000000004",
            params={"name": "2026-08 winners", "campaign_id": "120000000000004",
                    "pixel_id": "400000000000001"},
        )
        duplicate = Action(
            verb="duplicate.post_id", target_id="ad_graduate_a",
            params={
                "destination_campaign_id": "120000000000004",
                "destination_adset_id": None,
                "destination_adset_name": "2026-08 winners",
                "destination_stage": "SCALE",
                "page_id": "100000000000001",
                "activate_after_verify": True,
            },
        )
        result = run_dispatch(adapter, config, [create, duplicate], confirm_write=True)
        assert result.outcomes[0].outcome == "dispatched"
        outcome = result.outcomes[1]
        assert outcome.outcome == "dispatched"
        new_id = outcome.verify["new_ad_id"]
        assert adapter.get_ad(new_id).post_id == "p_g1"
        assert outcome.verify["activation"]["verified"] is True

    def test_duplicate_without_destination_adset_is_proposed(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="duplicate.post_id", target_id="ad_graduate_a",
            params={"destination_campaign_id": "120000000000004",
                    "destination_stage": "SCALE", "page_id": "100000000000001"},
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        assert result.outcomes[0].outcome == "proposed"
        assert adapter.writes == []

    def test_url_tags_survive_the_copy(self, config):
        adapter = FixtureAdapter(FIXTURES)
        action = Action(
            verb="duplicate.post_id", target_id="ad_fatigue",
            params={
                "destination_campaign_id": "120000000000006",
                "destination_adset_id": "as_nz_retired",
                "destination_stage": "RESERVE",
                "page_id": "100000000000001",
            },
        )
        result = run_dispatch(adapter, config, [action], confirm_write=True)
        new_id = result.outcomes[0].verify["new_ad_id"]
        assert adapter.get_ad(new_id).url_tags == "utm_source=meta&utm_campaign=proving"


# --- review fixes: clamp bypasses, verb allowlist, sequencing, redaction -------


class TestBudgetBypassClamped:
    """§8: the clamp must hold whatever shape the request takes — the plan's
    own new_daily_budget is never dispatched verbatim."""

    def _increase(self, params):
        return Action(
            verb="campaign.budget_increase", target_id="120000000000004",
            params=params,
        )

    def test_amount_only_bypass_clamped(self, config):
        """Proven bypass: {current: 100, new: 500} with no pct sent 500."""
        adapter = FixtureAdapter(FIXTURES)
        run_dispatch(
            adapter, config,
            [self._increase({"current_daily_budget": 100, "new_daily_budget": 500})],
            confirm_write=True,
        )
        assert adapter.writes[0]["daily_amount"] == pytest.approx(130.0)

    def test_pct_bypass_clamped(self, config):
        adapter = FixtureAdapter(FIXTURES)
        run_dispatch(
            adapter, config,
            [self._increase({"pct": 400.0, "current_daily_budget": 100.0,
                             "new_daily_budget": 500.0})],
            confirm_write=True,
        )
        assert adapter.writes[0]["daily_amount"] == pytest.approx(130.0)

    def test_under_cap_amount_recomputed_not_forwarded(self, config):
        """Even under the cap the dispatched amount is recomputed from
        current × (1 + pct/100), never the plan's verbatim figure."""
        adapter = FixtureAdapter(FIXTURES)
        run_dispatch(
            adapter, config,
            [self._increase({"pct": 25.0, "current_daily_budget": 100.0,
                             "new_daily_budget": 999.0})],
            confirm_write=True,
        )
        assert adapter.writes[0]["daily_amount"] == pytest.approx(125.0)

    def test_missing_current_budget_fails_without_dispatch(self, config):
        """No numeric current ⇒ the cap cannot be verified ⇒ FAILED, never
        dispatched."""
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [self._increase({"pct": 25.0, "new_daily_budget": 125.0})],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == FAILED
        assert "current_daily_budget" in result.outcomes[0].detail

    def test_amount_only_without_current_budget_fails(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [self._increase({"new_daily_budget": 500})],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == FAILED


class TestVerbAllowlist:
    """An explicit ALLOWED-verb allowlist replaces the substring denylist —
    remove/destroy/archive are refused by the same mechanism."""

    @pytest.mark.parametrize(
        "verb", ["ad.delete", "ad.remove", "ad.destroy", "ad.archive",
                 "campaign.delete", "adset.archive"]
    )
    def test_destructive_verbs_refused_on_sight(self, config, verb):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config, [Action(verb=verb, target_id="x")], confirm_write=True
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == FAILED

    def test_non_executable_authorized_verb_refused(self, config):
        """campaign.pause is envelope-authorized in the example config but
        has no executor — FAILED is more honest than a silent proposal."""
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="campaign.create", target_id="x", authorized=True)],
            confirm_write=True,
        )
        assert adapter.writes == []
        assert result.outcomes[0].outcome == FAILED
        assert "allowlist" in result.outcomes[0].detail

    def test_proposal_with_non_executable_verb_stays_proposal(self, config):
        """Pipeline proposals (budget.bid_review, blocked duplicates) carry
        non-executable verbs by design — they stay proposals, not failures."""
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="budget.bid_review", target_id="x", authorized=False)],
            confirm_write=True,
        )
        assert result.outcomes[0].outcome == PROPOSED

    def test_every_executable_verb_is_sane(self):
        assert "delete" not in " ".join(ALLOWED_VERBS)
        assert "ad.pause" in ALLOWED_VERBS


def _retirement_pair(source_id="ad_fatigue"):
    """What the pipeline emits for a §6 retirement, in dispatch order."""
    return [
        Action(
            verb="duplicate.post_id", target_id=source_id,
            params={
                "destination_campaign_id": "120000000000006",
                "destination_adset_id": "as_nz_retired",
                "destination_stage": "RESERVE",
                "page_id": "100000000000001",
                "activate_after_verify": True,
            },
        ),
        Action(
            verb="ad.pause", target_id=source_id,
            params={"requires_verified_duplicate_of": source_id},
        ),
    ]


class _DuplicateFailure(FixtureAdapter):
    def duplicate_post(self, *_args, **_kwargs):
        raise AdapterError("creative creation refused (test)")


class TestVerifiedDuplicateSequencing:
    """§6/§7: the source pause only follows a copy that verified ACTIVE."""

    def test_failed_duplication_does_not_pause_source(self, config):
        adapter = _DuplicateFailure(FIXTURES)
        actions = _retirement_pair()
        result = run_dispatch(adapter, config, actions, confirm_write=True)
        duplicate, pause = result.outcomes
        assert duplicate.outcome == FAILED
        assert pause.outcome == SKIPPED_DEPENDENCY
        # Nothing paused the source: no status write, entity still ACTIVE.
        assert not [
            w for w in adapter.writes
            if w["method"] == "set_status" and w["entity_id"] == "ad_fatigue"
        ]
        assert adapter.get_ad("ad_fatigue").effective_status == "ACTIVE"

    def test_verified_duplication_then_pauses_source(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(adapter, config, _retirement_pair(), confirm_write=True)
        duplicate, pause = result.outcomes
        assert duplicate.outcome == DISPATCHED
        assert duplicate.verify["activation"]["verified"] is True
        assert pause.outcome == DISPATCHED
        assert adapter.get_ad("ad_fatigue").effective_status == "PAUSED"

    def test_pause_without_requires_param_dispatches_normally(self, config):
        """A KILL pause (no copy expected) has no dependency and must not be
        held by the sequencing mechanism."""
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        assert result.outcomes[0].outcome == DISPATCHED


class _StatusNoOp(FixtureAdapter):
    """Accepts the write but never applies it — the read-back contradicts."""

    def set_status(self, entity_id, entity_type, status, *, dry_run, validate_only):
        self._record(
            "set_status", entity_id=entity_id, entity_type=entity_type,
            status=status, dry_run=dry_run, validate_only=validate_only,
        )


class TestFailedVerify:
    def test_pause_readback_contradiction_is_failed_verify(self, config):
        adapter = _StatusNoOp(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="ad.pause", target_id="ad_kill_a")],
            confirm_write=True,
        )
        assert result.outcomes[0].outcome == FAILED_VERIFY
        assert result.dispatched_count == 0
        assert "read-back" in result.outcomes[0].detail

    def test_adset_pause_readback_verified(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(verb="reserve.reactivate", target_id="as_nz_retired")],
            confirm_write=True,
        )
        assert result.outcomes[0].outcome == DISPATCHED
        assert result.outcomes[0].verify["verified"] is True
        assert adapter.get_adset("as_nz_retired").effective_status == "ACTIVE"
        # The live adapter resolves the owning account by branching on this
        # exact token — the display string "ad set" once reached it and broke
        # every ad-set status write against the real Graph API.
        recorded = [w for w in adapter.writes if w["method"] == "set_status"]
        assert recorded and recorded[0]["entity_type"] == "adset"

    def test_budget_readback_verified(self, config):
        adapter = FixtureAdapter(FIXTURES)
        result = run_dispatch(
            adapter, config,
            [Action(
                verb="campaign.budget_decrease", target_id="120000000000004",
                params={"pct": 30.0, "current_daily_budget": 400.0,
                        "new_daily_budget": 280.0},
            )],
            confirm_write=True,
        )
        assert result.outcomes[0].outcome == DISPATCHED
        assert result.outcomes[0].verify["verified"] is True
        assert adapter.get_campaign("120000000000004").daily_budget == pytest.approx(280.0)


class _PostIdMismatch(FixtureAdapter):
    def duplicate_post(self, source_ad, *_args, **_kwargs):
        raise PostIdMismatchError(
            f"duplicated ad carries post 'p_other', expected {source_ad.post_id!r}"
        )


class TestPostIdMismatch:
    def test_mismatch_raises_loudly_and_is_audited(self, config, tmp_path):
        """writes.md mechanism 9: a post-ID mismatch raises loudly — never
        accepted as success — and lands in the audit first."""
        audit = tmp_path / "audit.jsonl"
        with pytest.raises(PostIdMismatchError):
            run_dispatch(
                _PostIdMismatch(FIXTURES), config,
                [Action(
                    verb="duplicate.post_id", target_id="ad_fatigue",
                    params={"destination_campaign_id": "120000000000006",
                            "destination_adset_id": "as_nz_retired",
                            "destination_stage": "RESERVE",
                            "page_id": "100000000000001"},
                )],
                confirm_write=True, audit=audit,
            )
        entry = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry["outcome"] == FAILED_VERIFY
        assert "p_other" in entry["detail"]


class TestAuditRedaction:
    """writes.md §4 / SECURITY.md: the audit redacts anything token-shaped."""

    TOKEN = "EAAXy9aBcDeFgHiJkLmNoPqRsTuV"

    def test_tokens_redacted_in_params_and_exception_detail(self, config, tmp_path):
        audit = tmp_path / "audit.jsonl"
        token = self.TOKEN

        class _Leaky(FixtureAdapter):
            def set_status(self, *_args, **_kwargs):
                raise AdapterError(f"write failed: bearer {token} rejected")

        action = Action(
            verb="ad.pause", target_id="ad_kill_a",
            params={
                "note": f"bearer {token}",
                "url": "https://graph.facebook.com/?access_token=SECRET123&x=1",
            },
        )
        result = run_dispatch(_Leaky(FIXTURES), config, [action],
                              confirm_write=True, audit=audit)
        assert result.outcomes[0].outcome == FAILED
        raw = audit.read_text(encoding="utf-8")
        assert token not in raw
        assert "SECRET123" not in raw
        entry = json.loads(raw.strip().splitlines()[-1])
        assert entry["params"]["note"] == "bearer [REDACTED]"
        assert "[REDACTED]" in entry["detail"]
        assert entry["params"]["url"].startswith("https://graph.facebook.com/?")

    def test_redaction_is_recursive(self):
        from moot.writes import _redact

        record = {
            "a": ["x", "EAA" + "z" * 25],
            "b": {"c": "access_token=abc&y=2", "d": 3},
        }
        out = _redact(record)
        assert out["a"][1] == "[REDACTED]"
        assert out["b"]["c"] == "[REDACTED]"
        assert out["b"]["d"] == 3


class TestReadOnlyVariants:
    """A kill switch must not be case- or spelling-fragile."""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "yes", "y", "Y",
                                       "on", "ON", "enabled", "T"])
    def test_truthy_spellings_are_on(self, monkeypatch, value):
        monkeypatch.setenv("MOOT_READ_ONLY", value)
        assert read_only_env() is True

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "No", "off",
                                       "OFF", "", "  "])
    def test_off_words_are_off(self, monkeypatch, value):
        monkeypatch.setenv("MOOT_READ_ONLY", value)
        assert read_only_env() is False

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("MOOT_READ_ONLY", raising=False)
        assert read_only_env() is False
