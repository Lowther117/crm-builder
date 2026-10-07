#!/bin/bash
# Run CRM Builder from source on macOS, setting up a virtual environment the
# first time. Use build-app.command if you want a standalone .app instead.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
VENV=".venv"

# The same rule as build-app.command: Apple's /usr/bin/python3 links against
# system Tk 8.5, which draws this app wrongly or not at all, so only accept an
# interpreter whose tkinter reports 8.6 or newer. Candidates are resolved with
# readlink rather than by running python3, because on a clean Mac running
# python3 pops up the Xcode command line tools installer.
pick_python() {
  local c real
  if [ -n "$CRMBUILDER_BUILD_PYTHON" ] && [ -x "$CRMBUILDER_BUILD_PYTHON" ]; then
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
    if "$real" -c 'import tkinter,sys;sys.exit(0 if tkinter.TkVersion>=8.6 else 1)' 2>/dev/null; then
      # the name it was found under, not the resolved one: a Homebrew update
      # moves the resolved path, and the environment made here should survive that
      echo "$c"; return 0
    fi
  done
  return 1
}

if [ ! -x "$VENV/bin/python" ]; then
  echo "First run - setting Python up. This happens once."
  PY="$(pick_python)" || {
    echo "No Python with a usable Tk (8.6 or newer) was found. Apple's own"
    echo "/usr/bin/python3 is not used: its Tk 8.5 cannot draw this app."
    echo "Run build-app.command once; it installs a suitable Python."
    read -r; exit 1
  }
  "$PY" -m venv "$VENV" || { echo "Could not create the environment."; read -r; exit 1; }
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install --only-binary :all: -r requirements.txt || {
    # remove the half-made environment, or the next run would see it, skip the
    # set-up and start the app without its packages
    rm -rf "$VENV"
    echo "The packages CRM Builder needs would not install - is this Mac online?"
    echo "Nothing has been left half set up. Run this again once it is."
    read -r; exit 1
  }
  # optional: encrypted CRM files; fine if there is no wheel for this Python
  "$VENV/bin/python" -m pip install --only-binary :all: sqlcipher3-wheels >/dev/null 2>&1 \
    || echo "Note: no encryption package for this Python - encrypted CRM files will not be available."
fi
exec "$VENV/bin/python" crm_builder.py "$@"
