# Changelog

## 2026-04-15

### refactor(project)

- move `convert_broker_data.py` and `pdf_portfolio.py` from `attribution_analysis/scripts/` to `shared/` for cross-module reuse
- unify report output under root `output/` and update `.gitignore` to ignore the shared output directory
- update attribution and risk-control quickstart scripts to call shared PDF parsers and write reports to the unified output path
- adjust attribution config and risk report persistence paths to use the repository-level output directory
- refresh top-level and module READMEs to reflect the new shared script layout and output locations

## 2026-04-14

### fix(risk-control)

- prevent positions with missing quotes and zero cost from being silently valued at zero
- validate portfolio pricing inputs before generating the risk report
- compute circuit breaker triggers from window drawdown instead of raw period return
- derive anomaly actions from unique signal types to avoid pair-count amplification
- raise runtime errors for benchmark fetch failures so callers can degrade gracefully
- add regression tests for valuation fallback, circuit breaker logic, anomaly escalation, and benchmark failures
