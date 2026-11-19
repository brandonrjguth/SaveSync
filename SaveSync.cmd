@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    py -3 -m savesync %*
) else (
    python -m savesync %*
)
if errorlevel 1 pause
