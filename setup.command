#!/bin/bash
# Breathe for macOS -- one-time setup / updater. Run WITHOUT sudo.
set -e
cd "$(dirname "$0")"

if [ "$(id -u)" -eq 0 ]; then
    echo "Please run WITHOUT sudo:"
    echo "    bash \"$(pwd)/setup.command\""
    exit 1
fi

echo "==> Setting up Breathe..."

# 1. Find Python 3.9+
PY=""
for cand in "$(command -v python3 2>/dev/null)" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.9+ was not found. Run:  xcode-select --install   then rerun this."
    exit 1
fi
echo "==> Using Python: $PY"

# 2. Private virtual environment (never touches system Python)
SUPPORT="$HOME/Library/Application Support/BreatheApp"
VENV="$SUPPORT/venv"
mkdir -p "$SUPPORT"
if [ ! -x "$VENV/bin/python3" ]; then
    echo "==> Creating environment (one time)..."
    "$PY" -m venv "$VENV"
fi
echo "==> Installing dependencies (PySide6, pyobjc)..."
"$VENV/bin/python3" -m pip install --upgrade pip --quiet --no-cache-dir
"$VENV/bin/python3" -m pip install --upgrade PySide6 pyobjc-framework-Cocoa --quiet --no-cache-dir

# 3. Copies used by the fallback launcher
cp "breathe_app.py" "$SUPPORT/breathe_app.py"
[ -f "icon.png" ] && cp "icon.png" "$SUPPORT/icon.png"

# 4. Build Breathe.app with a tiny native launcher -> ONE Dock icon
echo "==> Building Breathe.app..."
APP="$HOME/Applications/Breathe.app"
mkdir -p "$HOME/Applications"
chmod -R u+w "$APP" 2>/dev/null || true
rm -rf "$APP"

TMPB="$(mktemp -d)"
cat > "$TMPB/launcher.c" << 'CEOF'
#include <mach-o/dyld.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX];
    uint32_t n = sizeof(exe);
    if (_NSGetExecutablePath(exe, &n) != 0) return 1;
    char real[PATH_MAX];
    if (!realpath(exe, real)) return 1;

    char macos_dir[PATH_MAX];
    snprintf(macos_dir, sizeof(macos_dir), "%s", dirname(real));      /* .../Contents/MacOS */
    char contents[PATH_MAX];
    snprintf(contents, sizeof(contents), "%s", dirname(macos_dir));   /* .../Contents */

    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/Resources/breathe_app.py", contents);

    const char *home = getenv("HOME");
    if (!home) return 1;
    char py[PATH_MAX];
    snprintf(py, sizeof(py),
             "%s/Library/Application Support/BreatheApp/venv/bin/python3", home);

    execl(py, py, script, (char *)NULL);
    return 1;  /* exec failed */
}
CEOF

BUILT=""
if command -v cc >/dev/null 2>&1; then
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    if cc -O2 -o "$APP/Contents/MacOS/breathe" "$TMPB/launcher.c" 2>/dev/null; then
        cat > "$APP/Contents/Info.plist" << 'PLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>Breathe</string>
    <key>CFBundleDisplayName</key>       <string>Breathe</string>
    <key>CFBundleIdentifier</key>        <string>com.v.breatheapp</string>
    <key>CFBundleVersion</key>           <string>3.1</string>
    <key>CFBundleShortVersionString</key><string>3.1</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleExecutable</key>        <string>breathe</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLEOF
        cp "breathe_app.py" "$APP/Contents/Resources/breathe_app.py"
        [ -f "icon.png" ] && cp "icon.png" "$APP/Contents/Resources/icon.png"
        [ -f "Breathe.icns" ] && cp "Breathe.icns" "$APP/Contents/Resources/AppIcon.icns"
        codesign -s - --force "$APP/Contents/MacOS/breathe" >/dev/null 2>&1 || true
        BUILT="native (single Dock icon)"
    else
        rm -rf "$APP"
    fi
fi

if [ -z "$BUILT" ]; then
    # Fallback: AppleScript launcher applet
    cat > "$TMPB/breathe.applescript" << OSAEOF
do shell script "pgrep -f 'BreatheApp/breathe_app.py' >/dev/null || nohup '$VENV/bin/python3' '$SUPPORT/breathe_app.py' >/dev/null 2>&1 &"
OSAEOF
    osacompile -o "$APP" "$TMPB/breathe.applescript"
    [ -f "Breathe.icns" ] && cp "Breathe.icns" "$APP/Contents/Resources/applet.icns"
    BUILT="applet (fallback)"
fi
rm -rf "$TMPB"
touch "$APP"
echo "==> Launcher: $BUILT"

# 5. Refresh the Dock's icon cache, then launch
killall Dock >/dev/null 2>&1 || true
sleep 1
echo ""
echo "==> Done! Launching Breathe..."
open "$APP"
echo ""
echo "Pin it: Finder > Cmd+Shift+G > ~/Applications > drag Breathe.app to the Dock."
