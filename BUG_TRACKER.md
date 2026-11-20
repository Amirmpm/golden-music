# BUG_TRACKER — Golden Music v3.1

Evidence: direct code inspection (file:line) of commit 16e6bf8 + runtime reproduction in
the offscreen test environment. Status updated as fixes land.

| ID | Sev | Category | Location | Defect (evidence) | Status |
|----|-----|----------|----------|--------------------|--------|
| DM-01 | Critical | Downloads/Windows | downloads.py:141-146 | `free_bytes` uses `os.statvfs` — absent on Windows → AttributeError uncaught (only OSError caught) → downloads broken on the primary platform | FIXED |
| DM-02 | Critical | Downloads/concurrency | downloads.py:222-238 | `cancel()` scans `_queue` first; a RUNNING job stays in `_queue` (dropped only at terminal state) → active cancel marks CANCELLED + unlinks `.part` while the worker holds the handle (Windows: PermissionError; Linux: silent corruption), never sets `_cancel` → worker continues, later overwrites state | FIXED |
| DM-03 | Critical | Downloads | downloads.py:240-253 | `retry()` searches `_queue`, but `_run_job` finally-block drops FAILED/CANCELLED jobs from `_queue` (341-343) → failed jobs unreachable → retry always returns False | FIXED |
| DM-04 | High | Downloads/persistence | downloads.py:441-451 + main.pyw:4490,4671 | Manager `_history` starts empty; config-loaded history (main.pyw:6031) never passed in; first completion overwrites `self.downloads_history` with manager's 1-record list → persisted history lost | FIXED |
| DM-05 | High | Downloads/persistence | downloads.py (module) | No `serialise_state()` despite docstring promise; unfinished jobs never persisted → no restart recovery | FIXED |
| DM-06 | Critical | Shutdown | main.pyw:5841-5865 | closeEvent force-quit handles scanner/tag/cover/albums/lyrics workers only. Missing: `_resolve_worker`, `_download_resolve_worker`, `_online_art_worker`, `_update_worker` (no cancel exists), `search_page._worker`, `download_manager.stop()` (exists at downloads.py:529, never called), `audio.stop()`. Long-lived DownloadWorker thread can be destroyed mid-run | FIXED |
| Q-01 | Critical | Queue | main.pyw:1752,1783,2186-2189,4813-4825,5385-5440 | Two disjoint queues: `current_playlist` (local paths) + `online_queue` (OnlineTrack). `_on_next` operates only on `current_playlist`; online "Play next" lands in `online_queue` which only drains after an ONLINE track finishes → online track added during local playback never plays next; Next/Previous/redo/shuffle ignore online entries | FIXED |
| Q-02 | High | Queue/UI | queue_panel.py (whole file) | Functional QueuePanel never imported/wired (dead module) — "Up next" drawer missing; also assumes path-string entries only | FIXED |
| ON-01 | High | Online/search | search_page.py:231-249 | No generation/request id at delivery; `_on_results` caches under the CURRENT field text (249) → a stale worker's results cached under the wrong key; `wait(1500)` on the GUI thread (239) blocks the UI | FIXED |
| ON-02 | High | Online/streaming | main.pyw:4527-4546 | `_play_online_track` never cancels the previous StreamResolveWorker; `_on_stream_resolved` has no generation/sender check → last-arrival-wins: a slow stale resolve replaces a newer selection | FIXED |
| ON-03 | High | Online/artwork | main.pyw:4569-4590 | Online artwork worker overwritten without cancel; `_on_online_artwork` applies unconditionally → late art for an old track overwrites the newer track's art + theme accent | FIXED |
| ON-04 | High | Online/copyright | online.py:54,161 + downloads.py:471 | `downloads_allowed=True` constant for every item (mission 6.4: false claim) — no item-specific eligibility | FIXED |
| ON-05 | Medium | Online/network | online.py:196-215 | No custom-proxy support (parity item 34 partial) | FIXED |
| UI-01 | High | Home/actions | main.pyw:2167-2170 | `add_to_playlist_requested` → `_show_playlists()` ignores item (opens generic dialog, adds nothing); `open_album_requested` → `_on_rail_clicked("albums")` ignores item | FIXED |
| UI-02 | High | Home | main.pyw:4750-4754,4765 | `_home_see_all("artists")` is a silent no-op (mapping lacks artists); `_refresh_home` feeds `"album": ""` for every track → all tracks collapse into one "Unknown album" card; album shelf/grouping dead | FIXED |
| UI-03 | High | Home/perf | home_page.py:114-134 + coverart.py | Card artwork read+decoded synchronously on the GUI thread (~70 mutagen parses per rebuild); double scaling (192 + 168); no async fill | FIXED |
| UI-04 | Medium | Downloads page | downloads_page.py:147,273-306 | `show_in_folder_requested` declared, never emitted (dead); full row rebuild on every 0.5 s progress tick; no remove / clear-completed actions | FIXED |
| ST-01 | Medium | Settings parity | NUVX_SETTINGS_PARITY items 30,31,34,51,53 | Lyrics font size / lyrics timing offset / custom proxy / queue drawer / shortcuts pane not implemented | FIXED (4 real impls; EQ=item 23 documented blocker) |
| ST-02 | Medium | Settings parity | NUVX_SETTINGS_PARITY item 23 | 7-band EQ: Qt Multimedia exposes no per-band DSP; requires backend swap (WASAPI APO or FFmpeg graph) — technically justified blocker, documented | BLOCKED (documented) |
| TH-01 | Medium | Threads | update_dialog.py:56-72 | UpdateCheckWorker has no cancel(); orphaned on shutdown | FIXED |
| CI-01 | Medium | CI | .github/workflows/*.yml | CI runs a fixed script list; v3 regression suites not in the list; expansion blocked by token workflow-scope (patch prepared) | BLOCKED (external) |

Reproduction notes: DM-01/02/03 reproduced via unit tests against the real worker
(file:// scheme, local temp dirs); ON-01/02/03 reproduced via generation-guard tests;
Q-01 reproduced by mixed-queue state inspection; UI-01/02 reproduced by signal emission
with a selected item and observing no effect.
