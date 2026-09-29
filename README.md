# Bookkeeper Assistant

A configurable, governed bookkeeping workflow and evidence-control layer for field-service and service businesses.

## Product boundary

`External Systems / Evidence -> Relationships -> Verification -> Authority -> Accounting System`

The operating rule is simple:

**If normal code can determine something exactly, do not ask AI.**

AI is used for interpretation and proposals. Authenticated users authorize consequential actions. Deterministic bookkeeping code records and checks financial state, while provenance and audit history preserve the evidence chain.

A second invariant is equally important:

**Verification is not authority.** A deterministic PASS proves that a defined bookkeeping rule currently holds; it does not authorize posting, payment, period close, or another consequential action.

## Current application

The secure application entrypoint is:

```text
secure_web_app.py
```

The current runtime includes:

- SQLite authoritative persistence
- authenticated users and hashed passwords
- CSRF and same-origin redirect protection
- configurable roles and permissions
- company-specific terminology and policies
- guided company onboarding
- configurable approval workflows
- separation of duties and multi-person approval
- work-order / job-cost / invoice / payment / bank workflow
- accounts receivable and payable views
- source-document provenance and SHA-256 duplicate detection
- append-only application audit history
- governed cost corrections
- capability-aware multi-provider connectors
- encrypted managed connector credentials and OAuth support
- durable synchronization history and outbound outbox controls
- scheduled safe pull synchronization with durable exponential backoff
- economic record relationships and candidate-link review
- unified bookkeeping exception queue
- completed-job invoice readiness
- deterministic verifier registry with PASS / FAIL / UNKNOWN evidence
- configuration/readiness reporting

## Deterministic verification

Open:

```text
/verifications
```

The first verifier set includes:

- `V-INVOICE-BALANCE` — linked payments must agree with invoice paid/receivable state
- `V-PROCESSOR-SETTLEMENT` — gross payment minus processor fee must agree with the linked net deposit under the current settlement model
- `V-WORK-ORDER-LINK` — operational-to-financial work-order references must resolve
- `V-VENDOR-BILL-TREATMENT` — vendor-bill treatment must resolve to valid job-cost semantics
- `V-JOB-MARGIN-CALC` — job revenue minus current job cost is calculated deterministically, without claiming the resulting margin is acceptable

Results use three states:

```text
PASS     rule is provably satisfied by current evidence
FAIL     current records contradict the deterministic rule
UNKNOWN  evidence is insufficient to prove or disprove the rule
```

Only actual verifier failures are promoted into the exception queue as contradictions. `UNKNOWN` remains uncertainty for review rather than being mislabeled as an accounting failure.

## Installation

Detailed Windows and Linux installation instructions, prerequisites, dependencies, environment variables, data locations, and verification steps are in:

**`INSTALL.md`**

### Linux quick install

```bash
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
cd Bookkeeping-assistant-
git checkout v0.1-hardening
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
git checkout v0.1-hardening
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1

$env:BOOKKEEPER_SECRET="replace-with-a-long-random-secret"
.\.venv\Scripts\python.exe secure_web_app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Dependencies

Baseline:

- Python 3.12+
- Flask 3.1+
- SQLite through Python's standard library

Installed by `requirements.txt`:

- `Flask>=3.1,<4` — application runtime
- `openai>=2,<3` — optional document-extraction connector
- `cryptography>=46,<47` — encrypted managed connector credentials
- `pytest>=9,<10` — regression testing

Git is recommended for cloning and updating the application.

## First run

1. Start `secure_web_app.py`.
2. Create the first Administrator account.
3. Complete the Company Setup Wizard.
4. Open `/readiness`.
5. Resolve blocking items and connect any external systems required by the company.
6. Use `/exceptions`, `/verifications`, `/relationships`, and `/sync-status` as the primary control views.

The readiness page distinguishes a system merely selected in configuration from an adapter that is actually loaded and connected.

## Optional document extraction

The application automatically enables the OpenAI document connector when `OPENAI_API_KEY` is present.

Linux:

```bash
export OPENAI_API_KEY="your-key"
export BOOKKEEPER_DOCUMENT_MODEL="your-supported-model"   # optional
python secure_web_app.py
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your-key"
$env:BOOKKEEPER_DOCUMENT_MODEL="your-supported-model"   # optional
.\.venv\Scripts\python.exe secure_web_app.py
```

Supported uploads currently include PDF, PNG, JPG/JPEG, and WEBP. The document model proposes structured fields; it does not directly mutate bookkeeping state.

## Integration model

Connectors advertise explicit capabilities rather than broad vendor authority. A work-order source can contribute jobs without claiming to own cost or invoice state, while an accounting or payment provider can contribute only the financial facts its adapter supports.

Vendor-specific adapters remain transport/translation layers. Bookkeeping policy, verification, and authority stay in the core.

## Data and evidence

SQLite is authoritative for application/configuration/audit state. Uploaded source documents remain in a separate local evidence vault and are linked to financial records through provenance metadata.

Back up the SQLite database and evidence directory together to preserve the complete financial evidence chain.

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

CI runs the regression suite on pushes to `kiss-fresh` and `v0.1-hardening`.
