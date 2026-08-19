#Requires -Version 5.1
<#
    One-command install for google-workspace-mcp on Windows.

      cd ~
      git clone https://github.com/adelaidasofia/google-workspace-mcp.git
      powershell -ExecutionPolicy Bypass -File .\google-workspace-mcp\install.ps1

    The Windows half of install.sh: same steps, same checks, same messages.
    Builds an isolated venv next to this script, installs the dependencies,
    asks for the OAuth client, and registers the server with Claude Code.
    Nothing is written outside this directory and Claude Code's own config.
    Safe to re-run.

    Targets Windows PowerShell 5.1, which is what ships in the box, so no 7.x
    syntax: no ternaries, no ??, no &&/|| chains.

    ASCII ONLY, deliberately. A .ps1 saved as UTF-8 without a BOM is read by
    5.1 through the active code page, and cp1252 turns an em dash into a
    character it accepts as a string quote -- the file then fails to parse
    somewhere far from the actual line. Keep every character in this file
    inside ASCII and that whole class of bug cannot happen.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ServerName = 'google-workspace'
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir    = Join-Path $ScriptDir '.venv'
$VenvPy     = Join-Path $VenvDir 'Scripts\python.exe'

# --------------------------------------------------------------- output

$UseColor = $Host.UI.RawUI -and -not $env:NO_COLOR

function Write-Step($Text) {
    Write-Host ''
    if ($UseColor) { Write-Host '==> ' -ForegroundColor Green -NoNewline } else { Write-Host '==> ' -NoNewline }
    Write-Host $Text
}
function Write-Ok($Text) {
    if ($UseColor) { Write-Host '    + ' -ForegroundColor Green -NoNewline } else { Write-Host '    + ' -NoNewline }
    Write-Host $Text
}
function Write-Warn($Text) {
    if ($UseColor) { Write-Host '    ! ' -ForegroundColor Yellow -NoNewline } else { Write-Host '    ! ' -NoNewline }
    Write-Host $Text
}
function Die($Text) {
    Write-Host ''
    if ($UseColor) { Write-Host "X  $Text" -ForegroundColor Red } else { Write-Host "X  $Text" }
    Write-Host ''
    exit 1
}

function Read-Value($Prompt) {
    # Read-Host reads the console directly, not stdin, so this keeps working
    # when the script itself was piped in.
    $value = ''
    while ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host ''
        Write-Host "    $Prompt"
        $value = Read-Host '    >'
        if ($null -eq $value) { Die 'No input received. Run the script from a PowerShell window.' }
        # Trim spaces and the quotes people pick up copying out of a browser.
        $value = $value.Trim().Trim('"').Trim("'").Trim()
        if ([string]::IsNullOrWhiteSpace($value)) { Write-Warn 'That was empty. Try again.' }
    }
    return $value
}

function Confirm-Yes($Question) {
    Write-Host ''
    $reply = Read-Host "    $Question [y/N]"
    return ($reply -match '^[yY]')
}

Write-Host ''
Write-Host '  Google Workspace MCP'
Write-Host '  Gmail, Calendar, Drive, Docs and Sheets, connected to Claude Code.'

# ---------------------------------------------------------- 1. tooling

Write-Step 'Checking what you already have'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die @'
git is not installed.
   Install Git for Windows from https://git-scm.com/download/win , close this
   window, open a new one, then run this script again.
'@
}

# Pick an interpreter, rather than trusting the first `python` on PATH.
#
# Windows 10 and 11 ship stub python.exe / python3.exe "app execution aliases"
# in %LOCALAPPDATA%\Microsoft\WindowsApps that only open the Microsoft Store.
# They are real files on PATH, so every existence check passes; they just never
# run Python. That is why nothing here trusts a name -- each candidate has to
# print its own version before it counts, and the stubs print nothing.
#
# `py` (the Python launcher, installed with python.org Python) is tried first
# because it is the one name that reliably resolves to a real interpreter and
# knows about every version installed. GWS_PYTHON overrides the lot.

function Get-PyVersion($Exe, $PreArgs) {
    # Returns "3.12", or $null when this is not a working interpreter.
    #
    # SINGLE quotes inside the snippet, and no double quotes anywhere in it.
    # Windows PowerShell 5.1 strips embedded double quotes when it builds the
    # command line for a native executable, so the shell-obvious
    # 'print("%d.%d" % ...)' arrives at Python as print(%d.%d % ...) and dies
    # with a SyntaxError -- which this function reads as "not an interpreter",
    # and every Python on the machine gets rejected.
    $argv = @()
    if ($PreArgs) { $argv += $PreArgs }
    $argv += @('-c', 'import sys;print(str(sys.version_info[0]) + chr(46) + str(sys.version_info[1]))')
    try {
        $out = & $Exe @argv 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0) { return $null }
    $text = ($out | Out-String).Trim()
    if ($text -match '^\d+\.\d+$') { return $text }
    return $null
}

function Test-PyNewEnough($Version) {
    if (-not $Version) { return $false }
    $parts = $Version.Split('.')
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -gt 3) { return $true }
    return ($major -eq 3 -and $minor -ge 10)
}

$Py      = $null      # executable to invoke
$PyArgs  = @()        # arguments that must precede every call (py -3.12)
$PyVer   = $null
$PySeen  = @()

if ($env:GWS_PYTHON) {
    # An explicit override is handled on its own and its problems are fatal:
    # quietly searching past a GWS_PYTHON that turned out to be wrong would
    # install against a different interpreter than the one that was asked for.
    $cmd = Get-Command $env:GWS_PYTHON -ErrorAction SilentlyContinue
    if ($cmd) { $resolved = $cmd.Source } else { $resolved = $env:GWS_PYTHON }
    if (-not (Test-Path -LiteralPath $resolved)) {
        Die "GWS_PYTHON is set to `"$($env:GWS_PYTHON)`", but there is no such interpreter.`n   Unset it, or point it at a real python.exe."
    }
    $found = Get-PyVersion $resolved @()
    if (-not $found) {
        Die "GWS_PYTHON points at `"$resolved`", which did not run and report a version.`n   Point it at a real python.exe."
    }
    if (-not (Test-PyNewEnough $found)) {
        Die "GWS_PYTHON points at Python $found, but 3.10 or newer is needed."
    }
    $Py = $resolved
    $PyVer = $found
}

if (-not $Py) {
    # `py -3` asks the launcher for the newest installed 3.x, so the explicit
    # descending list after it is only a fallback for boxes with no launcher.
    $candidates = @(
        @{ Exe = 'py';         Pre = @('-3') },
        @{ Exe = 'py';         Pre = @('-3.14') },
        @{ Exe = 'py';         Pre = @('-3.13') },
        @{ Exe = 'py';         Pre = @('-3.12') },
        @{ Exe = 'py';         Pre = @('-3.11') },
        @{ Exe = 'py';         Pre = @('-3.10') },
        @{ Exe = 'python';     Pre = @() },
        @{ Exe = 'python3';    Pre = @() },
        @{ Exe = 'python3.14'; Pre = @() },
        @{ Exe = 'python3.13'; Pre = @() },
        @{ Exe = 'python3.12'; Pre = @() },
        @{ Exe = 'python3.11'; Pre = @() },
        @{ Exe = 'python3.10'; Pre = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # Skip the Store alias outright. Get-PyVersion would reject it anyway,
        # but launching it can pop the Store, which is alarming mid-install.
        if ($cmd.Source -and $cmd.Source -like '*\WindowsApps\*') { continue }
        $found = Get-PyVersion $cmd.Source $c.Pre
        if (-not $found) { continue }
        if ($PySeen -notcontains $found) { $PySeen += $found }
        if (Test-PyNewEnough $found) {
            $Py = $cmd.Source
            $PyArgs = $c.Pre
            $PyVer = $found
            break
        }
    }
}

if (-not $Py) {
    if ($PySeen.Count -eq 0) {
        Die @'
No working Python was found.
   Install it from https://www.python.org/downloads/windows/ and tick
   "Add python.exe to PATH" in the installer. Then close this window, open
   a new one, and run this script again.

   Note: the python.exe under Microsoft\WindowsApps is a Microsoft Store
   placeholder, not Python. It is skipped on purpose.
'@
    }
    Die @"
No Python 3.10 or newer was found, but 3.10 or newer is needed.
   Looked for: py -3, py -3.14 down to -3.10, python, python3, python3.14 down to python3.10.
   Versions found: $($PySeen -join ' ')
   Install a newer one from https://www.python.org/downloads/windows/ , then
   run this script again.
   Already have one somewhere unusual? Point at it directly:
     `$env:GWS_PYTHON = 'C:\full\path\to\python.exe'
     powershell -ExecutionPolicy Bypass -File .\install.ps1
"@
}
if ($PyArgs.Count -gt 0) {
    Write-Ok "python $PyVer  ($Py $($PyArgs -join ' '))"
} else {
    Write-Ok "python $PyVer  ($Py)"
}

# Claude Code, and specifically a form of it that can be called with arguments.
#
# npm installs three shims side by side: claude.ps1, claude.cmd and a bash
# `claude`. Get-Command prefers the .ps1, and calling that would break this
# install in a way nobody would trace back to here: PowerShell's parameter
# binder eats a bare `--` before a script ever sees it, so the separator that
# tells `claude mcp add` where the server command starts would silently vanish
# and the connector would be registered wrong. So take the .exe if there is
# one, else the .cmd/.bat, which forwards its raw command line intact.
$ClaudeExe = $null
$claudeCandidates = @(Get-Command claude -All -ErrorAction SilentlyContinue)
foreach ($ext in @('.exe', '.cmd', '.bat')) {
    foreach ($c in $claudeCandidates) {
        if ($c.Source -and $c.Source.ToLower().EndsWith($ext)) { $ClaudeExe = $c.Source; break }
    }
    if ($ClaudeExe) { break }
}
if (-not $ClaudeExe) {
    # Only a .ps1 (or a bash shim) on PATH: look for a usable sibling next to it.
    foreach ($c in $claudeCandidates) {
        if (-not $c.Source) { continue }
        $dir = Split-Path -Parent $c.Source
        foreach ($ext in @('.exe', '.cmd', '.bat')) {
            $sibling = Join-Path $dir "claude$ext"
            if (Test-Path -LiteralPath $sibling) { $ClaudeExe = $sibling; break }
        }
        if ($ClaudeExe) { break }
    }
}
if (-not $ClaudeExe) {
    Die @'
Claude Code is not installed, or its `claude` command is not on your PATH.
   Install Claude Code first, close this window, open a new one, then run
   this again.
'@
}
Write-Ok 'claude'

if (-not (Test-Path -LiteralPath (Join-Path $ScriptDir 'server.py'))) {
    Die @'
This script is not sitting next to server.py, so the clone looks incomplete.
   Delete the folder and clone it again.
'@
}

# --------------------------------------------------------------- 2. install

Write-Step 'Installing the connector (about a minute)'

# A venv left behind by a too-old interpreter fails much later, at
# `import server`, with nothing useful to read. Rebuild it instead. Only a
# definite answer counts: an interpreter that cannot report its version is
# left alone rather than deleted.
if (Test-Path -LiteralPath $VenvPy) {
    $venvVer = Get-PyVersion $VenvPy @()
    if ($venvVer -and -not (Test-PyNewEnough $venvVer)) {
        Write-Warn "the existing environment runs Python $venvVer - rebuilding it with $PyVer"
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $VenvPy)) {
    $venvArgs = @()
    if ($PyArgs.Count -gt 0) { $venvArgs += $PyArgs }
    $venvArgs += @('-m', 'venv', $VenvDir)
    & $Py @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPy)) {
        Die "Could not create the virtual environment in $VenvDir.`n   If that folder half-exists, delete it and run this script again."
    }
}
Write-Ok 'isolated environment ready'

& $VenvPy -m pip install --quiet --upgrade pip 2>&1 | Out-Null
& $VenvPy -m pip install --quiet -r (Join-Path $ScriptDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Die @'
Could not install the dependencies.
   Check your internet connection and run this script again.
'@
}
Write-Ok 'dependencies installed'

# Import the real module. A dependency that resolves at install time but not at
# import time (a missing transitive, a version clash) otherwise shows up much
# later as a connector that is simply absent from /mcp, with nothing to read.
Push-Location $ScriptDir
try {
    $loadErr = (& $VenvPy -c 'import server' 2>&1 | Out-String).Trim()
    $loadCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($loadCode -ne 0) {
    Die "The connector installed but did not load. Please send this to the cohort channel:`n`n$loadErr"
}
Write-Ok 'connector loads'

# ------------------------------------------------------------ 3. credentials

Write-Step 'Your Google OAuth client'

# Two ways in. A person running this by hand gets prompted. An agent (Claude
# Code) driving it passes both values in the environment and is never asked a
# question, because a prompt it cannot answer would hang the whole install.
$ClientId     = $env:GWS_CLIENT_ID
$ClientSecret = $env:GWS_CLIENT_SECRET
$NonInteractive = $false

if ($ClientId -and $ClientSecret) {
    $NonInteractive = $true
    Write-Ok 'using the client passed in the environment'
} elseif ($ClientId -or $ClientSecret) {
    Die @'
Only one of GWS_CLIENT_ID / GWS_CLIENT_SECRET was set.
   Set both, or neither and this script will ask you for them.
'@
} else {
    Write-Host @'

    You need two values from the Google Cloud Console, from the screen
    shown when you created your Desktop OAuth client:

      Client ID       ends with .apps.googleusercontent.com
      Client secret   usually starts with GOCSPX-

    Do not have them yet? Press Ctrl-C, finish the console steps in the
    guide, then run this script again. Nothing done so far is lost.
'@
    $ClientId     = Read-Value 'Paste your Client ID, then press Enter'
    $ClientSecret = Read-Value 'Paste your Client secret, then press Enter'
}

$ClientId     = $ClientId.Trim()
$ClientSecret = $ClientSecret.Trim()

# Shape checks. A value can be present and still be the wrong thing: pasting
# the ID into both boxes is the single most common way this goes wrong, and it
# fails much later with an opaque OAuth error instead of here.
if ($ClientSecret -eq $ClientId) {
    Die @'
The Client ID and Client secret you gave are identical.
   The ID ends with .apps.googleusercontent.com; the secret usually starts with GOCSPX-.
'@
}
if ($ClientSecret.EndsWith('.apps.googleusercontent.com')) {
    Die @'
The Client secret you gave is actually a Client ID (it ends with .apps.googleusercontent.com).
   The secret usually starts with GOCSPX-.
'@
}

function Confirm-OddShape($Label, $Hint) {
    Write-Warn $Label
    Write-Warn $Hint
    if ($NonInteractive) {
        Write-Warn 'Continuing, because both values were supplied deliberately.'
        return
    }
    if (-not (Confirm-Yes 'Use it anyway?')) {
        Die 'Nothing was changed. Run the script again with the right value.'
    }
}

if ($ClientId.EndsWith('.apps.googleusercontent.com')) {
    Write-Ok 'client ID looks right'
} else {
    Confirm-OddShape 'That client ID does not end with .apps.googleusercontent.com, which every Google client ID does.' 'It is easy to paste the project name here by mistake.'
}

if ($ClientSecret.StartsWith('GOCSPX-')) {
    Write-Ok 'client secret looks right'
} else {
    Confirm-OddShape 'That client secret does not start with GOCSPX-, which most Google client secrets do.' 'Older clients can differ, so this may be fine.'
}

# -------------------------------------------------------------- 4. register

Write-Step 'Connecting it to Claude Code'

$ServerPy = Join-Path $ScriptDir 'server.py'

# Re-running should heal a bad value rather than fail on "already exists". The
# remove is unconditional and its failure ignored, so this does not depend on
# parsing `claude mcp list` output, which is a display format, not a contract.
& $ClaudeExe mcp remove $ServerName -s user 2>&1 | Out-Null

& $ClaudeExe mcp add $ServerName -s user -e "GWS_CLIENT_ID=$ClientId" -e "GWS_CLIENT_SECRET=$ClientSecret" -- $VenvPy $ServerPy 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Die "Could not register the connector with Claude Code.`n   Run this to see the error:`n     claude mcp add $ServerName -s user -e GWS_CLIENT_ID=... -e GWS_CLIENT_SECRET=... -- `"$VenvPy`" `"$ServerPy`""
}

Write-Ok "registered as `"$ServerName`""

Write-Host @"

  Installed.

  Two things left, both inside Claude Code:

    1. Quit Claude Code completely and open it again.
       It only picks up new connectors when it starts.

    2. Send it this message:

           Call gws_account_add

       Your browser opens. Sign in as yourself and approve the access.
       Seeing "Google hasn't verified this app"? That is expected, it is
       your own app. Click Advanced, then the link to continue.

  Then try:  Call gmail_search with query "is:unread" and limit 5

"@
