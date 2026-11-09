$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

if (-not (Test-Path .venv\Scripts\python.exe)) {
    throw "Run .\install.ps1 first."
}
& .venv\Scripts\python.exe -m streamlit run streamlit_app.py
