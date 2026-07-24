param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$serviceName = 'ShineHeMCP'
$hostProject = Join-Path $ProjectRoot 'service_host\ShineHeMCPServiceHost.csproj'
$hostDirectory = Join-Path $ProjectRoot 'service_host\runtime'
$hostExecutable = Join-Path $hostDirectory 'ShineHeMCPServiceHost.exe'

if (-not (Test-Path -LiteralPath $hostExecutable)) {
    & dotnet publish $hostProject -c Release -r win-x64 --self-contained false -o $hostDirectory
    if ($LASTEXITCODE -ne 0) { throw "Windows 服务宿主编译失败，退出码: $LASTEXITCODE" }
}

$binaryPath = '"{0}" --project-root "{1}"' -f $hostExecutable, $ProjectRoot
& sc.exe query $serviceName *> $null
if ($LASTEXITCODE -eq 0) {
    & sc.exe config $serviceName 'binPath=' $binaryPath 'start=' 'auto'
} else {
    & sc.exe create $serviceName 'binPath=' $binaryPath 'start=' 'auto' 'DisplayName=' 'ShineHe Knowledge MCP Server'
}
if ($LASTEXITCODE -ne 0) { throw "Windows 服务注册/更新失败，退出码: $LASTEXITCODE" }

& sc.exe description $serviceName '本地知识库 MCP Server (streamable-http 模式，端口 9000)'
& sc.exe failure $serviceName 'reset=' '86400' 'actions=' 'restart/5000/restart/10000/restart/30000'
