# Bookkeeper Assistant

**Governed AI-assisted bookkeeping where AI can interpret and propose without possessing business authority.**

Bookkeeper Assistant is an open-source bookkeeping workflow and evidence-control layer for field-service and service businesses.

```text
External Systems / Evidence
        ↓
Interpretation / Proposals
        ↓
Deterministic Verification
        ↓
Policy + Action Firewall
        ↓
Human Authority / Exact Approval
        ↓
Reliable Execution
        ↓
Accounting / Operational Systems
        ↓
Audit + Provenance
```

The operating rule is simple:

> **If normal code can determine something exactly, do not ask AI.**

AI is used for interpretation and proposals. Authenticated humans authorize consequential actions. Deterministic bookkeeping code records and checks financial state, while provenance and audit history preserve the evidence chain.

A second invariant is equally important:

> **Verification is not authority.**

A deterministic PASS proves that a defined rule currently holds. It does not authorize posting, payment, scheduling, period close, or another consequential action.

## Governed-AI boundary

The current architecture includes the BK-AI-001 through BK-AI-009 hardening program:

- stable regression baseline;
- canonical, hash-bound `ActionProposal` objects;
- deterministic AI Action Firewall;
- AI credential, connector, capability, and authority isolation;
- provider-neutral OpenAI and Anthropic proposer adapters;
- governed scheduling proposals;
- exact proposal-bound human approval;
- durable/idempotent execution with explicit `UNCERTAIN` outcomes;
- full-system adversarial certification tests.

The intended security property is:

```text
AI intent != system authority
```

A model may propose an action. It does not gain authority merely by claiming approval, requesting execution, changing providers, or producing convincing text.

### Threat-model scope

These controls establish application-level boundaries. They are **not** a claim that arbitrary operating-system, host, administrator, dependency, or physical compromise cannot reach secrets or mutate the system. See `SECURITY.md` for the reporting policy and scope.

## Current application

The secure application entrypoint is:

```text
secure_web_app.py
```

The runtime includes:

- SQLite authoritative persistence;
- authenticated users and hashed passwords;
- CSRF and same-origin redirect protection;
- configurable roles and permissions;
- company-specific terminology and policies;
- guided company onboarding;
- configurable approval workflows;
- separation of duties and multi-person approval;
- work-order / job-cost / invoice / payment / bank workflow;
- accounts receivable and payable views;
- source-document provenance and SHA-256 duplicate detection;
- append-only application audit history;
- governed cost corrections;
- capability-aware multi-provider connectors;
- encrypted managed connector credentials and OAuth support;
- durable synchronization history and outbound outbox controls;
- scheduled safe pull synchronization with durable exponential backoff;
- economic record relationships and candidate-link review;
- unified bookkeeping exception queue;
- completed-job invoice readiness;
- deterministic verifier registry with PASS / FAIL / UNKNOWN evidence;
- configuration/readiness reporting.

## Deterministic verification

Open `/verifications` after starting the application.

The verifier set includes checks for invoice balances, processor settlements, work-order links, vendor-bill treatment, and deterministic job-margin calculations.

Results use three states:

```text
PASS     rule is provably satisfied by current evidence
FAIL     current records contradict the deterministic rule
UNKNOWN  evidence is insufficient to prove or disprove the rule
```

Only actual verifier failures are promoted into the exception queue as contradictions. `UNKNOWN` remains uncertainty for review rather than being mislabeled as an accounting failure.

## Installation

Detailed Windows and Linux installation instructions, prerequisites, dependencies, environment variables, data locations, and verification steps are in `INSTALL.md`.

### Linux quick install

```bash
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
cd Bookkeeping-assistant-
chmod +x scripts/install_linux.sh
./scripts/install_linux.sh

source .venv/bin/activate
export BOOKKEEPER_SECRET="replace-with-a-long-random-secret"
python secure_web_app.py
```

### Windows quick install

```powershell
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
Set-Location Bookkeeping-assistant-
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1

$env:BOOKKEEPER_SECRET="replace-with-a-long-random-secret"
.\.venv\Scripts\python.exe secure_web_app.py
```

Open `http://127.0.0.1:5000` locally.

## Dependencies

Baseline:

- Python 3.12+
- Flask 3.1+
- SQLite through Python's standard library

See `requirements.txt` for the pinned dependency ranges used by the project.

Git is recommended for cloning and updating the application.

## First run

1. Start `secure_web_app.py`.
2. Create the first Administrator account.
3. Complete the Company Setup Wizard.
4. Open `/readiness`.
5. Resolve blocking items and connect any external systems required by the company.
6. Use `/exceptions`, `/verifications`, `/relationships`, and `/sync-status` as the primary control views.

The readiness page distinguishes a system merely selected in configuration from an adapter that is actually loaded and connected.

## Optional AI providers

AI providers participate as bounded proposers. Provider choice does not grant additional bookkeeping or operational authority. Never place execution credentials in AI-provider configuration.

Supported provider adapters include OpenAI and Anthropic where configured. Document extraction and proposal generation remain non-authoritative until they cross the applicable deterministic governance and approval boundaries.

## Integration model

Connectors advertise explicit capabilities rather than broad vendor authority. A work-order source can contribute jobs without claiming to own cost or invoice state, while an accounting or payment provider can contribute only the financial facts its adapter supports.

Vendor-specific adapters remain transport/translation layers. Bookkeeping policy, verification, and authority stay in the core.

## Data and evidence

SQLite is authoritative for application/configuration/audit state. Uploaded source documents remain in a separate local evidence vault and are linked to financial records through provenance metadata.

Back up the SQLite database and evidence directory together to preserve the complete financial evidence chain. See `BACKUP_RESTORE.md`.

## Run tests

Linux:

```bash
source .venv/bin/activate
pytest -q
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions runs the regression suite on pushes to `main` and on pull requests targeting `main`, with Linux and Windows jobs.

## Contributing

Contributions are welcome. Start with `CONTRIBUTING.md` and preserve the project's core separation between evidence, verification, authority, approval, and execution.

For security vulnerabilities, follow `SECURITY.md` rather than opening a public exploit report.

Community participation is governed by `CODE_OF_CONDUCT.md`.

## License

Licensed under the **Apache License 2.0**. See `LICENSE`.

Copyright 2026 Noetican Labs.
