[CmdletBinding()]
param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTEST_DEBUG_TEMPROOT = Join-Path $repo ".codex-pytest-tmp"

try {
    Push-Location $repo
    & $PythonExe -m pytest `
        tests/test_knowledge_settings.py `
        tests/test_config_verified_hybrid_migration.py `
        tests/test_wiki_serving_validation_migrator.py `
        tests/test_maintenance_repo.py `
        tests/test_maintenance_worker.py `
        tests/test_maintenance_scheduler.py `
        tests/test_verified_answer.py `
        tests/test_verified_hybrid_release_eval.py `
        -q
    if ($LASTEXITCODE -ne 0) { throw "Verified Hybrid acceptance tests failed ($LASTEXITCODE)." }

    & $PythonExe evals/run_verified_hybrid_release_eval.py --strict --json
    if ($LASTEXITCODE -ne 0) { throw "Verified Hybrid release evaluation failed ($LASTEXITCODE)." }
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-Item Env:PYTEST_DEBUG_TEMPROOT -ErrorAction SilentlyContinue
}
