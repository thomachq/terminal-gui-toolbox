@echo off
chcp 65001 >nul
title Synology firewall - READ ONLY probe
cd /d "%~dp0"
set "PY=C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" _synoread.py --save
echo.
echo Done. Nothing was changed on the NAS.
pause
