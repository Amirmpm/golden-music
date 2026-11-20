# TEST_RESULTS — Golden Music v3.1

Run date: 2026-11-20. Environment: Linux sandbox, PyQt6 6.11, Qt offscreen
(`QT_QPA_PLATFORM=offscreen`), Python 3.12 — the same headless technique the
project's own CI uses. Windows surfaces execute through the windows-latest
GitHub Actions runners (Release workflow runs the full suite list + installer
smoke test).

## Full battery on the v3.1 remediation branch (exit codes verified)

| Suite | Result | Exit |
|-------|--------|------|
| test_functional | 23 passed, 0 failed | 0 |
| test_deep | 23 passed, 0 failed | 0 |
| test_stability | 11 passed, 0 failed | 0 |
| test_stability_v2 | 11 passed, 0 failed | 0 |
| test_history_nav | 11 passed, 0 failed | 0 |
| test_features (incl. v3.1 hook) | 15 + 62 passed, 0 failed | 0 |
| test_crash | 12 passed, 0 crashes | 0 |
| test_tray_toggle | pass | 0 |
| test_open_with | pass | 0 |
| test_shortcuts | pass | 0 |
| test_updater | pass | 0 |
| test_version_sync | 10/10 checks | 0 |
| test_v3_regression | 46 passed, 0 failed | 0 |
| **test_v31_regression (new)** | **62 passed, 0 failed** | **0** |

Note: an interpreter ABRT (exit 134) after a green summary on the first runs
was traced to the new always-on Home cover loader never being joined when a
test path exits without closeEvent — fixed with the idempotent
`_shutdown_v3_workers` registered on closeEvent AND atexit; all suites then
exited 0 (verified against the v3.0.0 baseline which exits 0).

## v3.1 suite coverage detail (62 checks)

- DM-01 free_bytes cross-platform: real dir, missing dir, shutil-based (3)
- Download lifecycle E2E (file://): completion, byte-exact file, no .part,
  history auto-record, atomic state file, restart history load, in-flight
  duplicate refusal, disk-space floor failure, queued cancel (10)
- DM-03 registry retry: fail → FAILED addressable → source fixed → retry
  completes (3)
- DM-02/05 via real throttled Range-capable HTTP server: RUNNING mid-flight,
  ACTIVE pause (state PAUSED + .part bytes), stop → restart restore with
  .part progress, resume-after-restart completes over Range (206), byte-exact
  resumed file, ACTIVE cancel (state + .part removed only after worker close),
  worker health after cancel, bounded shutdown join (11)
- ON-04 eligibility: public/restricted/dark/garbage metadata, unknown-by-
  default search results, restricted-track refusal, hashable OnlineTrack (7)
- ON-05 proxy: install/clear/invalid-refused (3)
- Q-01 unified queue: local plays, online Play-next lands next, local finish
  advances INTO online, stream committed, repeat-one online restart, online
  finish advances to next online, tail returns to local, stale resolve
  rejected, current resolve commits, artwork token guard (10)
- ON-01 search: stale results rejected, current render, correct cache key (3)
- UI-02 Home: distinct album grouping, see-all artists navigation (3)
- UI-01: Open Album plays that album's tracks in order (1)
- Q-02: queue drawer wired, mixed rendering (1)
- Album tag extraction via background loader (1)
- UI-03: async covers delivered; no synchronous pixmap read on GUI thread (2)
- UI-04: in-place row refresh; remove action (2)
- DM-06: closeEvent completes; state persisted (3)

## Windows verification (Part 16)

The Release workflow (windows-latest) runs the complete suite list above —
including the v3.1 regressions riding test_features — plus a silent
install → upgrade → uninstall smoke test on a real Windows runner before
any release is published. Windows-specific APIs (SMTC, registry, media keys)
degrade gracefully off-Windows and are covered by the labeled checks.
Manual interactive Windows verification was NOT performed in this
environment (no Windows machine) — runner evidence only, as allowed by the
spec's fallback rule.
