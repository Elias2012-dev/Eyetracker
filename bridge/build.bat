@echo off
setlocal
cd /d "%~dp0"

rem Builds our NPClient bridge DLLs with the bundled TinyCC.
rem TinyCC is downloaded on first run (LGPL, build tool only).

if not exist "tools\tcc\tcc\tcc.exe" (
  echo [build] downloading TinyCC...
  if not exist "tools" mkdir tools
  powershell -NoProfile -Command ^
    "Invoke-WebRequest -Uri 'https://download.savannah.gnu.org/releases/tinycc/tcc-0.9.27-win64-bin.zip' -OutFile 'tools\tcc.zip'"
  powershell -NoProfile -Command "Expand-Archive -Force 'tools\tcc.zip' 'tools\tcc'"
  del tools\tcc.zip
)

"tools\tcc\tcc\tcc.exe" -shared -o NPClient64.dll src\ebt_npclient.c
if errorlevel 1 (
  echo [build] FAILED
  exit /b 1
)
rem ETS2/ATS are 64-bit; ship both well-known file names.
copy /y NPClient64.dll NPClient.dll >nul
echo [build] built NPClient.dll + NPClient64.dll (64-bit)
endlocal
