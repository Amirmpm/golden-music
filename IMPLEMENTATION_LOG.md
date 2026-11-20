# IMPLEMENTATION_LOG — Golden Music v3.1

Starting commit: 16e6bf8 (v3.0.0). Branch: feature/v3.1-remediation.

## Session 1 — 2026-11-20

### Phase 1 — repository verification (DONE)
- Remote main == 16e6bf8 == local (newest line). Older divergent v2.3.0 line
  (tag 8500ce6, branches feature/2.3.0, diag/*, backup/*) preserved untouched.
- Working branch created from main; rollback = checkout main.

### Phase 2 — audit (DONE)
- Full re-inspection of downloads.py, online.py, main.pyw queue/shutdown/race
  paths, search_page.py, home_page.py, coverart.py, downloads_page.py,
  queue_panel.py, settings_dialog.py, lyrics_panel.py, fullscreen_player.py,
  update_dialog.py, config.py + tests. 21 findings (6 Critical, 9 High,
  6 Medium) — every one reproduced from code evidence (BUG_TRACKER.md).

### Phase 3 — download manager rebuild (DONE)
- downloads.py: shutil.disk_usage free_bytes; persistent job registry
  (id → job, any state); cancel checks _current FIRST and only signals the
  worker (worker closes handle + removes .part); retry via registry;
  atomic downloads_state.json persistence (schema version, quarantine);
  restart restore (QUEUED/PAUSED, Range resume, corrupt-.part guard,
  from_dict carries dest_path); duplicate-URL refusal; remove/clear_terminal;
  stop_and_join cancels active transfer + bounded 10 s join.
- online.py: downloads_allowed tri-state (None=unknown); eligibility from
  item metadata (access-restricted-item / is_dark / private file); shared
  proxy-aware urlopen + set_proxy/proxy_url.
- main.pyw: manager init with state file + history migration; _on_download_
  completed no longer double-records; downloads page remove/clear/reveal
  wired.

### Phase 4 — worker lifecycle / shutdown (DONE)
- closeEvent force branch now joins: stream resolvers (cancel+wait),
  artwork worker, update probe (new cancel), search workers (new
  cancel_search), home cover loader, download manager (persist+join),
  audio.stop.
- `_shutdown_v3_workers` idempotent + registered on atexit (fixed the
  interpreter abort at teardown found during regression runs).

### Phase 5 — unified queue (DONE)
- current_playlist entries: str | OnlineTrack. _load_and_play_current
  branches; _on_track_finished = _on_next for both kinds; online
  Play-next/Add-to-queue insert into the unified queue; repeat-one covers
  online entries; _play_path_from_playlist accepts entries; history/shuffle
  bookkeep online tracks (OnlineTrack identity __hash__).
- QueuePanel wired as "Up Next" drawer (player-bar queue button).

### Phase 6 — races (DONE)
- Stream resolve: worker identity guard + cancel previous.
- Download resolve: same guard + post-resolve eligibility re-check.
- Online artwork: request token guard.
- Search: generation guard at delivery, cache keyed by worker query,
  zombie worker management, no GUI-blocking wait.

### Phase 7 — Home (DONE)
- Album tags: TagLoaderThread emits (path,title,artist,album); extract_album
  in config.py; _refresh_home feeds album tags; distinct album cards.
- Actions: _home_add_to_playlist (selected track), _home_open_album (that
  album), see-all artists mapping.
- Async covers: HomeCoverLoader QThread (QImage off-thread, bounded,
  dedup, cancellable); pixmap cache; single-scale cards.

### Phase 8 — settings completion (DONE)
- Lyrics font size + timing offset (both lyric surfaces, persisted,
  validated, live-applied); custom proxy; Shortcuts settings tab;
  QueueDrawer. EQ documented as technical blocker.

### Phase 12 — regression suite (DONE)
- scripts/test_v31_regression.py: 62 checks (see TEST_RESULTS.md).
  Hooked into test_features (CI-listed) because the token lacks workflow
  scope — the workflow file list cannot be pushed; expansion patch prepared
  (ci-workflow-expansion-v3.1-pending-workflow-scope.patch).
- Fixed test-races exposed by the new async history recording (V9 pump).

### Phase 15 — release prep (DONE)
- Version 3.1.0 across version.py/iss/README/INSTALLATION/CHANGELOG/
  RELEASE_NOTES; version-sync 10/10.
- AUDIT_REPORT/NUVX_SETTINGS_PARITY/status files updated.

### Verification
All 14 suites green locally (TEST_RESULTS.md). Windows runner verification
via the Release workflow (full suite + installer smoke test).

Next: commit, push branch, merge to main, tag v3.1.0, monitor Actions,
verify the release artifacts.
