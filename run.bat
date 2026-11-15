@echo off
REM ============================================================
REM  CICD App - chay bang 1 click tren Windows
REM  Lan dau: tu tao moi truong Python (.venv) va cai thu vien.
REM  Dong cua so nay = dung CICD App.
REM ============================================================
setlocal
cd /d "%~dp0"
title CICD App
chcp 65001 >nul

if not defined CICD_PORT set "CICD_PORT=8088"
REM 0.0.0.0 = mo duoc tu may khac bang bat ky IP nao cua may. Chi may nay: set CICD_HOST=127.0.0.1
if not defined CICD_HOST set "CICD_HOST=0.0.0.0"
if not defined CICD_OPEN_BROWSER set "CICD_OPEN_BROWSER=1"
set "PYTHONUTF8=1"

REM ---- 1. Kiem tra Git for Windows ----
where git >nul 2>nul
if errorlevel 1 (
    echo [LOI] Chua cai Git for Windows.
    echo       Tai tai: https://git-scm.com/download/win  ^(cai xong hay mo lai run.bat^)
    goto :fail
)

REM ---- 2. Tim Python 3.10+ ----
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [LOI] Chua cai Python 3.10 tro len.
    echo       Tai tai: https://www.python.org/downloads/  ^(nho tick "Add python.exe to PATH"^)
    goto :fail
)

REM ---- 3. Tao moi truong ao lan dau ----
if not exist ".venv\Scripts\python.exe" (
    echo Lan chay dau tien: dang tao moi truong Python...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc moi truong Python ^(.venv^).
        goto :fail
    )
)

REM ---- 4. Cai / cap nhat thu vien khi requirements.txt thay doi ----
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Dang cai thu vien Python, vui long doi...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [LOI] Cai thu vien that bai - kiem tra ket noi mang roi chay lai.
        goto :fail
    )
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

REM ---- 5. Chay app ----
echo.
echo  CICD App - cac dia chi truy cap (ca tu may khac) hien o dong log ben duoi. Doi cong: run.bat 8888
echo  Trinh duyet se tu mo. Dong cua so nay hoac nhan Ctrl+C de dung.
echo.
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
pause
exit /b 1
