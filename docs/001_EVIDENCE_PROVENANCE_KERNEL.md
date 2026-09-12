# 001 — Evidence + Provenance Kernel

## Objective

Establish a trustworthy source-evidence substrate before AI interpretation or ledger posting.

The governing invariant is:

> A financial claim must remain traceable to immutable source evidence.

## Objects

### EvidenceRecord

Each captured source record contains:

- unique evidence identity
- source kind
- source URI
- SHA-256 content digest
- byte length
- capture timestamp
- normalized metadata

Evidence records are immutable dataclasses. The registry is append-only: an existing evidence identity cannot be reused with altered content.

### ProvenanceLink

A provenance link records an explicit directed relation between system objects, for example:

`evidence -> supports -> transaction`

`transaction -> interpreted_as -> proposal`

Later stages will extend the same graph through validation, approval, posting, reconciliation, reversal, and correction.

## Duplicate detection

Exact-content duplicate detection is digest-based. Two independently captured documents with the same SHA-256 digest are treated as duplicate evidence candidates, while preserving their separate source identities.

This is intentionally narrower than semantic duplicate detection. Near-duplicate invoices, edited documents, repeated bank transactions, and OCR-equivalent documents require later mechanisms.

## Certified invariants in 001

1. Equal byte content produces equal content digests.
2. Exact duplicate evidence is discoverable by digest.
3. Different normal test content produces different digests.
4. An existing evidence identity cannot be silently rebound to altered content.
5. Provenance relations are explicit and queryable across evidence, transaction, and proposal objects.

## Not yet certified

001 does not yet establish durable database persistence, cryptographic signatures, append-only database enforcement, source-system authentication, semantic duplicate detection, OCR integrity, retention policy, chain-of-custody signatures, or ledger posting provenance.

Those are subsequent obligations, not implied capabilities.
