# RELEASE_CHECKLIST — Golden Music

## v3.2.0 — Nuvx visual accuracy + first Linux release (2026-11-21)

- [x] User's 12-file Nuvx UI rewrite grafted onto the 3.1.0 base
      (method-level three-way merge — 3.1 machinery preserved)
- [x] Linux release machinery: release-linux job (ubuntu-latest),
      ci.yml dual matrix, cross-platform build_release.py
- [x] Version sources synchronized (version.py, iss default, README
      badge/links, INSTALLATION, CHANGELOG 3.2.0 section, RELEASE_NOTES)
      — version-sync 10/10
- [x] Commit v3.2.0 on main; annotated tag v3.2.0
- [x] Push main + tag v3.2.0 (main ec68324..2b885e6, tag pushed with a
      user-supplied PAT after the sandbox lost its stored credential)
- [x] Release workflow green — run #12 on 2b885e6 (windows-latest:
      tests → Bandit → PyInstaller → Inno Setup → portable zip →
      SHA-256 → smoke test → GitHub release; then ubuntu-latest:
      suite → Bandit → tar.gz → SHA-256 → upload into the release)
- [x] Release artifacts verified (installer 30.4 MB + .sha256,
      portable 43.8 MB, linux tar.gz 86.7 MB + .sha256) —
      https://github.com/Amirmpm/golden-music/releases/tag/v3.2.0

## v3.1.0 — remediation release (2026-11-20, closed)

- [x] All Critical/High findings fixed (BUG_TRACKER.md — 15/15, Medium 4/6,
      2 documented blockers)
- [x] Regression suite for every fix (test_v31_regression.py, 62 checks)
- [x] All 14 local suites green (TEST_RESULTS.md)
- [x] Bandit security gate green locally (same command as CI)
- [x] Version sources synchronized (version.py, iss default, README badge,
      download links, CHANGELOG, RELEASE_NOTES) — version-sync 10/10
- [x] Docs updated (AUDIT_REPORT, NUVX_SETTINGS_PARITY, README claims
      checked — no false online/download claims found)
- [x] Status files current (COMPLETION_STATUS, BUG_TRACKER, TEST_RESULTS,
      IMPLEMENTATION_LOG, RELEASE_CHECKLIST)
- [x] Commit + push feature/v3.1-remediation
- [x] Fast-forward main + push
- [x] Tag v3.1.0 + push (triggers Release workflow on windows-latest)
- [x] Release workflow green — run #11 on 583cf55 (tests → Bandit →
      PyInstaller → Inno Setup → portable zip → SHA-256 →
      install/upgrade/uninstall smoke test → GitHub release)
- [x] Release artifacts verified (installer 30.4 MB, portable 43.8 MB,
      .sha256) — https://github.com/Amirmpm/golden-music/releases/tag/v3.1.0
- [x] Final evidence recorded; COMPLETION_STATUS.md closed out
- [ ] CI expansion patch delivered (workflow-scope-blocked; documented:
      ci-workflow-expansion-v3.1-pending-workflow-scope.patch)
