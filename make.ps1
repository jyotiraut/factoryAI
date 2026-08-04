<#
.SYNOPSIS
    Windows task runner for FactoryAI — mirrors the Makefile targets.

.DESCRIPTION
    GNU make is not part of a standard Windows toolchain, so this script provides the same
    task interface for local development. The Makefile remains the source of truth for CI
    and Docker; any target added there should be added here too.

.EXAMPLE
    .\make.ps1 install
    .\make.ps1 quality
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
$PythonVersion = '3.11'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

$targets = [ordered]@{
    'venv'             = 'Create the Python 3.11 virtualenv (ADR-0007)'
    'install'          = 'Install the project with dev dependencies and pre-commit hooks'
    'install-all'      = 'Install every optional dependency group (slow - pulls torch)'
    'lint'             = 'Ruff lint'
    'format'           = 'Format with Black and fix import order'
    'format-check'     = 'Verify formatting without writing'
    'typecheck'        = 'Mypy strict'
    'check-layers'     = 'Enforce Clean Architecture boundaries (ADR-0001)'
    'test'             = 'Run unit tests with coverage'
    'test-integration' = 'Run integration tests (requires Docker)'
    'test-e2e'         = 'Run end-to-end tests against the compose stack'
    'quality'          = 'Everything CI runs'
    'up'               = 'Start the local platform'
    'down'             = 'Stop the local platform, keep volumes'
    'reset'            = 'Stop and DESTROY local volumes'
    'logs'             = 'Tail all service logs'
    'migrate'          = 'Apply database migrations'
    'seed'             = 'Download, validate and ingest the MVTec bottle dataset'
    'train'            = 'Run the training pipeline with the default config'
    'clean'            = 'Remove caches and build artifacts'
}

$compose = 'deploy/compose/docker-compose.yml'

switch ($Target) {
    'help' {
        Write-Host 'FactoryAI task runner' -ForegroundColor Green
        Write-Host ''
        foreach ($t in $targets.Keys) {
            '{0,-20} {1}' -f $t, $targets[$t] | Write-Host
        }
    }

    'venv'         { Invoke-Step 'uv venv' { uv venv --python $PythonVersion } }

    'install' {
        Invoke-Step 'install dev dependencies' { uv pip install -e ".[dev]" }
        Invoke-Step 'install pre-commit hooks' { uv run pre-commit install }
    }

    'install-all' {
        Invoke-Step 'install all dependency groups' {
            uv pip install -e ".[dev,storage,imaging,ml,api,auth,worker,monitoring,test-infra]"
        }
    }

    'lint'         { Invoke-Step 'ruff check' { uv run ruff check src tests } }

    'format' {
        Invoke-Step 'ruff import order' { uv run ruff check --select I --fix src tests }
        Invoke-Step 'black' { uv run black src tests }
    }

    'format-check' { Invoke-Step 'black --check' { uv run black --check src tests } }
    'typecheck'    { Invoke-Step 'mypy' { uv run mypy } }
    'check-layers' { Invoke-Step 'import-linter' { uv run lint-imports } }
    'test'         { Invoke-Step 'pytest (unit)' { uv run pytest -m unit } }

    'test-integration' { Invoke-Step 'pytest (integration)' { uv run pytest -m integration } }
    'test-e2e'         { Invoke-Step 'pytest (e2e)' { uv run pytest -m e2e } }

    'quality' {
        foreach ($t in 'lint', 'format-check', 'typecheck', 'check-layers', 'test') {
            & $PSCommandPath $t
        }
        Write-Host 'All quality gates passed.' -ForegroundColor Green
    }

    'up'      { Invoke-Step 'compose up' { docker compose -f $compose up -d --build } }
    'down'    { Invoke-Step 'compose down' { docker compose -f $compose down } }
    'reset'   { Invoke-Step 'compose down -v' { docker compose -f $compose down -v } }
    'logs'    { docker compose -f $compose logs -f }
    'migrate' { Invoke-Step 'alembic upgrade head' { uv run alembic upgrade head } }
    'seed'    { Invoke-Step 'seed dataset' { uv run python scripts/seed_dataset.py --category bottle } }
    'train'   { Invoke-Step 'train' { uv run factoryai train --config configs/bottle/patchcore.yaml } }

    'clean' {
        foreach ($p in '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', '.coverage',
                       'coverage.xml', 'dist', 'build') {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
        Get-ChildItem -Recurse -Directory -Filter __pycache__ |
            Remove-Item -Recurse -Force
        Write-Host 'Cleaned.' -ForegroundColor Green
    }

    default {
        Write-Error "Unknown target '$Target'. Run '.\make.ps1 help' for the list."
        exit 1
    }
}
