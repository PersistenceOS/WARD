# ============================================================================
# WARD one-line installer (native Windows PowerShell)
#
#   iex (irm https://raw.githubusercontent.com/PersistenceOS/WARD/main/install.ps1)
#
# or download and run:
#   .\install.ps1
#
# What it does:
#   1. clones (or pulls) the WARD repo into $env:WARD_DIR (default ~\WARD)
#   2. creates phase0\.venv and installs the Python deps: `lark` (surface
#      parsing) + `z3-solver` (the standalone z3 backend). z3-solver is
#      non-fatal - check/proof run through Dafny's own Z3 without it.
#   3. runs `ward.py setup` - installs the Claude Code skill + Cursor rule
#      globally, checks the toolchain
#   4. prints a per-tool "you're ready" summary
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 files
# without a UTF-8 BOM using the ANSI code page, so non-ASCII characters
# (em-dashes, smart quotes, ...) silently corrupt into parse errors.
#
# Requires: git, python (3.10+). Dafny + Z3 are checked but not installed
# here - see the note at the end.
# ============================================================================
$ErrorActionPreference = "Stop"

$WARD_REPO = if ($env:WARD_REPO) { $env:WARD_REPO } else { "https://github.com/PersistenceOS/WARD.git" }
$WARD_DIR  = if ($env:WARD_DIR)  { $env:WARD_DIR  } else { Join-Path $HOME "WARD" }

Write-Host "==> WARD installer"
Write-Host "    repo: $WARD_REPO"
Write-Host "    dest: $WARD_DIR"

# ---- 1. clone / pull -------------------------------------------------------
if (-not (Test-Path (Join-Path $WARD_DIR ".git"))) {
    Write-Host "==> cloning WARD..."
    git clone --depth 1 $WARD_REPO $WARD_DIR
    # native commands do NOT throw under $ErrorActionPreference="Stop" in
    # Windows PowerShell 5.1 - check the exit code explicitly
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }
} else {
    Write-Host "==> WARD already present - pulling latest..."
    git -C $WARD_DIR pull --ff-only
    if ($LASTEXITCODE -ne 0) { Write-Host "    (pull skipped: local changes)" }
}
Set-Location $WARD_DIR

# ---- 2. python + venv ------------------------------------------------------
$PYTHON_BIN = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
Write-Host "==> python: $(& $PYTHON_BIN --version 2>&1)"

$VENV_PY = $null
if (Test-Path "phase0/.venv/Scripts/python.exe") { $VENV_PY = "phase0/.venv/Scripts/python.exe" }
elseif (Test-Path "phase0/.venv/bin/python")     { $VENV_PY = "phase0/.venv/bin/python" }

if (-not $VENV_PY) {
    Write-Host "==> creating venv at phase0\.venv ..."
    Push-Location phase0
    & $PYTHON_BIN -m venv .venv
    Pop-Location
    if (Test-Path "phase0/.venv/Scripts/python.exe") { $VENV_PY = "phase0/.venv/Scripts/python.exe" }
    elseif (Test-Path "phase0/.venv/bin/python")     { $VENV_PY = "phase0/.venv/bin/python" }
    else { Write-Error "could not find the venv python after creation."; exit 1 }
}

Write-Host "==> installing Python deps (lark + z3-solver)..."
& $VENV_PY -m pip install --quiet --upgrade lark
# z3-solver powers the standalone SMT backend (E10). Non-fatal: the Dafny
# path (check/proof) uses Dafny's own Z3 and doesn't need it.
& $VENV_PY -m pip install --quiet --upgrade z3-solver
if ($LASTEXITCODE -ne 0) { Write-Host "NOTE: z3-solver install failed - the standalone z3 backend (E10) is unavailable; check/proof still work." }

# ---- 3. install skills + check toolchain -----------------------------------
Write-Host "==> running ward.py setup..."
& $VENV_PY ward.py setup
# setup returns non-zero only on warnings; don't abort the summary below
if ($LASTEXITCODE -ne 0) { Write-Host "(setup reported notes - see above)" }

# ---- 4. toolchain note ------------------------------------------------------
Write-Host ""
Write-Host "==> Done. Quick check:"
Write-Host "    cd $WARD_DIR"
Write-Host "    $VENV_PY ward.py check phase0/benchmarks/w_tasks/w5_currency_roundtrip.ward0"
Write-Host ""
if (-not (Get-Command dafny -ErrorAction SilentlyContinue) -or -not (Get-Command z3 -ErrorAction SilentlyContinue)) {
    Write-Host "NOTE: dafny and/or z3 are not on your PATH."
    Write-Host "  Live verification needs both (Dafny 4.11 + Z3 4.12):"
    Write-Host "    https://github.com/dafny-lang/dafny/releases"
    Write-Host "    https://github.com/Z3Prover/z3/releases"
    Write-Host "  (Certificates - .proof files - still verify without them.)"
    Write-Host "  Re-run 'python ward.py setup' after installing to confirm."
}
Write-Host ""
Write-Host 'To use WARD from any AI tool, just ask: "verify this with ward".'
