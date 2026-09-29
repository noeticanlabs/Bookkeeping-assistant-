# Contributing to Bookkeeper Assistant

Thank you for helping improve Bookkeeper Assistant.

This project treats governance, evidence, verification, authority, and reliable execution as architectural boundaries rather than prompt conventions. Contributions must preserve those boundaries.

## Development setup

1. Fork and clone the repository.
2. Create a branch from `main`.
3. Install the project using `INSTALL.md`.
4. Run the complete test suite before and after your change.

Linux:

```bash
source .venv/bin/activate
pytest -q
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Pull requests

Keep pull requests focused. Explain:

- what problem is being solved;
- what behavior changes;
- which trust or authority boundaries are affected;
- what tests demonstrate the change;
- any migration, compatibility, or security implications.

New consequential behavior should include falsification tests, not only happy-path tests.

## Architectural invariants

Contributions must not silently weaken these rules:

1. AI output is untrusted proposal material, not system authority.
2. Verification does not grant authority.
3. AI-facing code must not receive execution credentials or unrestricted write connectors.
4. Consequential actions require the configured authority/approval path.
5. Approval is bound to the exact proposal being approved.
6. Evidence and provenance must remain traceable.
7. Ambiguous external outcomes must not be treated as confirmed failure and blindly retried.
8. Vendor/model adapters must not redefine core bookkeeping policy or authority.

If a proposal intentionally changes one of these invariants, open an issue describing the threat model and rationale before implementation.

## Tests and certification

Bug fixes should include a regression test whenever practical. Security- or authority-sensitive changes should include adversarial tests demonstrating that the prohibited path remains unavailable.

Do not mark a gate, feature, or security property as certified solely because an implementation exists. Certification requires the applicable tests and CI to pass.

## Security issues

Do not open a public issue for a vulnerability that could expose credentials, bypass authorization, corrupt financial records, or cause unintended external mutations. Follow `SECURITY.md` instead.

## Style

Prefer deterministic code for facts and rules that normal code can determine exactly. Use AI only where interpretation or proposal generation is actually needed.

Keep dependencies justified and narrowly scoped. Never commit API keys, OAuth tokens, passwords, private customer records, financial exports, production databases, or evidence-vault contents.

## License

By submitting a contribution, you agree that your contribution will be licensed under the repository's Apache License 2.0.