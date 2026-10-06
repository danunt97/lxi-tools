# Komplett-Setup fuer das Hantek DSO5000P Tool unter Windows 10/11.
#
# Start in PowerShell:
#   irm https://raw.githubusercontent.com/danunt97/lxi-tools/claude/serene-archimedes-p3dv23/contrib/hantek-dso5000p/setup-windows.ps1 | iex
#
# Macht: Python installieren (falls noetig), Tool nach %USERPROFILE%\DSO5102P,
# Python-Pakete, Desktop-Verknuepfung, Zadig fuer den WinUSB-Treiber, Verbindungstest.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # macht Invoke-WebRequest viel schneller

$Branch = 'claude/serene-archimedes-p3dv23'
$ZipUrl = "https://github.com/danunt97/lxi-tools/archive/refs/heads/$Branch.zip"
$ZadigUrl = 'https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe'
$Dst = Join-Path $env:USERPROFILE 'DSO5102P'
# Nicht $env:TEMP: bei Benutzernamen mit Leerzeichen ist das ein 8.3-Kurzpfad
# (C:\Users\ABC~1\...), mit dem Remove-Item/Expand-Archive scheitern.
$Work = Join-Path $env:LOCALAPPDATA 'dso5102p-setup'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Find-Python {
    $ErrorActionPreference = 'Continue'
    # echter Python-Interpreter, nicht der Microsoft-Store-Platzhalter
    $candidates = @()
    foreach ($cmd in 'py', 'python') {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notlike '*WindowsApps*') { $candidates += $c.Source }
    }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                 "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | ForEach-Object FullName
    foreach ($p in $candidates) {
        try {
            $exe = & $p -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch {}
    }
    return $null
}

Write-Host '=== Hantek DSO5102P Tool - Setup ===' -ForegroundColor White

# 1. Python ---------------------------------------------------------------
Step 'Python pruefen'
$Py = Find-Python
if (-not $Py) {
    Warn 'Python nicht gefunden, wird installiert (dauert 1-2 Minuten) ...'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        winget install --id Python.Python.3.12 -e --scope user --silent `
            --accept-package-agreements --accept-source-agreements `
            --override '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1'
    } else {
        New-Item -ItemType Directory $Work -Force | Out-Null
        $inst = Join-Path $Work 'python-setup.exe'
        Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $inst
        Start-Process $inst -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1' -Wait
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
    $Py = Find-Python
    if (-not $Py) { throw 'Python-Installation fehlgeschlagen. Bitte von python.org installieren und Setup erneut starten.' }
}
Ok "Python: $Py"

# 2. Tool herunterladen ----------------------------------------------------
Step "Tool herunterladen nach $Dst"
$tmp = $Work
if (Test-Path -LiteralPath $tmp) { [System.IO.Directory]::Delete($tmp, $true) }
New-Item -ItemType Directory $tmp | Out-Null
Invoke-WebRequest $ZipUrl -OutFile "$tmp\tool.zip"
Expand-Archive "$tmp\tool.zip" "$tmp\x"
$src = Get-ChildItem "$tmp\x" -Directory | Select-Object -First 1
New-Item -ItemType Directory $Dst -Force | Out-Null
Copy-Item "$($src.FullName)\contrib\hantek-dso5000p\*" $Dst -Recurse -Force
Set-Content -Path "$Dst\python-path.txt" -Value $Py -Encoding ASCII -NoNewline
Ok 'Dateien kopiert'

# 3. Python-Pakete ---------------------------------------------------------
Step 'Python-Pakete installieren (pyusb, libusb, matplotlib)'
$ErrorActionPreference = 'Continue'
& $Py -m pip install --disable-pip-version-check -q --upgrade pyusb libusb-package matplotlib
if ($LASTEXITCODE -ne 0) { throw 'pip install fehlgeschlagen (Internet?).' }
$ErrorActionPreference = 'Stop'
Ok 'Pakete installiert'

# 4. Verknuepfung ----------------------------------------------------------
Step 'Desktop-Verknuepfung anlegen'
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desktop\DSO5102P.lnk")
$lnk.TargetPath = "$Dst\start-windows.bat"
$lnk.WorkingDirectory = $Dst
$lnk.Save()
Ok "$desktop\DSO5102P.lnk"

# 5. Treiber + Test --------------------------------------------------------
function Test-Scope {
    # PowerShell 5.1 wuerde stderr eines Programms sonst als Abbruchfehler werten
    $ErrorActionPreference = 'Continue'
    Push-Location $Dst
    try { $out = & $Py dso5000p.py info 2>&1 | ForEach-Object { "$_" } | Out-String } finally { Pop-Location }
    return @{ ok = ($LASTEXITCODE -eq 0); text = $out }
}

Step 'Verbindung zum Scope testen (Scope einschalten und per USB anstecken!)'
$r = Test-Scope
if (-not $r.ok) {
    Write-Host $r.text
    Warn 'Das Scope braucht den WinUSB-Treiber. Zadig wird jetzt gestartet:'
    Write-Host @'

    In Zadig:
      1. Menue "Options" -> "List All Devices" anhaken
      2. Im Auswahlfeld das Geraet mit USB ID  049F 505A  waehlen
      3. Rechts vom gruenen Pfeil "WinUSB" einstellen
      4. "Replace Driver" (oder "Install Driver") klicken, warten bis "successfully"
      5. Zadig schliessen

    (Zurueck zur Hantek-Software: Geraete-Manager -> Geraet -> Treiber -> Vorheriger Treiber)

'@ -ForegroundColor White
    $zadig = Join-Path $Dst 'zadig.exe'
    if (-not (Test-Path $zadig)) { Invoke-WebRequest $ZadigUrl -OutFile $zadig }
    Start-Process $zadig -Verb RunAs -Wait
    Write-Host '    Scope einmal ab- und wieder anstecken, dann Enter druecken ...' -ForegroundColor White
    [void](Read-Host)
    $r = Test-Scope
}

Write-Host $r.text
if ($r.ok) {
    Write-Host "`n=== Fertig! Das Scope ist verbunden. ===" -ForegroundColor Green
    Write-Host 'Ab jetzt einfach "DSO5102P" auf dem Desktop doppelklicken.'
} else {
    Write-Host "`n=== Installation fertig, aber das Scope antwortet noch nicht. ===" -ForegroundColor Yellow
    Write-Host 'Bitte einen Screenshot von diesem Fenster an Claude schicken.'
}
