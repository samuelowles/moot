# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Three-stage ladder (Proving Ground / Scale / Reserve) with six transitions.
- Target-derived gate set — every performance threshold is a ratio of a single
  configured return target (ADR-0001).
- Rolling per-market baselines from top-quartile cost per cart, with fallback
  and seeded sources reported per run.
- Kill limbs A, B, C1, C2 and the concept-level cart-rate limb, including the
  AOV-relative cost ceiling (ADR-0004).
- Auction-versus-fatigue check as a hard gate on every retirement.
- Post-ID duplication with idempotency on post ID and derived market routing
  (ADR-0003).
- Adversarial council: five opposed archetypes plus an adjudicator, with two
  vetoes enforced in code (ADR-0005).
- Live Meta Graph API adapter and an offline fixture adapter.
- Write safety layer: dry-run default, `AGON_READ_ONLY` kill switch,
  server-side validation, envelope enforcement, budget clamp, append-only
  audit, post-write verification, no delete verb (ADR-0002).
- Claude Code plugin: six agents, two skills, three commands, two pre-dispatch
  hooks, and two scheduled-task templates.

### Fixed (second review pass)
- **Ad-set status writes against the live adapter**: dispatch passed the
  display string `"ad set"` where the Meta adapter branches on `"adset"` to
  resolve the owning account, so every `reserve.reactivate` (and any
  `adset.pause`/`adset.activate`) failed closed with a misleading
  account-resolution error. The §8 Reserve-reactivation capability could
  never actually execute. The entity vocabulary is now a shared `Literal`
  type, so the type checker pins it, and the account-resolution field
  requests are unit-tested per entity type.
- **Windows consoles**: the report's Δ / § / × crashed the CLI with a
  `UnicodeEncodeError` on a legacy codepage (cp1252) before a single line
  printed — the README quickstart died on stock Windows. The CLI now
  re-encodes its own stdio as UTF-8, a cp1252-forcing journey test pins it,
  and CI gained a Windows leg alongside Python 3.13.

### Removed (second review pass)
- Unused API surface: the Round 1/Round 3 brief renderers and the Python
  copy of the opposition map (the plugin layer owns those prompts;
  docs/agents.md owns the map), a parsed-but-never-consulted
  `windows.attribution` config key (§11.8 holds attribution constant in the
  adapter), the unread `reporting.sinks` key, an unused `Gate` protocol, and
  four zero-caller helpers. Net ~190 lines.

### Fixed (pre-release hardening, after an independent review)
- Budget clamp could be bypassed by supplying an amount instead of a
  percentage; the amount is now always recomputed from the current budget, and
  an increase without a known current budget fails rather than dispatching.
- A failed duplication no longer pauses its source ad. Retirement pauses are
  held behind a verified-duplicate dependency, and Reserve copies now activate
  after verification.
- A failing insights call trips circuit breaker 2 instead of raising past the
  reporter.
- Account allowlist now normalises the `act_` prefix, so it can actually match.
- Pagination detects a repeated cursor and a page ceiling instead of looping.
- Audit log redacts token-shaped strings, as the docs already claimed.
- Status and budget writes get a real read-back with a `failed-verify` outcome;
  a post-ID mismatch propagates instead of only logging.
- `AGON_READ_ONLY` accepts the obvious truthy spellings, not just `1`.
- Within-run idempotency: two ads sharing a post ID no longer produce duplicate
  ads and duplicate cohort ad sets in a single run.
- Kill limb D no longer synthesises zeros — a concept whose carts are *absent*
  is not killed for having none.
- Destination policy veto is wired into the pipeline rather than being dead
  code, and `require_tracking_params` is enforced.
- Documented CLI invocations work: options are accepted at both group and
  subcommand level.
- Meta adapter requests the real video action fields, so hook rate is computed
  for video and stays `None` for static.
- **Budget currency units**: Graph returns and accepts budget fields in minor
  units. Reads were 100× high and writes 100× low; both now convert at the
  adapter boundary. Zero-decimal currencies remain a known limitation.
- Added the missing `campaign.pause` executor, so every verb the example config
  authorizes can actually run.
- Council roster now matches the documented charters (it had drifted to
  invented names), and `contested()` requires the gate evidence to show a close
  call — the ratio was above 100% of actions and is now about one in five.
