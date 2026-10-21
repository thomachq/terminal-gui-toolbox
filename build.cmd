@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem  Rebuild TerminalToolbox.exe in place (no dist\ subfolder).
rem  Output: %~dp0TerminalToolbox.exe   (same folder as data\)
rem ------------------------------------------------------------------

set "PY="
rem Prefer a project-local venv, then fall back to whatever python is on PATH.
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
    for %%I in (python.exe) do if not "%%~$PATH:I"=="" set "PY=%%~$PATH:I"
)
if not defined PY (
    echo [ERROR] No Python interpreter found.
    echo.
    echo Create a venv with a Tk-capable Python 3.9+ first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements-dev.txt
    pause
    exit /b 1
)

echo Cleaning previous build cache...
if exist build rmdir /s /q build

echo Building...
"%PY%" -m PyInstaller --noconfirm --clean --distpath . toolbox.spec
if errorlevel 1 (
    echo.
    echo [FAILED] see messages above
    pause
    exit /b 1
)

echo.
echo [OK] %~dp0TerminalToolbox.exe
for %%F in ("%~dp0TerminalToolbox.exe") do echo      size: %%~zF bytes
echo.
echo Remember: keep data\ next to the exe. Deleting it destroys the CA.
pause
