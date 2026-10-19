@echo off
setlocal
title ArcScaler
cd /d "%~dp0"
if exist ArcScaler.exe (
    start "" ArcScaler.exe
) else (
    py main.py
    if errorlevel 1 pause
)
