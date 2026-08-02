#!/usr/bin/env bash
# ============================================================================
# WARD one-line installer (POSIX / macOS / Linux / Git Bash on Windows)
#
#   curl -fsSL https://raw.githubusercontent.com/PersistenceOS/WARD/main/install.sh | bash
#
# or download and run:
#   bash install.sh
#
# What it does:
#   1. clones (or pulls) the WARD repo into ~/WARD  (override: WARD_DIR=...)
#   2. creates phase0/.venv and installs `lark` (the only Python dep)
#   3. runs `ward.py setup` — installs the Claude Code skill + Cursor rule
#      globally, checks the toolchain
#   4. prints a per-tool "you're ready" summary
#
# Requires: git, python3 (3.10+). Dafny + Z3 are checked but not installed
# here — see the note at the end (they're the only missing piece, and only
# for *live verification*; certificates still verify without them).
# ============================================================================
set -euo pipefail

WARD_REPO="${WARD_REPO:-https://github.com/PersistenceOS/WARD.git}"
WARD_DIR="${WARD_DIR:-$HOME/WARD}"

echo "==> WARD installer"
echo "    repo: $WARD_REPO"
echo "    dest: $WARD_DIR"

# ---- 1. clone / pull -------------------------------------------------------
if [ ! -d "$WARD_DIR/.git" ]; then
    echo "==> cloning WARD…"
    git clone --depth 1 "$WARD_REPO" "$WARD_DIR"
else
    echo "==> WARD already present — pulling latest…"
    git -C "$WARD_DIR" pull --ff-only || echo "    (pull skipped: local changes)"
fi
cd "$WARD_DIR"

# ---- 2. python + venv ------------------------------------------------------
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN=python3
    else
        PYTHON_BIN=python
    fi
fi
echo "==> python: $($PYTHON_BIN --version 2>&1)"

VENV_PY=""
if [ -x "phase0/.venv/bin/python" ]; then
    VENV_PY="phase0/.venv/bin/python"
elif [ -x "phase0/.venv/Scripts/python.exe" ]; then
    VENV_PY="phase0/.venv/Scripts/python.exe"
fi

if [ -z "$VENV_PY" ]; then
    echo "==> creating venv at phase0/.venv …"
    (cd phase0 && "$PYTHON_BIN" -m venv .venv)
    if [ -x "phase0/.venv/bin/python" ]; then
        VENV_PY="phase0/.venv/bin/python"
    elif [ -x "phase0/.venv/Scripts/python.exe" ]; then
        VENV_PY="phase0/.venv/Scripts/python.exe"
    else
        echo "ERROR: could not find the venv python after creation." >&2
        exit 1
    fi
fi

echo "==> installing lark into the venv…"
"$VENV_PY" -m pip install --quiet --upgrade lark

# ---- 3. install skills + check toolchain -----------------------------------
echo "==> running ward.py setup…"
"$VENV_PY" ward.py setup || true   # setup returns non-zero only on warnings

# ---- 4. toolchain note ------------------------------------------------------
echo
echo "==> Done. Quick check:"
echo "    cd $WARD_DIR"
echo "    $VENV_PY ward.py check phase0/benchmarks/w_tasks/w5_currency_roundtrip.ward0"
echo
if ! command -v dafny >/dev/null 2>&1 || ! command -v z3 >/dev/null 2>&1; then
    echo "NOTE: dafny and/or z3 are not on your PATH."
    echo "  Live verification needs both (Dafny 4.11 + Z3 4.12):"
    echo "    https://github.com/dafny-lang/dafny/releases"
    echo "    https://github.com/Z3Prover/z3/releases"
    echo "  (Certificates — .proof files — still verify without them.)"
    echo "  Re-run 'python ward.py setup' after installing to confirm."
fi
echo
echo "To use WARD from any AI tool, just ask: \"verify this with ward\"."
