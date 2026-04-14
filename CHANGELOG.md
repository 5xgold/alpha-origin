# Changelog

## 2026-04-14

### fix(risk-control)

- prevent positions with missing quotes and zero cost from being silently valued at zero
- validate portfolio pricing inputs before generating the risk report
- compute circuit breaker triggers from window drawdown instead of raw period return
- derive anomaly actions from unique signal types to avoid pair-count amplification
- raise runtime errors for benchmark fetch failures so callers can degrade gracefully
- add regression tests for valuation fallback, circuit breaker logic, anomaly escalation, and benchmark failures
