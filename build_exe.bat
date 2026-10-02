@echo off
rem Build the standalone, windowed Eyetracker.exe (no Python, no terminal).
rem
rem   build_exe.bat            -> dist\Eyetracker.exe        (single file)
rem   build_exe.bat --onedir   -> dist\Eyetracker\...       (folder, instant start)
rem
rem The build bundles the bridge DLLs and the MediaPipe model; everything the
rem app writes goes to %APPDATA%\Eyetracker at runtime (see eyetrack/paths.py).
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtualenv...
  py -3 -m venv .venv || python -m venv .venv || python3 -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo [setup] could not create a virtualenv - is Python installed?
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -m pip install -q pyinstaller

if /I "%~1"=="--onedir" (
  set EYE_TRACKER_ONEDIR=1
  echo [build] mode: onedir (folder, starts instantly)
) else (
  set EYE_TRACKER_ONEDIR=0
  echo [build] mode: onefile (single exe, unpacks on launch)
)

echo [build] PyInstaller running - this takes a few minutes the first time...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\eyetracker.spec
if errorlevel 1 (
  echo [build] FAILED - see the PyInstaller output above.
  exit /b 1
)

echo.
echo [build] done:
if "%EYE_TRACKER_ONEDIR%"=="1" (
  echo   dist\Eyetracker\Eyetracker.exe
) else (
  echo   dist\Eyetracker.exe
)
echo.
echo First launch creates %%APPDATA%%\Eyetracker\ for the config and log.
echo Try:  dist\Eyetracker.exe --list-cameras   (or --paths to see the layout)
endlocal