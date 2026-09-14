# GitHub Actions cleanup

Operational and reusable workflows are kept in `.github/workflows`. One-shot development, forensic, bootstrap-only, or workstream-specific smoke/audit workflows should be removed once their evidence is frozen and the production path is established.

Removed in the September 2026 cleanup:
- Oneil P8 DEVELOPMENT execution host
- Audit three pre-backtest invalid bars (read-only)
- 54 broad-market provider stack audit
- 54 broad-market Tiingo coverage audit
- Pre-backtest gate (R2 read-only)
- EMA state smoke
- standalone QQQ benchmark bootstrap
- standalone SPY benchmark bootstrap

Reusable operational/maintenance workflows were retained/restored.
