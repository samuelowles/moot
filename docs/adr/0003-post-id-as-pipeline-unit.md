# ADR-0003 — The post ID is the unit of the pipeline

**Status:** Accepted · **Affects:** `duplication.py`, `adapters/meta.py`

## Context

The platform has no native "move ad" operation. An ad belongs to the ad set it
was created in, permanently. Any staged pipeline therefore needs a way to
represent movement, and there are only two candidates.

**Recreate the ad in the destination.** Simple, and it silently destroys the
asset. Every published ad is backed by a page post; creating a fresh ad mints a
new post with zero reactions, zero comments, zero shares and a reset
learning phase. The thing being "promoted" arrives at the destination stripped
of exactly the accumulated proof that made it worth promoting.

**Reference the existing post.** A new ad created from the source's
`effective_object_story_id` inherits the post's engagement and its ranking
signal. The pipeline stage changes; the asset does not.

## Decision

**Every transition is a post-ID duplication.** Read the source's post ID →
create a creative from that post ID in the destination account → create an ad
from that creative, paused → verify the post ID survived → pause the source
if the transition requires it.

Never delete. The source is paused, because the entity ID and its lifetime
metrics are the audit anchor.

Never edit the creative on a duplicated post: edits reset learning and can
detach the post. New hooks go to fresh Proving Ground tests.

Two pre-flight checks are mandatory, and both were written after the naive
version failed in production.

**Deduplicate on post ID, never on name.** Names drift: suffixes get appended by
successive duplications, sources get renamed, and two genuinely different posts
can share a name. A name-based check fails in both directions: in one
observed run it reported two ads carrying different posts as duplicates and
blocked a legitimate promotion, while three copies of a single post accumulated
in another campaign because their names matched a pattern the check tolerated.
Enumerate every ad in the destination *campaign*, all ad sets, all statuses,
paginated to exhaustion, and compare post IDs. A paused existing copy still
means skip: it usually records a prior demotion or a deliberate operator pause,
and re-creating it silently overrides a decision someone already made.

**Derive the destination market from the source campaign**, via the configured
stage map. Never by name similarity, by scanning for an existing
similarly-named copy, or by defaulting. One creative running in two markets
under one name once collapsed into a single destination: the retirement landed
in the wrong market's Reserve while the correct market's twin kept spending
unnoticed for four days.

Both checks report their skips, so a duplication that was considered and not
performed stays in the audit trail.

## Consequences

- Movement is three API calls and a verification read rather than one: slower,
  and the only version that preserves the asset.
- Post ID extraction must use the page prefix from the extracted ID, never
  an assumed default page. Accounts routinely run ads from more than one page,
  and an assumed prefix produces a valid-looking ID that resolves to nothing.
- Tracking parameters must be carried across explicitly or the copy becomes
  invisible to every downstream analytics surface.
- An ad can legitimately exist in two stages at once: the Proving Ground
  original and its Scale copy. This is intended, and it means "which stage is
  this ad in" is a question about a *specific ad ID*, never about a post.
- Verification is not optional. A duplication that silently minted a new post
  looks like a success in every field except the one that mattered, which is
  why `PostIdMismatchError` raises rather than warns.
