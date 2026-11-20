# COMPLETION_STATUS — Golden Music v3.1 Remediation

> Living file. Updated after every substantial phase. Never claims completion while
> a mandatory acceptance criterion is unresolved.

## Session 1 — started 2026-11-20

**Starting commit:** `16e6bf8` (branch `feature/v3.1-remediation` created from it)
**Baseline:** v3.0.0 release (published, CI green at that commit)
**Target:** v3.1.0 per the remediation mission spec.

### Repository verification (Part 1) — DONE
- Remote `main` == local `main` == 16e6bf8 (newest work; no unpushed user commits).
- Divergent older line `v2.3.0` (tag 8500ce6 + branches feature/2.3.0, diag/*, backup/*)
  is pre-existing rolled-back work from 2026-11-06 — PRESERVED, not touched.
- Working branch: `feature/v3.1-remediation` (rollback = checkout main).

### Environment
- Local offscreen Qt test capability established (PyQt6 6.11 + local GL libs via LD_LIBRARY_PATH).
- pytest 9.0.2 available; mutagen available.

### Phase progress
- [x] Phase 1 — repository verification
- [ ] Phase 2 — complete audit (in progress)
- [ ] Phase 3 — critical download-manager fixes
- [ ] Phase 4 — worker lifecycle/shutdown
- [ ] Phase 5 — unified queue
- [ ] Phase 6 — search/stream/artwork races
- [ ] Phase 7 — home actions + async artwork
- [ ] Phase 8 — settings completion
- [ ] Phase 9-11 — UI/Windows/perf
- [ ] Phase 12 — regression suite
- [ ] Phase 13 — CI expansion (see BLOCKERS)
- [ ] Phase 14-15 — Windows release + docs

### Known blockers (external, documented per spec §0.4)
- CI workflow file changes cannot be pushed: the available GitHub token lacks the
  `workflow` scope (GitHub rejects workflow updates). CI expansion (Part 14) will be
  prepared as a patch + documented; all other work proceeds.
- Windows-native manual verification is unavailable in this environment; Windows
  verification runs through GitHub Actions windows-latest runners (full test suite +
  install/upgrade/uninstall smoke test) per Part 16 fallback.

### Next unfinished task
Phase 2 audit of downloads.py / online.py / main.pyw / home_page.py.
