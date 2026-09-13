# Bookkeeper Assistant Installation

Bookkeeper Assistant is a local-first Python web application. The secure application entrypoint is `secure_web_app.py` and the authoritative application database is SQLite.

## Supported baseline

- Python 3.12 or newer
- Windows 10/11 or a current Linux distribution
- 250 MB+ free disk space for the application and Python environment, plus space for uploaded evidence documents
- A modern web browser
- Network access only when using external connectors such as OpenAI

Git is recommended for cloning/updating the repository but is not required if the source is obtained another way.

## Python dependencies

`requirements.txt` currently installs:

| Dependency | Purpose | Required? |
| --- | --- | --- |
| Flask 3.1+ | Web application/runtime | Yes |
| OpenAI Python SDK 2.x | Optional document extraction connector | Installed by default; only used when configured |
| pytest 9.x | Regression tests | Development/testing |

SQLite support is provided by Python's standard library; no separate SQLite Python package is required.

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

If your distribution's default Python is older than 3.12, install a supported Python version using your distribution's supported method and set `PYTHON_BIN` when running the installer.

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
git checkout kiss-fresh
chmod +x scripts/install_linux.sh
./scripts/install_linux.sh
```

If the required Python executable has a different name:

```bash
PYTHON_BIN=python3.12 ./scripts/install_linux.sh
```

### Start on Linux

```bash
source .venv/bin/activate
export BOOKKEEPER_SECRET="replace-with-a-long-random-secret"
python secure_web_app.py
```

Open:

```text
http://127.0.0.1:5000
```

For a secret value, use a long random string rather than the development default.

### Optional OpenAI document extraction on Linux

```bash
export OPENAI_API_KEY="your-key"
export BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"   # optional override
python secure_web_app.py
```

Without `OPENAI_API_KEY`, the bookkeeping application still runs. The readiness page will show document extraction as unavailable or declared-but-not-connected depending on company configuration.

## Windows 10 / 11

### Prerequisites

Install Python 3.12 or newer from Python.org or with Windows Package Manager:

```powershell
winget install Python.Python.3.12
```

Git is recommended:

```powershell
winget install Git.Git
```

Restart PowerShell after installing Python or Git if the commands are not immediately found.

Confirm Python:

```powershell
py -3.12 --version
```

### Install the application

```powershell
git clone https://github.com/noeticanlabs/Bookkeeping-assistant-.git
Set-Location Bookkeeping-assistant-
git checkout kiss-fresh
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

The `-ExecutionPolicy Bypass` applies only to that PowerShell process and avoids requiring a permanent machine-wide execution-policy change.

### Start on Windows

```powershell
$env:BOOKKEEPER_SECRET="replace-with-a-long-random-secret"
.\.venv\Scripts\python.exe secure_web_app.py
```

Open:

```text
http://127.0.0.1:5000
```

### Optional OpenAI document extraction on Windows

```powershell
$env:OPENAI_API_KEY="your-key"
$env:BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"   # optional override
.\.venv\Scripts\python.exe secure_web_app.py
```

Environment variables set this way last for the current PowerShell session. Use Windows user/system environment-variable settings if persistent configuration is desired.

## Application data

By default the application creates its local data beside the configured data path. The runtime uses SQLite as the authoritative store and preserves uploaded source documents in the local evidence directory.

Important runtime data includes:

- SQLite bookkeeping/configuration/audit database
- uploaded source-document evidence vault
- authenticated users and password hashes
- workflow and approval policy
- provenance and audit history

Back up both the SQLite database and the evidence-document directory together. A database backup without the source documents does not preserve the complete evidence chain.

You can set `BOOKKEEPER_DATA` to choose the application data location before startup.

Linux example:

```bash
export BOOKKEEPER_DATA="$HOME/bookkeeper/bookkeeper-data.json"
```

Windows example:

```powershell
$env:BOOKKEEPER_DATA="$env:USERPROFILE\Bookkeeper\bookkeeper-data.json"
```

The compatibility path is converted to a sibling SQLite database by the current storage layer.

## First run

1. Start `secure_web_app.py`.
2. Open `http://127.0.0.1:5000`.
3. Create the first Administrator account.
4. Complete the Company Setup Wizard.
5. Open `/readiness`.
6. Resolve any blocking items and distinguish declared connectors from live connectors.

## Verify the installation

Run the regression suite:

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
