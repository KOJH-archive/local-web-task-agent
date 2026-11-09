$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv, then run this script again."
}
if (-not (Get-Command ego-browser -ErrorAction SilentlyContinue)) {
    throw "ego-browser is required. Install ego lite and the official ego-browser CLI first."
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is required. Install and start Ollama first."
}

uv sync --extra dev
Copy-Item config.example.json config.json -ErrorAction SilentlyContinue
Write-Host "Installed. Start with .\run.ps1"

