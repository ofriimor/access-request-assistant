# Access Request Assistant — implemented architecture

```mermaid
flowchart TD
    A[CLI / JSON Request] --> B[Intake: Pydantic validation + request ID]
    B --> C[Understand: Gemini API extraction<br/>gemini-3.5-flash-lite]
    C --> D[Structured Understanding schema]
    D --> E[Retrieve: policy search / policy read / user lookup]
    E --> F[Deterministic Guard + ActionPlan]
    F -->|BLOCK| X[BLOCKED]
    F -->|NEEDS_INFO| Y[NEEDS_INFO]
    F -->|ALLOW| G[AWAITING_APPROVAL]
    G -->|Manager rejects| Z[REJECTED]
    G -->|Manager approves exact plan hash| H[APPROVED]
    H --> I[Execute: validated grant_access tool]
    I -->|Tool failure| J[FAILED_SAFE]
    J -->|Retry with valid approval| I
    I --> K[Read-back verification]
    K --> L[COMPLETED / VERIFICATION_FAILED]
    B -.-> M[(SQLite state)]
    F -.-> M
    H -.-> M
    I -.-> M
    K -.-> M
    B -.-> N[(JSONL audit)]
    C -.-> N
    E -.-> N
    F -.-> N
    H -.-> N
    I -.-> N
    K -.-> N
```

## Code map

| Module | Responsibility |
| --- | --- |
| `app/__main__.py` | CLI and interactive demo |
| `app/schemas.py` | Pydantic contracts and input validation |
| `app/llm.py` | Google Gemini API adapter (`gemini-3.5-flash-lite`), bounded HTTP retry, extraction schema parsing |
| `app/tools.py` | Local policy search, read, user lookup, write tool |
| `app/guard.py` | Deterministic policy evaluation, injection heuristics |
| `app/engine.py` | Orchestration, tool allowlist, approval gate, retries, verification |
| `app/state.py` | SQLite-backed state machine and action deduplication |
| `app/audit.py` | JSONL event trail |
| `app/report.py` | HTML As-Built from runtime evidence |
| `demo.py` | End-to-end scripted demonstration |

## Design vs. implementation

- The functional seven-stage flow is retained.
- Plan decision is deterministic rather than an unconstrained LLM decision.
- Approval is bound to the exact `plan_hash`.
- Knowledge retrieval uses local TF-IDF over five Markdown policies, not an embeddings service.
- The write tool updates a local JSON directory, not a real identity provider.
- The CLI manager approval is a simulation and is not authenticated.
- The application has no automatic revocation, no SSO, and no production transaction/locking guarantees.
- Gemini is the only runtime provider; there is no silent fallback. Tests use an isolated FakeLLM (`tests/fake_llm.py`) that the application never loads.
