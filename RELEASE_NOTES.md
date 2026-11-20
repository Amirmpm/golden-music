# Release Notes — Golden Music 3.1.0

3.1.0 is the engineering-remediation release: every Critical and High defect
from the v3.0.0 audit is fixed, verified by the new `test_v31_regression`
suite (62 checks) riding CI, plus the existing 13 suites (all green).

## Download manager (rebuilt)
- Windows-safe disk-space validation (`shutil.disk_usage`; `os.statvfs`
  crashed with an uncaught AttributeError on Windows)
- Active-job cancellation signals the worker — never unlinks a `.part`
  the worker still holds open (PermissionError on Windows before)
- Persistent job REGISTRY: `retry()` actually finds FAILED jobs (it was a
  silent no-op in 3.0.0)
- History + queue persist atomically to `downloads_state.json` —
  restart recovery resumes unfinished downloads via HTTP Range
- In-flight duplicate-URL prevention; remove / clear-finished actions
- Completed downloads join the local library exactly once

## Unified playback queue
- ONE queue for local files AND online tracks — "Play next", Next,
  Previous, repeat-one and shuffle behave identically across both
  (v3.0's separate online queue never actually played next)
- "Up Next" queue drawer wired into the player bar (the dead QueuePanel
  module is live again)

## Online correctness
- Item-specific download eligibility from the Internet Archive's own
  access metadata (no more blanket "everything is downloadable")
- Stream-resolution and search races fixed with request generations —
  stale responses can never replace a newer selection or pollute the
  search cache
- Custom HTTP(S) proxy setting (search/stream/artwork/downloads)

## Home
- Album-aware cards (real album tags, distinct albums)
- "Add to Playlist" / "Open Album" act on the SELECTED item
- Async cover loading — artwork reads + decodes off the GUI thread
- "See all artists" navigates (was a silent no-op)

## Lyrics settings
- Text size (85–130%) and timing offset (±5 s) — persisted, applied live

## Shutdown reliability
- Coordinated, bounded shutdown of every background worker
  (download worker, stream resolvers, artwork, update probe, cover
  loader) — no more interpreter aborts at teardown

See CHANGELOG.md for the full list and BUG_TRACKER.md for root causes.
