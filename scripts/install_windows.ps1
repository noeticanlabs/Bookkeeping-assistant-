$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pythonArgs = @("-3.12")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
    $pythonArgs = @()
} else {
    Write-Error "Python 3.12+ is required. Install Python 3.12 or newer and rerun this script."
}

& $python @pythonArgs -c "import sys; assert sys.version_info >= (3,12), f'Python 3.12+ required; found {sys.version.split()[0]}'; print('Using Python', sys.version.split()[0])"

if (-not (Test-Path ".venv")) {
    & $python @pythonArgs -m venv .venv
}

$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host ""
Write-Host "Installation complete."
Write-Host "Start Bookkeeper Assistant with:"
Write-Host '  $env:BOOKKEEPER_SECRET="replace-with-a-long-random-secret"'
Write-Host "  .\.venv\Scripts\python.exe secure_web_app.py"
Write-Host ""
Write-Host "Optional document extraction:"
Write-Host '  $env:OPENAI_API_KEY="your-key"'
Write-Host '  $env:BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"  # optional'
