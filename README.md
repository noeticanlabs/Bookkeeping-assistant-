# BOOKKEEPER-AI v0.1

Governed AI-assisted bookkeeping for small businesses, initially specialized for plumbing contractors and job-cost accounting.

> **AI interprets and proposes. The accounting system records and proves.**

## Core pipeline

```text
Evidence
  -> Normalization
  -> AI / rules proposal
  -> Deterministic validation
  -> Exception or approval
  -> Authorized posting
  -> Reconciliation
  -> Authoritative books
```

The AI is not the ledger, database, source of truth, or authority.

## Authority model

- A0 Observe
- A1 Recommend
- A2 Prepare bookkeeping entry
- A3 Post an independently approved bookkeeping entry
- A4 External financial action

BOOKKEEPER-AI v0.1 is capped at **A3**. A4 actions such as paying vendors, moving money, submitting payroll, or filing taxes are prohibited by policy and code.

## Initial invariants

1. An unbalanced journal cannot validate.
2. A proposal without source evidence cannot validate.
3. Journal accounts must exist in the configured chart of accounts.
4. Journal totals must match the normalized source transaction amount.
5. Recommendation authority cannot authorize posting.
6. External financial actions are forbidden in v0.1.
7. AI output remains non-authoritative until validation and approval succeed.

## Current branch

Development begins on `bookkeeper-ai-v0.1` with domain models, deterministic validation, authority boundaries, and executable invariant tests.

## Direction

The next layers are durable evidence/provenance, duplicate detection, accounting periods, approval records, job-cost allocation, exception routing, reconciliation, and an adapter to an existing authoritative accounting platform such as QuickBooks rather than replacing the general ledger in the first release.
