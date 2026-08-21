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
