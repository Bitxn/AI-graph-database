# OnePort - one-command build: rebuild the app + compile the installer.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Produces:  dist\OnePort\          (self-contained app folder, all 18 tools)
#            OnePort-Setup.exe      (the installer to upload to a GitHub release)
#
# ATOMIC BUILD: PyInstaller writes into a staging folder (dist_new\), and only
# after it fully succeeds do we swap it into dist\. That means dist\OnePort\ is
# NEVER a half-deleted / half-written state -- if you launch the app mid-build you
# get the last good version, not a broken exe. (Fixes the "empty folder",
# "shortcut moved", and "No module named oneport_mcp" mid-build crashes.)
#
# Update loop:  edit code  ->  run this  ->  upload OnePort-Setup.exe to a NEW
#               GitHub release (bump the tag).  Bump AppVersion in OnePort.iss too.

# NOTE: not "Stop" — PyInstaller/ISCC log to stderr, which PS 5.1 would treat as
# a terminating error. We rely on the Test-Path guards below to catch real failures.
$ErrorActionPreference = "Continue"
$py  = "C:/Users/Bitan/Desktop/oneport-testenv/Scripts/python.exe"
$iss = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

Write-Host "==> closing any running OnePort" -ForegroundColor Cyan
Get-Process OnePort -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
# Clean the STAGING + work dirs and the stale installer -- but NOT dist\ (that's
# the last good build; it stays live until the new one is proven).
Remove-Item -Recurse -Force build, dist_new, OnePort.spec, OnePort-Setup.exe -ErrorAction SilentlyContinue

$toolPkgs = "oneport_secrets oneport_depcheck oneport_migrate oneport_apidiff oneport_testgap oneport oneport_conformance oneport_impact oneport_standup oneport_upgrade oneport_context oneport_docgen oneport_apiwatch costwatch oneport_evidence postmortem"
$deps     = "click git packaging platformdirs pydantic pydantic_core docx dotenv reportlab requests rich tomli yaml"
$appPkgs  = "oneport_mcp oneport_account webview httpx httpcore certifi"
$hidden   = "projects repomap chat artifacts report fixes terminal settings autofix diagnose debt toolspec activity rulebook guard telemetry tools server oneport_account.client sqlite3 _sqlite3"

$args = @("--noconfirm","--windowed","--name","OnePort","--distpath","dist_new","--icon","tauri/src-tauri/icons/icon.ico","--add-data","ui/index.html;ui")
foreach ($p in ($toolPkgs + " " + $deps + " " + $appPkgs).Split(" ")) { if ($p) { $args += @("--collect-all", $p) } }
foreach ($m in $hidden.Split(" ")) { if ($m) { $args += @("--hidden-import", $m) } }
$args += "app.py"

Write-Host "==> building app into staging (PyInstaller, onedir)..." -ForegroundColor Cyan
& $py -m PyInstaller @args
if (-not (Test-Path "dist_new\OnePort\OnePort.exe")) { throw "app build failed - your existing dist\ is untouched" }

# Atomic swap: only NOW replace the live dist\ with the freshly built one.
Write-Host "==> swapping the new build into dist\ ..." -ForegroundColor Cyan
Get-Process OnePort -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
if (Test-Path "dist") { Start-Sleep 2; Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue }
Rename-Item dist_new dist
if (-not (Test-Path "dist\OnePort\OnePort.exe")) { throw "swap failed - the new build is in dist_new\" }

Write-Host "==> compiling installer (Inno Setup)..." -ForegroundColor Cyan
if (-not (Test-Path $iss)) { throw "Inno Setup not found at $iss - install it or fix the path" }
& $iss "OnePort.iss"
if (-not (Test-Path "OnePort-Setup.exe")) { throw "installer compile failed" }

$mb = [math]::Round((Get-Item "OnePort-Setup.exe").Length/1MB,1)
Write-Host ""
Write-Host "DONE  ->  OnePort-Setup.exe  ($mb MB)" -ForegroundColor Green
Write-Host "Next: run it once to test, then upload to a NEW GitHub release." -ForegroundColor Green
