#!/usr/bin/env bash
# =============================================================================
# PulseBox — Nuitka Build Script  v0.208
# =============================================================================
#
# Compiles pulsebox.py + core/ and ui/ packages into a standalone binary.
# The modules/ directory is intentionally NOT compiled — kept as plain Python
# so that dynamic importlib loading (ModuleRegistry) keeps working at runtime.
#
# Usage:
#   bash build.sh              — build to ./dist/
#   bash build.sh --clean      — wipe dist/ and rebuild
#   bash build.sh --jobs N     — override parallel compile jobs (default: nproc)
#
# Requirements (install once on the Pi):
#   pip install nuitka ordered-set zstandard --break-system-packages
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
JOBS=$(nproc 2>/dev/null || echo 2)
CLEAN=false

for arg in "$@"; do
    case $arg in
        --clean) CLEAN=true ;;
        --jobs)  shift; JOBS="$1" ;;
    esac
done

echo "======================================================"
echo "  PulseBox Nuitka Build"
echo "  Source : $SCRIPT_DIR"
echo "  Output : $DIST_DIR"
echo "  Jobs   : $JOBS"
echo "======================================================"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if ! python3 -m nuitka --version &>/dev/null 2>&1; then
    echo "→ Nuitka not found — installing it now..."
    pip install nuitka ordered-set zstandard --break-system-packages --quiet \
        || python3 -m pip install nuitka ordered-set zstandard \
             --break-system-packages --quiet || true
    # patchelf is needed for --standalone/--onefile
    if ! command -v patchelf >/dev/null 2>&1; then
        sudo apt-get install -y patchelf >/dev/null 2>&1 || true
    fi
    if ! python3 -m nuitka --version &>/dev/null 2>&1; then
        echo "ERROR: Nuitka still not available after install attempt."
        echo "       Install manually, then re-run:"
        echo "       pip install nuitka ordered-set zstandard --break-system-packages"
        exit 1
    fi
    echo "  ✓ Nuitka installed."
fi

if $CLEAN; then
    echo "→ Cleaning dist/ ..."
    rm -rf "$DIST_DIR"
fi

mkdir -p "$DIST_DIR"

# ── Run Nuitka ────────────────────────────────────────────────────────────────
echo "→ Compiling with Nuitka (this takes ~10-20 min on a Pi 5) ..."
echo ""

# The binary is --standalone, so it has no access to system site-packages at
# runtime. Plugins in modules/ are loaded dynamically, so any third-party
# library they import is invisible to Nuitka's import scan and must be bundled
# explicitly here — otherwise the compiled binary reports them as missing when
# the plugin loads (e.g. gpiozero/lgpio for the rotary encoder, spotipy for
# Spotify). Each is included only if it's actually installed, so an optional
# feature you don't use never breaks the build.
echo "→ Checking plugin libraries to bundle ..."
EXTRA_INCLUDES=()
for pkg in gpiozero lgpio colorzero spotipy mutagen; do
    if python3 -c "import $pkg" >/dev/null 2>&1; then
        EXTRA_INCLUDES+=("--include-package=$pkg")
        echo "   + bundling $pkg"
    else
        echo "   - skipping $pkg (not installed)"
    fi
done
echo ""

python3 -m nuitka \
    --standalone \
    --onefile \
    --output-filename=pulsebox \
    --output-dir="$DIST_DIR" \
    \
    --include-package=core \
    --include-package=ui \
    "${EXTRA_INCLUDES[@]}" \
    \
    --include-data-files="$SCRIPT_DIR/assets/splash.png=assets/splash.png" \
    --include-data-files="$SCRIPT_DIR/assets/splash_logo.png=assets/splash_logo.png" \
    \
    --enable-plugin=tk-inter \
    \
    --jobs="$JOBS" \
    \
    "$SCRIPT_DIR/pulsebox.py"

echo ""
echo "→ Binary built: $DIST_DIR/pulsebox"

# ── Copy modules/ alongside the binary (kept as Python for dynamic loading) ───
echo "→ Copying modules/ (Python source — loaded dynamically at runtime) ..."
rm -rf "$DIST_DIR/modules"
cp -r "$SCRIPT_DIR/modules" "$DIST_DIR/modules"
find "$DIST_DIR/modules" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$DIST_DIR/modules" -name "*.pyc"       -delete 2>/dev/null || true

# ── Copy assets alongside the binary ─────────────────────────────────────────
echo "→ Copying assets/ ..."
mkdir -p "$DIST_DIR/assets"
cp -r "$SCRIPT_DIR/assets/." "$DIST_DIR/assets/"

# ── Create launcher script ────────────────────────────────────────────────────
# The binary needs to find modules/ and assets/ relative to itself.
# This launcher sets PULSEBOX_DIR so the app locates them correctly.
LAUNCHER="$DIST_DIR/run_pulsebox.sh"
cat > "$LAUNCHER" << 'LAUNCHEOF'
#!/usr/bin/env bash
# PulseBox launcher — sets working directory so binary finds modules/ and assets/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PULSEBOX_DIR="$SCRIPT_DIR"
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/pulsebox" "$@"
LAUNCHEOF
chmod +x "$LAUNCHER"

echo ""
echo "======================================================"
echo "  Build complete!"
echo ""
echo "  Run:"
echo "    bash $DIST_DIR/run_pulsebox.sh"
echo ""
echo "  dist/ layout:"
echo "    dist/"
echo "    ├── pulsebox              ← compiled binary (self-contained)"
echo "    ├── run_pulsebox.sh       ← launcher (use this)"
echo "    ├── modules/              ← Python source (dynamic loading)"
echo "    │   ├── audio_output_configurator.py"
echo "    │   ├── bluetooth_manager.py"
echo "    │   ├── cd_ripper.py"
echo "    │   ├── cover_art_extractor.py"
echo "    │   ├── dvd_ripper.py"
echo "    │   ├── lastfm_scrobbler.py"
echo "    │   ├── spotify_connect.py"
echo "    │   ├── spotify_librespot.py"
echo "    │   └── web_remote.py"
echo "    └── assets/               ← splash images"
echo "======================================================"
