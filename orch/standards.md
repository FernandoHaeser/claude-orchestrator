# Engineering standards (stack-agnostic)

Injected into every agent. Follow the project's own conventions first; these fill the gaps.

## Code
- Clean code: small functions, one responsibility, intention-revealing names, no dead code, no speculative abstraction.
- Code identifiers in en-US. User-facing text follows product language.
- Explicit boundaries: separate domain logic from I/O (hexagonal / ports and adapters when it pays off).
- Fail loud at boundaries: validate input on the server side, typed errors, no silent catch.
- Config via environment, never hardcode secrets.

## Scalability
- State the expected load and the bottleneck. Prefer stateless services, idempotent operations, pagination, bounded queues, timeouts and retries with backoff.
- No N+1 queries. Index what you filter by. Name the trade-off when you pick a simpler option.

## Security (checklist)
- AuthZ on every resource by id (ownership check on the backend), no user enumeration, parametrized queries, output encoding, rate limit on auth and expensive actions, no secrets in logs.

## Tests
- Test behavior through public interfaces. Cover happy path, edge cases, failure paths. Deterministic, no network.

## Docs
- README: what, why, how to run, how to test. ADR for each significant decision (context, decision, consequences). Docs describe what is true now.

## Agent rules
- Stay inside your role and file ownership. Never edit files owned by another role.
- Do not git commit. The orchestrator does that.
- Final message = short report (<= 300 words): what you did, files touched, open risks, assumptions.
