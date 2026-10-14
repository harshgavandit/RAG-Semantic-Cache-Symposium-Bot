param(
    [ValidateSet('backend', 'frontend', 'ingest', 'test', 'verify')]
    [string]$Action = 'backend',
    [int]$Port = 0
)
$ErrorActionPreference = 'Stop'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create a Python 3.12 virtual environment and install requirements-dev.txt first. See README.md.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    switch ($Action) {
        'backend' {
            if ($Port -eq 0) { $Port = 8000 }
            & $projectPython -m uvicorn phase2_retrieval.backend:app --host 127.0.0.1 --port $Port --no-proxy-headers
        }
        'frontend' {
            if ($Port -eq 0) { $Port = 8502 }
            & $projectPython -m streamlit run phase2_retrieval/frontend.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
        }
        'ingest' { & $projectPython -m phase1_ingestion.ingest }
        'test' { & $projectPython -m pytest -q }
        'verify' { & $projectPython scripts/verify_live.py }
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
