# RELEASE_CHECKLIST — Golden Music v3.1.0

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
- [ ] Fast-forward main + push
- [ ] Tag v3.1.0 + push (triggers Release workflow on windows-latest)
- [ ] Release workflow green (tests → Bandit → PyInstaller → Inno Setup →
      portable zip → SHA-256 → install/upgrade/uninstall smoke test →
      GitHub release)
- [ ] Verify release artifacts published (installer + portable + checksum)
- [ ] Record final evidence + close out COMPLETION_STATUS.md
- [ ] CI expansion patch delivered (workflow-scope-blocked; documented:
      ci-workflow-expansion-v3.1-pending-workflow-scope.patch)
