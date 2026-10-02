@echo off
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

".venv\Scripts\python.exe" -m eyetrack %*
endlocal
