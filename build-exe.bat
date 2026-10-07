@echo off
setlocal EnableExtensions
REM ---------------------------------------------------------------------------
REM  CRM Builder - Windows build. Double-click this file.
REM  Installs Python if needed, installs every dependency, and produces
REM  "dist\CRM Builder.exe" as a single standalone file.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
set "LOG=%~dp0build-win-log.txt"
set "VENV=%~dp0.venv-build"
set "EXE=%~dp0dist\CRM Builder.exe"
set "REPORT=%~dp0dist\crm-builder-selftest.txt"
echo CRM Builder build started %DATE% %TIME% > "%LOG%"

echo.
echo ===============================================
echo   Building CRM Builder for Windows
echo   Full log: build-win-log.txt
echo ===============================================
echo.

echo [1/6] Finding or installing Python...
REM  ensure_python.ps1 is shared between apps and reads its override from
REM  MAILEX_BUILD_PYTHON; pass this app's own variable through under that name.
if defined CRMBUILDER_BUILD_PYTHON set "MAILEX_BUILD_PYTHON=%CRMBUILDER_BUILD_PYTHON%"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_python.ps1" 2^>^> "%LOG%"`) do set "PY=%%P"
if not defined PY goto :fail
if not exist "%PY%" goto :fail
echo       using "%PY%"
echo Using Python: "%PY%" >> "%LOG%"

echo [2/6] Creating a clean build environment...
if exist "%VENV%" rmdir /s /q "%VENV%"
"%PY%" -m venv "%VENV%" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail
set "VPY=%VENV%\Scripts\python.exe"

echo [3/6] Installing dependencies (wheels only, no compiling)...
"%VPY%" -m pip install --upgrade pip wheel >> "%LOG%" 2>&1
"%VPY%" -m pip install --only-binary :all: -r "%~dp0requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail
"%VPY%" -m pip install --only-binary :all: pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 goto :fail
REM  Optional: sqlcipher3-wheels gives encrypted CRM files. It is a compiled
REM  package, so the very newest Python may have no wheel for it yet. That is
REM  not an error - the app is built without the encryption option.
REM  (ensure_python.ps1 asks the py launcher for Python 3.13 down to 3.9, which
REM  have one, before anything newer, so there is no fallback loop here.)
REM  sqlcipher3 is a compiled module: PyInstaller is told its name and to take
REM  any libraries shipped beside it, but only when it really installed.
set "ENC_EXTRA="
set "ENC_EXPECT="
echo Optional encryption package: >> "%LOG%"
"%VPY%" -m pip install --only-binary :all: sqlcipher3-wheels >> "%LOG%" 2>&1
"%VPY%" -c "import sqlcipher3.dbapi2" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo       NOTE: there is no encryption package ^(sqlcipher3-wheels^) for this
  echo       Python, so encryption will not be available in this build.
  echo       Everything else works as normal.
  echo Encryption will not be available in this build. >> "%LOG%"
) else (
  echo       Encryption support installed ^(encrypted CRM files^).
  set "ENC_EXTRA=--hidden-import sqlcipher3 --hidden-import sqlcipher3.dbapi2 --collect-binaries sqlcipher3"
  set "ENC_EXPECT=--expect-encryption"
)

echo [4/6] Checking the code before packaging...
REM  The working directory is already this folder, so '.' is the source tree.
REM  %~dp0 must NOT go inside the Python string: it always ends in a backslash,
REM  which would escape the closing quote and make it an unterminated literal.
"%VPY%" -c "import sys, pkgutil, importlib; sys.path.insert(0,'.'); import crmbuilder, openpyxl; n=[importlib.import_module(m.name) for m in pkgutil.walk_packages(crmbuilder.__path__,'crmbuilder.')]; print('imports ok:', len(n), 'modules')" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [5/6] Packaging (this takes a minute or two)...
if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0dist" rmdir /s /q "%~dp0dist"
REM  crmbuilder's pages are loaded by name at run time, so PyInstaller cannot
REM  see them by itself: --collect-submodules takes the whole package.
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "CRM Builder" ^
  --collect-submodules crmbuilder ^
  --hidden-import openpyxl ^
  --hidden-import tkinter ^
  --hidden-import tkinter.ttk ^
  --hidden-import tkinter.filedialog ^
  --hidden-import tkinter.messagebox ^
  --hidden-import sqlite3 ^
  --exclude-module pytest ^
  %ENC_EXTRA% ^
  "%~dp0crm_builder_app.py" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail
if not exist "%EXE%" goto :fail

echo [6/6] Running the self-test on the built program...
echo       It makes a test CRM from every template in a temporary folder and
echo       then opens every page. A CRM Builder window will flick through its
echo       pages for a few seconds - leave it alone until it closes by itself.
if exist "%REPORT%" del /q "%REPORT%"
pushd "%~dp0dist"
REM  A windowed build has no console, so a plain invocation returns at once and
REM  the report would be read before it exists. start /wait blocks until the
REM  self-test has actually finished.
start "CRM Builder self-test" /wait "%EXE%" selftest %ENC_EXPECT%
popd
if not exist "%REPORT%" goto :noreport
echo.
type "%REPORT%"
findstr /C:"SELF-TEST PASSED" "%REPORT%" >nul
if errorlevel 1 goto :selftestfail

echo.
echo ===============================================
echo   DONE.  dist\CRM Builder.exe is ready.
if not defined ENC_EXTRA echo   Built without encryption - see the note at step 3.
echo ===============================================
echo.
pause
exit /b 0

:noreport
echo.
echo ===============================================
echo   BUILT, BUT THE PROGRAM DID NOT RUN ITS SELF-TEST.
echo   It probably does not start. Look for
echo   dist\crm-builder-crash.log
echo ===============================================
echo.
pause
exit /b 2

:selftestfail
echo.
echo ===============================================
echo   BUILT, BUT THE SELF-TEST REPORTED PROBLEMS.
echo   See dist\crm-builder-selftest.txt above.
echo ===============================================
echo.
pause
exit /b 2

:fail
echo.
echo ===============================================
echo   BUILD FAILED.  Last 40 lines of the log:
echo ===============================================
powershell -NoProfile -Command "Get-Content -Tail 40 -LiteralPath '%LOG%'"
echo.
echo Full log: "%LOG%"
echo.
pause
exit /b 1
