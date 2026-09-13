# Bookkeeper Assistant

A configurable, governed bookkeeping workflow engine for field-service and service businesses.

## Product boundary

`Work Order -> Cost -> Invoice -> Payment -> Bank -> Books`

The design rule is simple:

**If normal code can determine something exactly, do not ask AI.**

AI is used for interpretation and proposals. Authenticated users authorize consequential actions. Deterministic bookkeeping code records financial state, while provenance and audit history preserve the evidence chain.

## Current application

The secure application entrypoint is:

```text
secure_web_app.py
```

The current runtime includes:

- SQLite authoritative persistence
- authenticated users and hashed passwords
- configurable roles and permissions
- company-specific terminology and policies
- guided company onboarding
- configurable approval workflows
- separation of duties and multi-person approval
- work-order / job-cost / invoice / payment / bank workflow
- accounts receivable and payable views
- source-document provenance and SHA-256 duplicate detection
- append-only decision audit history
- governed cost corrections
- optional field-service, accounting, document, and event connector hooks
- configuration/readiness reporting

## Installation

Detailed Windows and Linux installation instructions, prerequisites, dependencies, environment variables, data locations, and verification steps are in:

**`INSTALL.md`**

### Linux quick install

```bash
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
cd Bookkeeping-assistant-
git checkout kiss-fresh
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
git checkout kiss-fresh
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
- `pytest>=9,<10` — regression testing

Git is recommended for cloning and updating the application.

## First run

1. Start `secure_web_app.py`.
2. Create the first Administrator account.
3. Complete the Company Setup Wizard.
4. Open `/readiness`.
5. Resolve blocking items and connect any external systems required by the company.

The readiness page distinguishes a system merely selected in configuration from an adapter that is actually loaded and connected.

## Optional document extraction

The application automatically enables the OpenAI document connector when `OPENAI_API_KEY` is present.

Linux:

```bash
export OPENAI_API_KEY="your-key"
export BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"   # optional
python secure_web_app.py
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your-key"
$env:BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"   # optional
.\.venv\Scripts\python.exe secure_web_app.py
```

Supported uploads currently include PDF, PNG, JPG/JPEG, and WEBP. The document model proposes structured fields; it does not directly mutate bookkeeping state.

## Integration hooks

`connectors.py` defines vendor-neutral contracts for:

- field-service systems: pull work orders and issue invoices
- accounting systems: push invoices and pull payments/deposits
- document extraction: convert receipts/bills into structured proposals
- event sinks / automation: observe workflow events without owning bookkeeping state

Vendor-specific adapters should remain transport/translation layers. Bookkeeping policy stays in the core.

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

CI runs the complete regression suite on every push to `kiss-fresh`.
