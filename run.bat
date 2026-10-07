@echo off
setlocal EnableExtensions
REM Run CRM Builder from source on Windows, setting up a virtual environment
REM the first time. Use build-exe.bat if you want a standalone .exe instead.
cd /d "%~dp0"
set "VENV=%~dp0.venv"
if exist "%VENV%\Scripts\python.exe" goto :run

echo First run - setting Python up. This happens once.
REM  ensure_python.ps1 is shared between apps and reads its override from
REM  MAILEX_BUILD_PYTHON; pass this app's own variable through under that name.
if defined CRMBUILDER_BUILD_PYTHON set "MAILEX_BUILD_PYTHON=%CRMBUILDER_BUILD_PYTHON%"
REM  %PY% is set and used in separate statements on purpose: inside one
REM  parenthesised block cmd expands variables before the block runs.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_python.ps1"`) do set "PY=%%P"
if not defined PY goto :nopython
if not exist "%PY%" goto :nopython
"%PY%" -m venv "%VENV%"
if errorlevel 1 ( echo Could not create the environment. & pause & exit /b 1 )
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
"%VENV%\Scripts\python.exe" -m pip install --only-binary :all: -r "%~dp0requirements.txt"
if errorlevel 1 goto :nodeps
REM  optional: encrypted CRM files; fine if there is no wheel for this Python
"%VENV%\Scripts\python.exe" -m pip install --only-binary :all: sqlcipher3-wheels >nul 2>&1
if errorlevel 1 echo Note: no encryption package for this Python - encrypted CRM files will not be available.

:run
REM  "run.bat selftest" needs a console to print to, so it uses python.exe
REM  and waits; everything else opens the window with no console behind it.
if /i "%~1"=="selftest" goto :selftest
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0crm_builder.py" %*
exit /b 0

:selftest
"%VENV%\Scripts\python.exe" "%~dp0crm_builder.py" %*
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%

:nodeps
REM  Remove the half-made environment, or the next run would see it, skip the
REM  set-up and start the app without its packages.
echo.
echo The packages CRM Builder needs would not install - is this PC online?
echo Nothing has been left half set up. Run this file again once it is.
rmdir /s /q "%VENV%"
pause
exit /b 1

:nopython
echo Could not find or install Python.
pause
exit /b 1
