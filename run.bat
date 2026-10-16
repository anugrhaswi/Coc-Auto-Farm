@echo off
setlocal
cd /d "%~dp0"
title COC Auto Farm

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

rem --- Find or create the virtual environment --------------------------------
if exist "%VENV_PY%" goto :have_venv

echo This looks like the first run: a Python virtual environment (.venv)
echo is needed, along with its dependencies.
echo.
echo The first install can take several minutes (it downloads a few GB).
echo.
set /p "DO_SETUP=Create the environment and install dependencies now? [Y/n] "
if /i "%DO_SETUP%"=="n" goto :no_setup

rem Locate a system Python 3 interpreter to build the venv with.
set "SYS_PY="
where py >nul 2>&1 && set "SYS_PY=py -3"
if not defined SYS_PY (
    where python >nul 2>&1 && set "SYS_PY=python"
)
if not defined SYS_PY (
    echo.
    echo ERROR: Python 3 was not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/ and retry.
    goto :fail
)

echo.
echo Creating virtual environment at .venv ...
%SYS_PY% -m venv ".venv"
if errorlevel 1 goto :fail

echo Installing dependencies (this can take several minutes) ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

:have_venv

rem --- Verify dependencies ---------------------------------------------------
"%VENV_PY%" -c "import cv2, keyboard, PIL, numpy, ultralytics, easyocr" >nul 2>&1
if not errorlevel 1 goto :deps_ok

echo.
echo Some dependencies are missing or broken in .venv.
set /p "DO_REPAIR=Install/repair dependencies now? [Y/n] "
if /i "%DO_REPAIR%"=="n" goto :deps_skipped

echo Installing dependencies (this can take several minutes) ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
"%VENV_PY%" -c "import cv2, keyboard, PIL, numpy, ultralytics, easyocr" >nul 2>&1
if errorlevel 1 (
    echo ERROR: dependencies are still not importable.
    goto :fail
)
goto :deps_ok

:deps_skipped
echo Continuing without installing; the app may fail to start.

:deps_ok

rem --- Model check (warn only) -----------------------------------------------
if not exist "models\best.pt" (
    echo.
    echo WARNING: models\best.pt was not found.
    echo Place your trained YOLO model there, or set model_path in config.py.
    echo See docs\USAGE.md for details.
    echo.
)

echo Starting COC Auto Farm ...
"%VENV_PY%" main.py
if errorlevel 1 goto :fail

endlocal
exit /b 0

:no_setup
echo.
echo Skipped. To set it up manually:
echo   py -3 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
endlocal
exit /b 1

:fail
echo.
echo Something went wrong - read the messages above.
pause
endlocal
exit /b 1
