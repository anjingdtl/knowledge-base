[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),

    [Parameter(Mandatory = $false)]
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed ($LASTEXITCODE): $PythonExe $($Arguments -join ' ')"
    }
}

function Wait-TcpPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 60,
        [string]$StdoutLog,
        [string]$StderrLog
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $client.Connect("127.0.0.1", $Port)
            return
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
        finally {
            $client.Dispose()
        }
    }
    # Surface MCP process logs so CI failures show why the server did not start.
    foreach ($pair in @(@("stderr", $StderrLog), @("stdout", $StdoutLog))) {
        $label = $pair[0]
        $path = $pair[1]
        if ($path -and (Test-Path -LiteralPath $path)) {
            Write-Host "----- MCP $label.log -----"
            Get-Content -LiteralPath $path -Encoding utf8 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-Host $_ }
            Write-Host "---------------------------"
        }
    }
    throw "MCP server did not listen on port $Port within $TimeoutSeconds seconds."
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("shinehe-windows-smoke-" + [guid]::NewGuid().ToString("N"))
$mcpProcess = $null
$originalHome = $env:SHINEHE_HOME
$originalPythonIoEncoding = $env:PYTHONIOENCODING

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    $env:SHINEHE_HOME = $tempRoot
    # GitHub Windows runners default to cp1252; the CLI help contains Chinese.
    $env:PYTHONIOENCODING = "utf-8"
    $fixture = Join-Path $tempRoot "fixture.txt"
    Set-Content -LiteralPath $fixture -Value "Windows smoke fixture: raw retrieval must remain available." -Encoding utf8

    Push-Location $repo
    try {
        Invoke-Python -m src.cli --help
        Invoke-Python -m src.cli init --local --force --path $tempRoot
        Invoke-Python -m src.cli index $fixture --dry-run

        # Keep the process and all state under the Windows temp workspace.
        $port = Get-Random -Minimum 19000 -Maximum 19999
        $stdout = Join-Path $tempRoot "mcp.stdout.log"
        $stderr = Join-Path $tempRoot "mcp.stderr.log"
        $mcpProcess = Start-Process -FilePath $PythonExe `
            -ArgumentList @("-m", "src.mcp_cli", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", "$port") `
            -WorkingDirectory $repo -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
        Wait-TcpPort -Port $port -StdoutLog $stdout -StderrLog $stderr

        # Performs initialize and real calls to capabilities/search/ask/read/ping.
        Invoke-Python scripts/check_mcp.py --host 127.0.0.1 --port $port --smoke-reads
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($mcpProcess -and -not $mcpProcess.HasExited) {
        Stop-Process -Id $mcpProcess.Id -Force
        $mcpProcess.WaitForExit()
    }
    $env:SHINEHE_HOME = $originalHome
    $env:PYTHONIOENCODING = $originalPythonIoEncoding
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
