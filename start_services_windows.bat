@echo off
REM Loca — Windows startup script
REM Starts the Python proxy. Open http://localhost:8000 in your browser.
REM Prerequisites: Python 3.12, llama-server on PATH

setlocal enabledelayedexpansion

set "DIR=%~dp0"
set "VENV=%DIR%.venv"

echo [Loca] Starting...

REM ── 1. venv setup ──────────────────────────────────────────────────────────
if not exist "%VENV%\Scripts\python.exe" (
    echo [Loca] Setting up Python environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [Loca ERROR] Failed to create venv. Install Python 3.12 from python.org
        pause & exit /b 1
    )
    "%VENV%\Scripts\pip" install -r "%DIR%requirements.txt" -q
    if errorlevel 1 (
        echo [Loca ERROR] Failed to install dependencies.
        pause & exit /b 1
    )
)

REM ── 1b. spaCy English model (voice TTS — Kokoro/misaki) ───────────────────
REM Without this the first /v1/audio/speech request can crash when misaki
REM tries to auto-download via pip. One-time install.
"%VENV%\Scripts\python" -c "import en_core_web_sm" >nul 2>&1
if errorlevel 1 (
    echo [Loca] Installing spaCy English model (voice TTS)...
    "%VENV%\Scripts\python" -m spacy download en_core_web_sm -q
)

REM ── 1c. espeak-ng (voice TTS fallback) ────────────────────────────────────
REM Without espeak-ng, voice replies break on uncommon words. Soft-warn;
REM voice mode is optional and chat still works.
where espeak-ng >nul 2>&1
if errorlevel 1 (
    echo [Loca] espeak-ng not found on PATH — voice TTS may break on uncommon words.
    echo        Install from https://github.com/espeak-ng/espeak-ng/releases
)

REM ── 2. Check llama-server ──────────────────────────────────────────────────
where llama-server >nul 2>&1
if errorlevel 1 (
    echo [Loca ERROR] llama-server not found on PATH.
    echo Download llama.cpp from https://github.com/ggerganov/llama.cpp/releases
    pause & exit /b 1
)

REM ── 3. Kill any existing proxy on port 8000 ────────────────────────────────
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM ── 4. Start proxy ─────────────────────────────────────────────────────────
echo [Loca] Starting proxy...
cd /d "%DIR%"
start /B "" "%VENV%\Scripts\python" -m uvicorn src.proxy:app --host 0.0.0.0 --port 8000 > "%TEMP%\loca-proxy.log" 2>&1

REM Wait for proxy to come up
set /a ATTEMPTS=0
:wait_proxy
set /a ATTEMPTS+=1
if %ATTEMPTS% gtr 30 (
    echo [Loca ERROR] Proxy didn't start. Check %TEMP%\loca-proxy.log
    pause & exit /b 1
)
timeout /t 1 /nobreak >nul
curl -s http://localhost:8000/health >nul 2>&1
if errorlevel 1 goto wait_proxy

echo [Loca] Ready. Opening http://localhost:8000 ...
start http://localhost:8000
echo [Loca] Press Ctrl+C to stop.
pause
