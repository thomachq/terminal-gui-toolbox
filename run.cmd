@echo off
setlocal
title Terminal GUI Toolbox
cd /d "%~dp0"

rem ---- Locate an interpreter with tkinter + paramiko + cryptography ----
rem 1) project-local venv  (recommended: python -m venv .venv)
rem 2) whatever python is on PATH
rem 3) explicit override:  set TERMTOOLBOX_PYTHON=D:\some\venv\Scripts\python.exe
set "PYW="
set "PYC="
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%~dp0.venv\Scripts\python.exe"  set "PYC=%~dp0.venv\Scripts\python.exe"

rem 2) whatever python is on PATH
if not defined PYW (
    for %%I in (pythonw.exe) do if not "%%~$PATH:I"=="" set "PYW=%%~$PATH:I"
)
if not defined PYC (
    for %%I in (python.exe) do if not "%%~$PATH:I"=="" set "PYC=%%~$PATH:I"
)

rem 3) explicit override wins over the two above
if defined TERMTOOLBOX_PYTHON (
    if exist "%TERMTOOLBOX_PYTHON%" (
        set "PYC=%TERMTOOLBOX_PYTHON%"
        set "PYW=%TERMTOOLBOX_PYTHON:python.exe=pythonw.exe%"
        if not exist "%PYW%" set "PYW="
    )
)

if not defined PYC (
    echo [ERROR] No Python interpreter found.
    echo.
    echo Create a venv first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo   or:  set TERMTOOLBOX_PYTHON=D:\path\to\venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

rem fail fast with a readable message instead of a tkinter traceback
"%PYC%" -c "import paramiko, cryptography" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] paramiko / cryptography missing in:
    echo         %PYC%
    echo.
    echo Install them with:
    echo   "%PYC%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

rem pythonw keeps the console clean; on failure re-run with python to show the traceback
if defined PYW (
    "%PYW%" "%~dp0app.py" %*
    if not errorlevel 1 goto :eof
    echo.
    echo [startup failed] re-running in console mode to show the error ...
    echo.
)
"%PYC%" "%~dp0app.py" %*
if errorlevel 1 (
    echo.
    pause
)
