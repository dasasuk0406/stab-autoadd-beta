@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [오류] 가상환경이 없습니다. install.bat 을 먼저 실행하세요.
    pause
    exit /b 1
)

:: 기존 Chrome 창 사용 옵션: --attach 또는 --attach=포트번호
set CHROME_PORT=9222
set ATTACH=0

for %%A in (%*) do (
    if "%%A"=="--attach" set ATTACH=1
    echo %%A | findstr /b "--attach=" >nul && (
        set ATTACH=1
        for /f "tokens=2 delims==" %%B in ("%%A") do set CHROME_PORT=%%B
    )
)

if %ATTACH%==1 (
    echo Chrome 디버그 모드로 실행 중 (포트 %CHROME_PORT%)...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%CHROME_PORT% 2>nul || ^
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%CHROME_PORT% 2>nul || ^
    start "" chrome --remote-debugging-port=%CHROME_PORT%
    echo Chrome이 열렸습니다. nearminton에 로그인 후 UI에서 '기존 Chrome 창 사용'을 체크하세요.
    timeout /t 2 /nobreak >nul
)

echo STAB AutoAdd System 시작 중...
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe autoadd_ui_v2.py
pause
