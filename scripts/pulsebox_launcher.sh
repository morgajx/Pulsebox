#!/usr/bin/env bash
#
# pulsebox_launcher.sh — start PulseBox. Prefers the compiled build whenever
# one exists (the release binary, faster to start) and falls back to the Python
# source otherwise. Set PULSEBOX_FORCE_SOURCE=1 to run source even if a build
# is present. Also starts the "framekeeper" that keeps the Wayland compositor
# presenting frames smoothly (see below).
#
set -u

# --- Python source directory (holds pulsebox.py) ----------------------------
# scripts/ lives inside the source dir, so its parent is the source root.
PY_DIR="${PULSEBOX_DIR_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PY_MAIN="$PY_DIR/pulsebox.py"

# --- Where the compiled build might live ------------------------------------
# Override with PULSEBOX_COMPILED_DIR if you keep it somewhere custom.
COMPILED_DIRS=(
    "${PULSEBOX_COMPILED_DIR:-}"
    "$PY_DIR/dist"
    "$HOME/pulsebox_compiled"
    "$HOME/pulsebox/dist"
)

log() { echo "[launcher] $*"; }

# --- Keep the Wayland compositor's present loop active -----------------------
# On this Pi's wlroots compositor (labwc/cage + XWayland), a fullscreen app's
# frames are presented lazily unless a screencopy client is actively capturing
# — which shows up as sluggish scrolling and half-drawn/duplicated rows. A
# steady, cheap grim capture holds the present loop open. Running it from HERE
# (inside the session the compositor launched) means WAYLAND_DISPLAY/
# XDG_RUNTIME_DIR are already correct — the systemd-user-service route failed
# because cage never triggers graphical-session.target.
start_framekeeper() {
    command -v grim >/dev/null 2>&1 || return 0
    pgrep -f "grim -t ppm" >/dev/null 2>&1 && return 0   # already running
    # Make sure we can reach the compositor even when launched by systemd
    # (labwc path), which has a minimal environment with no WAYLAND_DISPLAY.
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [ -z "${WAYLAND_DISPLAY:-}" ]; then
        for s in "$XDG_RUNTIME_DIR"/wayland-*; do
            [ -S "$s" ] && { export WAYLAND_DISPLAY="$(basename "$s")"; break; }
        done
    fi
    grim -t ppm - >/dev/null 2>&1 || return 0            # only if capture works
    ( while true; do grim -t ppm - >/dev/null 2>&1; sleep 0.05; done ) &
    disown 2>/dev/null || true
    log "framekeeper started ($WAYLAND_DISPLAY; holds present loop, fixes sluggish scroll)"
}
start_framekeeper

run_python() {
    log "Starting Python source: $PY_MAIN"
    cd "$PY_DIR"
    exec /usr/bin/python3 "$PY_MAIN" "$@"
}

# --- Find the newest compiled binary among the candidate dirs ---------------
BEST_DIR=""
BEST_MTIME=0
for DIR in "${COMPILED_DIRS[@]}"; do
    [[ -n "$DIR" ]] || continue
    BIN="$DIR/pulsebox"
    [[ -x "$BIN" ]] || continue
    M="$(stat -c %Y "$BIN" 2>/dev/null || echo 0)"
    if (( M > BEST_MTIME )); then
        BEST_MTIME="$M"
        BEST_DIR="$DIR"
    fi
done

# --- Decide -----------------------------------------------------------------
# Always prefer the compiled build when one exists — it's the release binary
# and starts faster. Fall back to the Python source only when no compiled
# build is present. (To force source even when a build exists, set
# PULSEBOX_FORCE_SOURCE=1.)
if [[ -n "$BEST_DIR" && "${PULSEBOX_FORCE_SOURCE:-0}" != "1" ]]; then
    log "Using compiled build ($BEST_DIR)"
    export PULSEBOX_DIR="$BEST_DIR"
    cd "$BEST_DIR"
    if [[ -x "$BEST_DIR/run_pulsebox.sh" ]]; then
        exec bash "$BEST_DIR/run_pulsebox.sh" "$@"
    fi
    exec "$BEST_DIR/pulsebox" "$@"
fi

run_python "$@"
