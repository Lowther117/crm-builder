#!/bin/bash
# ---------------------------------------------------------------------------
#  CRM Builder - macOS build. Double-click this file.
#  Installs Homebrew and Python if they are missing, installs every
#  dependency, and produces "dist/CRM Builder.app".
# ---------------------------------------------------------------------------
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
LOG="$HERE/build-mac-log.txt"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# A double-clicked .command starts with a bare PATH that has no Homebrew in it.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1

APP="$HERE/dist/CRM Builder.app"
REPORT="$HERE/dist/crm-builder-selftest.txt"

echo "==============================================="
echo "  Building CRM Builder for macOS"
echo "  Full log: build-mac-log.txt"
echo "==============================================="
echo

die() {
  echo
  echo "==============================================="
  echo "  BUILD FAILED: $*"
  echo "==============================================="
  echo
  echo "Press return to close."
  read -r
  exit 1
}

ensure_brew() {
  if command -v brew >/dev/null 2>&1; then return 0; fi
  echo "Homebrew is not installed. Installing it now - it will ask for your password once."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty \
    || die "Homebrew would not install."
  export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
  command -v brew >/dev/null 2>&1 || die "Homebrew installed but brew is still not on the PATH."
}

brew_install() {
  ensure_brew
  echo "  brew install $*"
  brew install "$@" < /dev/null || die "brew install $* failed."
}

# Apple's /usr/bin/python3 links against system Tk 8.5, which PyInstaller
# cannot bundle: the app builds and then never opens a window. Only accept an
# interpreter whose tkinter reports 8.6 or newer. Resolve candidates with
# readlink rather than running python3, because on a clean Mac running
# python3 pops up the Xcode command line tools installer.
#
# pick_python            prints the newest suitable interpreter
# pick_python ":a:b:"    the same, leaving out the interpreters listed between
#                        the colons (used to step down to an older Python when
#                        the newest has no encryption package yet)
pick_python() {
  local skip="${1:-}" c real
  if [ -z "$skip" ] && [ -n "$CRMBUILDER_BUILD_PYTHON" ] && [ -x "$CRMBUILDER_BUILD_PYTHON" ]; then
    if "$CRMBUILDER_BUILD_PYTHON" -c 'import tkinter,sys;sys.exit(0 if tkinter.TkVersion>=8.6 else 1)' 2>/dev/null; then
      echo "$CRMBUILDER_BUILD_PYTHON"; return 0
    fi
  fi
  for c in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
           /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3.9 \
           /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /usr/local/bin/python3.11 /usr/local/bin/python3.10 /usr/local/bin/python3.9 \
           /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.9/bin/python3; do
    [ -e "$c" ] || continue
    real="$(readlink -f "$c" 2>/dev/null || echo "$c")"
    [ -x "$real" ] || continue
    case "$real" in /usr/bin/python3) continue ;; esac
    case "$skip" in *":$real:"*) continue ;; esac
    if "$real" -c 'import tkinter,sys;sys.exit(0 if tkinter.TkVersion>=8.6 else 1)' 2>/dev/null; then
      echo "$real"; return 0
    fi
  done
  return 1
}

pyver() { "$1" -c 'import sys;print("Python %d.%d" % sys.version_info[:2])'; }

VENV="$HERE/.venv-build-mac"
VPY="$VENV/bin/python"

# A fresh environment on the given interpreter, with the packages the app needs.
make_env() {
  rm -rf "$VENV"
  "$1" -m venv "$VENV" || die "Could not create the build environment."
  "$VPY" -m pip install --upgrade pip wheel >/dev/null || die "pip would not upgrade (is this Mac online?)."
  "$VPY" -m pip install --only-binary :all: -r "$HERE/requirements.txt" || die "A dependency would not install (is this Mac online?)."
}

# Optional: sqlcipher3-wheels gives encrypted CRM files. It is a compiled
# package, so the very newest Python may have no wheel for it yet. pip's own
# output goes to the log only - "no matching distribution" is not an error here.
try_encryption() {
  "$VPY" -m pip install --only-binary :all: sqlcipher3-wheels >> "$LOG" 2>&1 \
    && "$VPY" -c "import sqlcipher3.dbapi2" >> "$LOG" 2>&1
}

echo "[1/7] Finding a Python with a usable Tk..."
PY="$(pick_python)" || {
  echo "      None found. Installing Python and Tk through Homebrew..."
  brew_install python python-tk
  PY="$(pick_python)" || die "Even after installing python and python-tk, no interpreter reports Tk 8.6+."
}
echo "      using $PY"
"$PY" -c 'import sys,tkinter;print("      python",sys.version.split()[0],"tk",tkinter.TkVersion)'

echo "[2/7] Creating a clean build environment..."
make_env "$PY"

echo "[3/7] Installing the remaining dependencies (wheels only, no compiling)..."
# Encryption first, because it decides which Python the app is built with: if
# the newest suitable Python has no wheel for it and an older one is installed,
# build on the older one instead. A Python named in CRMBUILDER_BUILD_PYTHON is
# never second-guessed.
FIRST_PY="$PY"
SKIP=":"
HAVE_ENC=no
while :; do
  if try_encryption; then HAVE_ENC=yes; break; fi
  [ "$PY" = "$CRMBUILDER_BUILD_PYTHON" ] && break
  SKIP="$SKIP$PY:"
  NEXT="$(pick_python "$SKIP")" || break
  echo "      No encryption package for $(pyver "$PY") - trying $(pyver "$NEXT") instead..."
  PY="$NEXT"
  make_env "$PY"
done
ENC_EXTRA=""
ENC_EXPECT=""
if [ "$HAVE_ENC" = yes ]; then
  echo "      Encryption support installed (encrypted CRM files), building with $(pyver "$PY")."
  # sqlcipher3 is a compiled module; name it and take any libraries shipped beside it.
  ENC_EXTRA="--hidden-import sqlcipher3 --hidden-import sqlcipher3.dbapi2 --collect-binaries sqlcipher3"
  ENC_EXPECT="--expect-encryption"
else
  if [ "$PY" != "$FIRST_PY" ]; then
    PY="$FIRST_PY"
    make_env "$PY"
  fi
  echo "      NOTE: there is no encryption package (sqlcipher3-wheels) for $(pyver "$PY") on"
  echo "      this Mac, so encryption will not be available in this build."
  echo "      Everything else works as normal."
fi
"$VPY" -m pip install --only-binary :all: pyinstaller || die "PyInstaller would not install."

echo "[4/7] Checking the code before packaging..."
"$VPY" -c "import sys, pkgutil, importlib; sys.path.insert(0,'.'); import crmbuilder, openpyxl; n=[importlib.import_module(m.name) for m in pkgutil.walk_packages(crmbuilder.__path__,'crmbuilder.')]; print('      imports ok:', len(n), 'modules')" \
  || die "The application does not import cleanly."

echo "[5/7] Packaging (this takes a minute or two)..."
rm -rf "$HERE/build" "$HERE/dist"
# crmbuilder's pages are loaded by name at run time, so PyInstaller cannot see
# them by itself: --collect-submodules takes the whole package.
# shellcheck disable=SC2086  # ENC_EXTRA is a list of separate options on purpose
"$VPY" -m PyInstaller --noconfirm --clean --windowed \
  --name "CRM Builder" \
  --osx-bundle-identifier "uk.lowther.crmbuilder" \
  --collect-submodules crmbuilder \
  --hidden-import openpyxl \
  --hidden-import tkinter \
  --hidden-import tkinter.ttk \
  --hidden-import tkinter.filedialog \
  --hidden-import tkinter.messagebox \
  --hidden-import sqlite3 \
  --exclude-module pytest \
  $ENC_EXTRA \
  "$HERE/crm_builder_app.py" || die "PyInstaller failed - see the log above."
[ -d "$APP" ] || die "dist/CRM Builder.app was not produced."

echo "[6/7] Clearing quarantine and signing locally..."
# Without these two an app built on Apple silicon is killed the moment it opens.
xattr -cr "$APP" || true
codesign --force --deep --sign - "$APP" || die "Ad-hoc signing failed."

echo "[7/7] Running the self-test on the built app..."
echo "      It makes a test CRM from every template in a temporary folder and"
echo "      then opens every page. A CRM Builder window will flick through its"
echo "      pages for a few seconds - leave it alone until it closes by itself."
rm -f "$REPORT"
# The report file is the result; the same text on stdout would only show twice.
# shellcheck disable=SC2086
"$APP/Contents/MacOS/CRM Builder" selftest $ENC_EXPECT > /dev/null || true
sleep 2
PROBLEM=""
if [ ! -f "$REPORT" ]; then
  PROBLEM="THE BUILT APP DID NOT RUN ITS SELF-TEST - it probably does not start. Look for dist/crm-builder-crash.log."
else
  echo
  cat "$REPORT"
  grep -q "SELF-TEST PASSED" "$REPORT" || PROBLEM="See dist/crm-builder-selftest.txt above."
fi
if [ -n "$PROBLEM" ]; then
  echo
  echo "==============================================="
  echo "  BUILT, BUT THE SELF-TEST REPORTED PROBLEMS."
  echo "  $PROBLEM"
  echo "==============================================="
  echo
  echo "Press return to close."
  read -r
  exit 2
fi

echo
echo "==============================================="
echo "  DONE.  dist/CRM Builder.app is ready."
echo "  Drag it to your Applications folder."
[ "$HAVE_ENC" = yes ] || echo "  (Built without encryption - see the note at step 3.)"
echo "==============================================="
echo
echo "Press return to close."
read -r
exit 0
