@echo off
rem ward - global launcher for the WARD CLI (Windows).
rem
rem Installed by `ward.py setup` into %USERPROFILE%\.ward\bin (added to the
rem user PATH) so the CLI works from ANY terminal, in ANY project:
rem
rem     ward setup                          one-command setup, from anywhere
rem     ward check path\to\file.ward0       elaborate + prove + diagnose
rem     ward proof path\to\file.ward0       emit a .proof certificate
rem
rem Resolution order for the WARD checkout:
rem   1. %WARD_HOME%                     (explicit override)
rem   2. %USERPROFILE%\.ward\repo.txt    (the checkout `ward setup` last ran
rem                                       from — beats a stale ~\WARD clone)
rem   3. this script's own repo         (dev layout: <repo>\bin\ward.cmd)
rem   4. %USERPROFILE%\WARD             (one-line installer's default dest)
rem
rem Then it prefers the repo venv python (has lark) and falls back to system
rem python. Exit codes propagate to the caller.
rem
rem NOTE: each line here is parsed and executed sequentially (no compound
rem blocks spanning a set + its use), because cmd expands %VAR% at parse time
rem of the whole block — a set-then-use inside one (...) block silently sees
rem the OLD value. Deliberate structure: goto-based, one command per line.
setlocal EnableExtensions

set "REPO="
if defined WARD_HOME if exist "%WARD_HOME%\ward.py" set "REPO=%WARD_HOME%"
if defined REPO goto found

rem the checkout setup last installed from — beats an older ~\WARD clone.
rem Validate-and-clear immediately: REPO may now be defined but stale (the
rem checkout was moved/deleted), and the later unconditional `if defined
rem REPO goto found` would otherwise proceed with the bad path.
if exist "%USERPROFILE%\.ward\repo.txt" set /p "REPO="<"%USERPROFILE%\.ward\repo.txt"
if defined REPO if not exist "%REPO%\ward.py" set "REPO="
if defined REPO goto found

rem dev layout: <repo>\bin\ward.cmd -> <repo>\ward.py
for %%I in ("%~dp0..") do set "D=%%~fI"
if exist "%D%\ward.py" set "REPO=%D%"
if defined REPO goto found

if exist "%USERPROFILE%\WARD\ward.py" set "REPO=%USERPROFILE%\WARD"
if defined REPO goto found

echo ward: could not find a WARD checkout. 1>&2
echo   set WARD_HOME^=^<checkout dir^>, or run the one-line installer: 1>&2
echo   iex ^(irm https://raw.githubusercontent.com/PersistenceOS/WARD/main/install.ps1^) 1>&2
exit /b 2

:found
if exist "%REPO%\phase0\.venv\Scripts\python.exe" (
  set "PY=%REPO%\phase0\.venv\Scripts\python.exe"
) else if exist "%REPO%\phase0\.venv\bin\python.exe" (
  set "PY=%REPO%\phase0\.venv\bin\python.exe"
) else (
  set "PY=python"
)

"%PY%" "%REPO%\ward.py" %*
exit /b %errorlevel%
