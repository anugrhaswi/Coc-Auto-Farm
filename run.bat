@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Virtual environment not found in ".venv"
    echo Create it first with:
    echo     python -m venv .venv
    echo Then install dependencies:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Activating virtual environment...
call ".venv\Scripts\activate.bat"

".venv\Scripts\python.exe" -c "import pywinauto, keyboard, pyautogui, cv2" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Missing required dependencies (pywinauto, keyboard, pyautogui, cv2).
    echo Install them with:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Starting COC Auto Farm...
python main.py
pause
endlocal