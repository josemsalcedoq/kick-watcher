# kick-watcher installer for Windows (PowerShell 5.1+). ASCII only on purpose.
#
#   .\install.ps1                 install Python deps in .venv
#   .\install.ps1 -Login          ...and open the browser to log in to Kick (saves state.json)
#   .\install.ps1 -Autostart      ...and run the watcher on this PC at every logon
#   .\install.ps1 -Uninstall      remove the logon task and the .venv
#
# If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\install.ps1
param(
    [switch]$Login,
    [switch]$Autostart,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Here '.venv'
$TaskName = 'kick-watcher'
$RepoRaw = 'https://raw.githubusercontent.com/josemsalcedoq/kick-watcher/main'

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

if ($Uninstall) {
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop; Say 'Tarea de inicio eliminada' } catch { Warn 'No habia tarea de inicio' }
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv; Say '.venv eliminado' }
    Write-Host 'La sesion (state.json, login-profile) no se toco; borrala a mano si quieres.'
    exit 0
}

# ------------------------------------------------------------ codigo
foreach ($f in 'watcher.py', 'login.py') {
    if (-not (Test-Path (Join-Path $Here $f))) {
        Say "Descargando $f"
        Invoke-WebRequest -UseBasicParsing -Uri "$RepoRaw/$f" -OutFile (Join-Path $Here $f)
    }
}

# ------------------------------------------------------------ Python
function Find-Python {
    foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
        $exe = $cand[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @()
        if ($cand.Count -gt 1) { $rest = $cand[1..($cand.Count - 1)] }
        try {
            $ver = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -and ([version]$ver -ge [version]'3.9')) {
                return ,(@($exe) + $rest)
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say 'Instalando Python 3.12 con winget'
        winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $py = Find-Python
    }
    if (-not $py) { throw 'No encontre Python 3.9+. Instalalo desde https://www.python.org/downloads/ y vuelve a ejecutar.' }
}
$pyExe = $py[0]
$pyArgs = @()
if ($py.Count -gt 1) { $pyArgs = $py[1..($py.Count - 1)] }

# ------------------------------------------------------------ navegador
$roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
$chrome = $roots | ForEach-Object { Join-Path $_ 'Google\Chrome\Application\chrome.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
$edge = $roots | ForEach-Object { Join-Path $_ 'Microsoft\Edge\Application\msedge.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) { Say "Navegador: Chrome ($chrome)" }
elseif ($edge) { Say "Navegador: Edge ($edge)" }
elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Say 'Instalando Google Chrome con winget'
    winget install --id Google.Chrome -e --silent --accept-package-agreements --accept-source-agreements
}
else { throw 'Hace falta Google Chrome o Microsoft Edge.' }

# ------------------------------------------------------------ entorno
$venvPy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say 'Creando el entorno de Python (.venv)'
    & $pyExe @pyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual' }
}
Say 'Instalando Playwright'
& $venvPy -m pip -q install --disable-pip-version-check --upgrade playwright
if ($LASTEXITCODE -ne 0) { throw 'Fallo la instalacion de Playwright' }

# ------------------------------------------------------------ sesion
if ($Login) {
    Say 'Abriendo el navegador: inicia sesion en Kick en la ventana que aparece'
    & $venvPy (Join-Path $Here 'login.py')
    if ($LASTEXITCODE -ne 0) { throw 'No se guardo la sesion' }
}
$hasState = Test-Path (Join-Path $Here 'state.json')

# ------------------------------------------------------------ inicio automatico
if ($Autostart) {
    if (-not $hasState) { throw 'Primero inicia sesion: .\install.ps1 -Login' }
    $pyw = Join-Path $Venv 'Scripts\pythonw.exe'
    $action = New-ScheduledTaskAction -Execute $pyw -Argument ('"{0}" run' -f (Join-Path $Here 'watcher.py')) -WorkingDirectory $Here
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Say "Tarea '$TaskName' creada: el watcher arranca al iniciar sesion (y ya esta corriendo)"
    Write-Host "    Estado: $env:USERPROFILE\.kick-watcher\status.json"
}

Write-Host ''
Say 'Listo.'
if (-not $hasState) {
    Write-Host '  1. Inicia sesion en Kick:        .\install.ps1 -Login'
} else {
    Write-Host '  Sesion guardada en state.json (tratala como una contrasena).'
}
Write-Host '  Para verlo en ESTE PC:            .\install.ps1 -Autostart'
Write-Host '  Para un servidor Linux:           scp state.json servidor:/tmp/state.json'
Write-Host '                                    y alli: curl -fsSL ' -NoNewline
Write-Host "$RepoRaw/install.sh | sudo bash"
