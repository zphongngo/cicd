@echo off
REM CICD App - quan ly chay nen tren Windows:  cicd start [PORT] ^| stop ^| restart [PORT] ^| status ^| logs
REM Lan dau hay chay run.bat de tao moi truong .venv.
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [LOI] Chua co moi truong .venv - hay chay run.bat mot lan truoc.
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0cicd_cli.py" %*
