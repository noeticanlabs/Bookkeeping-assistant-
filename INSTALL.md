# Bookkeeper Assistant Installation

Bookkeeper Assistant is a local-first Python web application. The secure application entrypoint is `secure_web_app.py` and the authoritative application database is SQLite.

## Supported baseline

- Python 3.12 or newer
- Windows 10/11 or a current Linux distribution
- 250 MB+ free disk space for the application and Python environment, plus space for uploaded evidence documents
- A modern web browser
- Network access only when using external connectors

Git is recommended for cloning/updating the repository but is not required if the source is obtained another way.

## Python dependencies

`requirements.txt` currently installs:

| Dependency | Purpose | Required? |
| --- | --- | --- |
| Flask 3.1+ | Web application/runtime | Yes |
| cryptography 46.x | Encrypted connector credentials | Yes |
| OpenAI Python SDK 2.x | Optional document extraction connector | Installed by default; only used when configured |
| pytest 9.x | Regression tests | Development/testing |

SQLite support is provided by Python's standard library.

## Linux

### Ubuntu / Debian prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Confirm Python 3.12 or newer:

```bash
python3 --version
```

### Fedora / RHEL-family prerequisites

```bash
sudo dnf install -y python3 python3-pip git
```

### Arch Linux prerequisites

```bash
sudo pacman -S python python-pip git
```

### Install the application

```bash
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
cd Bookkeeping-assistant-
git checkout v0.1-hardening
chmod +x scripts/install_linux.sh
./scripts/install_linux.sh
```

If the required Python executable has a different name:

```bash
PYTHON_BIN=python3.12 ./scripts/install_linux.sh
```

### Create persistent secrets on Linux

The application uses two separate secrets:

- `BOOKKEEPER_SECRET` signs web sessions.
- `BOOKKEEPER_CREDENTIAL_KEY` encrypts external-system credentials stored in SQLite.

Generate a Fernet credential key once:

```bash
source .venv/bin/activate
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store the resulting value in your protected environment/secret manager, then set:

```bash
export BOOKKEEPER_SECRET="replace-with-a-long-random-session-secret"
export BOOKKEEPER_CREDENTIAL_KEY="paste-the-generated-fernet-key-here"
```

**Do not casually regenerate `BOOKKEEPER_CREDENTIAL_KEY`.** Existing managed connector credentials are intentionally unreadable without the same key. Back up the key separately from the SQLite database.

### Start on Linux

```bash
source .venv/bin/activate
python secure_web_app.py
```

Open:

```text
http://127.0.0.1:5000
```

### Optional OpenAI document extraction on Linux

```bash
export OPENAI_API_KEY="your-key"
export BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"   # optional override
python secure_web_app.py
```

Without `OPENAI_API_KEY`, the bookkeeping application still runs.

## Windows 10 / 11

### Prerequisites

Install Python 3.12 or newer:

```powershell
winget install Python.Python.3.12
```

Git is recommended:

```powershell
winget install Git.Git
```

Restart PowerShell if the new commands are not immediately found.

### Install the application

```powershell
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
Set-Location Bookkeeping-assistant-
git checkout v0.1-hardening
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

### Create persistent secrets on Windows

Generate the credential-encryption key once:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy that value and set:

```powershell
$env:BOOKKEEPER_SECRET="replace-with-a-long-random-session-secret"
$env:BOOKKEEPER_CREDENTIAL_KEY="paste-the-generated-fernet-key-here"
```

Keep `BOOKKEEPER_CREDENTIAL_KEY` stable and backed up. Changing it prevents existing encrypted connector credentials from being decrypted.

### Start on Windows

```powershell
.\.venv\Scripts\python.exe secure_web_app.py
```

Open:

```text
http://127.0.0.1:5000
```

### Optional OpenAI document extraction on Windows

```powershell
$env:OPENAI_API_KEY="your-key"
$env:BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"
.\.venv\Scripts\python.exe secure_web_app.py
```

Environment variables set this way last for the current PowerShell session. Use a protected persistent environment/secret-management mechanism for real deployments.

## In-app Connection Manager

With `BOOKKEEPER_CREDENTIAL_KEY` configured, an Administrator can open:

```text
/settings/connections/manage
```

Supported managed providers currently include Xero, Stripe, QuickBooks Online, Jobber, Housecall Pro, ServiceTitan, and Yardi Maintenance. Provider credentials are encrypted before SQLite persistence and are never displayed back in the UI.

Xero supports the standard OAuth authorization flow from inside the application. Configure the exact redirect URI in the Xero developer application; for local Xero testing use `localhost` rather than `127.0.0.1` where required by the provider.

## Application data

By default the application creates its local data beside the configured data path. Important runtime data includes:

- SQLite bookkeeping/configuration/audit database
- encrypted managed-connection records
- uploaded source-document evidence vault
- authenticated users and password hashes
- workflow and approval policy
- provenance and audit history

Back up the SQLite database and evidence-document directory together, and separately protect/back up `BOOKKEEPER_CREDENTIAL_KEY`. A database backup without the source documents does not preserve the complete evidence chain, and a database containing encrypted connectors cannot restore those connectors without the encryption key.

You can set `BOOKKEEPER_DATA` before startup.

Linux example:

```bash
export BOOKKEEPER_DATA="$HOME/bookkeeper/bookkeeper-data.json"
```

Windows example:

```powershell
$env:BOOKKEEPER_DATA="$env:USERPROFILE\Bookkeeper\bookkeeper-data.json"
```

## First run

1. Start `secure_web_app.py`.
2. Open `http://127.0.0.1:5000`.
3. Create the first Administrator account.
4. Complete the Company Setup Wizard.
5. Open the Connection Manager and connect the systems the company actually uses.
6. Open `/readiness`.
7. Resolve blocking items before a controlled pilot.

## Verify the installation

### Linux

```bash
source .venv/bin/activate
pytest -q
```

### Windows

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Production note

The current application is a V0.1 local-first prototype. Flask's built-in development server is appropriate for local testing and evaluation, but internet-facing or multi-machine production deployment should use a production WSGI server, TLS/reverse proxy, managed secrets, operating-system access controls, backups, and deployment-specific hardening.
