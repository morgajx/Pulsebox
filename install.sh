#!/usr/bin/env bash

# ---------------------------------------------------------------------------
# Ask yes/no — returns 0 (yes) or 1 (no)
# Skip prompt when --all or --core-only set
# ---------------------------------------------------------------------------
ask() {
    local prompt="$1"
    local default="${2:-y}"    # y or n
    if $INSTALL_ALL; then return 0; fi
    if $CORE_ONLY;  then return 1; fi
    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$(echo -e "${YELLOW}  ?${RESET} ${prompt} [Y/n]: ")" yn
        yn="${yn:-y}"
    else
        read -rp "$(echo -e "${YELLOW}  ?${RESET} ${prompt} [y/N]: ")" yn
        yn="${yn:-n}"
    fi
    [[ "$yn" =~ ^[Yy]$ ]]
}

ask_input() {
    # ask_input "prompt" "default_value"  →  sets global REPLY
    local prompt="$1"
    local default="${2:-}"
    read -rp "$(echo -e "${YELLOW}  ?${RESET} ${prompt} [${default}]: ")" REPLY
    REPLY="${REPLY:-$default}"
}

# ---------------------------------------------------------------------------
# Wait for dpkg/apt locks to be released (unattended-upgrades, etc.)
# ---------------------------------------------------------------------------
wait_for_apt() {
    local waited=0
    while sudo fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock                      /var/cache/apt/archives/lock >/dev/null 2>&1; do
        if [[ $waited -eq 0 ]]; then
            info "Waiting for another apt process to finish..."
        fi
        sleep 3
        waited=$((waited + 3))
        if [[ $waited -ge 120 ]]; then
            warn "apt lock held for 2+ minutes — trying to continue anyway"
            break
        fi
    done
}


/env bash
# =============================================================================
# PulseBox Installer
# Raspberry Pi OS Trixie (Debian 13) — interactive, menu-driven
#
# Usage:
#   bash install.sh              # interactive (recommended)
#   bash install.sh --all        # install everything non-interactively
#   bash install.sh --core-only  # core only, no prompts
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m';     RESET='\033[0m'

info()    { echo -e "${CYAN}  →${RESET} $*"; }
success() { echo -e "${GREEN}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}  ⚠${RESET} $*"; }
error()   { echo -e "${RED}  ✗${RESET} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}${CYAN}  $*${RESET}"; \
            echo -e "${BOLD}${CYAN}══════════════════════════════════════════${RESET}"; }
section() { echo -e "\n${BOLD}── $* ${RESET}"; }

# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------
INSTALL_ALL=false
CORE_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --all)       INSTALL_ALL=true ;;
        --core-only) CORE_ONLY=true   ;;
    esac
done



apt_install() {
    wait_for_apt
    info "apt install $*"
    # grep exit 1 (no match) must not trip set -o pipefail
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" 2>&1         | { grep -E "^(Setting up|Unpacking|E:|W:)" || true; }
}

pip_install() {
    info "pip install $*"
    pip install "$@" --break-system-packages --quiet
}



# ---------------------------------------------------------------------------
# Detect OS codename (e.g. trixie, bookworm) — never hardcode it
# ---------------------------------------------------------------------------
OS_CODENAME=$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-}" || true)
OS_CODENAME="${OS_CODENAME:-$(lsb_release -sc 2>/dev/null || echo unknown)}"

# ---------------------------------------------------------------------------
# Safe apt / pip wrappers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Detect install directory (where this script lives)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PULSEBOX_DIR="$SCRIPT_DIR"

# Current user (even when run with sudo)
PULSEBOX_USER="${SUDO_USER:-$(whoami)}"
PULSEBOX_HOME=$(eval echo "~$PULSEBOX_USER")

# Name PulseBox advertises in the AirPlay menu on iPhone/iPad/Mac
AIRPLAY_NAME="${AIRPLAY_NAME:-PulseBox}"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
header "PulseBox Installer"

echo ""
info "Install directory : $PULSEBOX_DIR"
info "Running as user   : $PULSEBOX_USER"
info "Home directory    : $PULSEBOX_HOME"
echo ""

if [[ "$EUID" -ne 0 ]]; then
    error "This script needs sudo to install system packages."
    echo  "  Run:  sudo bash install.sh"
    exit 1
fi

# Check OS
if ! grep -q "Debian\|Raspberry" /etc/os-release 2>/dev/null; then
    warn "Not confirmed Raspberry Pi OS / Debian — continuing anyway."
fi

# Check Python 3
if ! command -v python3 &>/dev/null; then
    error "python3 not found. Install it first: sudo apt install python3"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PYTHON_VER"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
    success "Python $PYTHON_VER — OK"
else
    warn "Python $PYTHON_VER detected — PulseBox requires 3.10+. Consider upgrading."
fi

echo ""
if ! $INSTALL_ALL && ! $CORE_ONLY; then
    echo -e "${BOLD}  Select what to install.${RESET}"
    echo -e "  Press Enter to accept the default shown in brackets."
    echo ""
fi

# =============================================================================
# SECTION 1 — Core (always installed)
# =============================================================================
header "1 / 8  Core install"

section "Checking dpkg integrity"
    # If a previous install was interrupted, dpkg may be in a broken state.
    # This must be fixed before any apt-get call will work.
    if ! sudo dpkg --audit 2>&1 | grep -q "^$\|^No packages"; then
        warn "dpkg has unconfigured packages — running dpkg --configure -a to repair..."
        sudo dpkg --configure -a || {
            error "dpkg --configure -a failed. Try running it manually:"
            error "  sudo dpkg --configure -a"
            exit 1
        }
        success "dpkg repaired"
    else
        # Even if audit is clean, run configure -a defensively — it's fast and idempotent
        sudo dpkg --configure -a 2>/dev/null || true
        success "dpkg OK"
    fi

section "System packages"

    # Remove any stale third-party sources that can block apt-get update.
    # deb-multimedia is incompatible with Trixie (wrong signing key) and
    # must be removed if a previous install attempt left it behind.
    for STALE_SOURCE in             /etc/apt/sources.list.d/deb-multimedia.list             /etc/apt/sources.list.d/deb-multimedia*.list; do
        if [[ -f "$STALE_SOURCE" ]]; then
            warn "Removing stale apt source: $STALE_SOURCE"
            sudo rm -f "$STALE_SOURCE"
        fi
    done
    # Also scrub any inline deb-multimedia lines from sources.list itself
    if grep -q "deb-multimedia" /etc/apt/sources.list 2>/dev/null; then
        warn "Removing deb-multimedia entry from /etc/apt/sources.list"
        sudo sed -i '/deb-multimedia/d' /etc/apt/sources.list
    fi

    wait_for_apt
    sudo apt-get update -qq
    apt_install vlc mpv python3-pil python3-pil.imagetk ffmpeg
    # GPIO libraries for the Rotary Encoder Volume plugin (Pi 5 uses lgpio).
    # Harmless on systems without an encoder — the plugin only loads them when
    # enabled — but they must be present for the plugin to work and, when
    # building the binary, for Nuitka to bundle them.
    apt_install python3-gpiozero python3-lgpio
    # DLNA/UPnP media server (used by the DLNA Media Server plugin). PulseBox
    # runs its own per-user minidlna instance, so disable the packaged system
    # service to free the DLNA port and avoid a conflict.
    apt_install minidlna
    sudo systemctl disable --now minidlna 2>/dev/null || true
    # UPnP/DLNA renderer — lets Android phones cast audio to PulseBox
    # (Chromecast itself is proprietary and can't be implemented).
    apt_install gmediarender

    # grim: used by the launcher's "framekeeper" to keep the Wayland compositor
    # presenting frames smoothly in a fullscreen kiosk (avoids sluggish
    # scrolling / half-drawn rows on wlroots + XWayland). Harmless if unused.
    apt_install grim

    # Optical-disc PLAYBACK prerequisites for the CD Player / DVD Player
    # plugins. Installed unconditionally (unlike the optional CD *ripping*
    # section below), since playing a disc is a core feature: cdparanoia reads
    # the audio-CD table of contents, eject handles the ⏏ button. VLC (above)
    # does the actual CD/DVD playback; libdvdcss for encrypted DVDs is still
    # set up in the DVD section further down.
    apt_install cdparanoia eject

    # Nuitka toolchain for the optional compiled build (build.sh). patchelf is
    # required for --standalone/--onefile; nuitka + helpers come from pip.
    apt_install patchelf
    if python3 -m nuitka --version >/dev/null 2>&1; then
        success "Nuitka already installed"
    else
        info "Installing Nuitka (for the optional compiled build)..."
        if pip_install nuitka ordered-set zstandard; then
            success "Nuitka installed"
        else
            warn "Could not install Nuitka — the app still runs from source."
            warn "For the compiled build, install it manually:"
            warn "  pip install nuitka ordered-set zstandard --break-system-packages"
        fi
    fi

section "Remove PulseAudio (conflicts with PipeWire)"
if dpkg -l pulseaudio &>/dev/null 2>&1; then
    info "PulseAudio found — removing"
    sudo apt-get remove -y --purge pulseaudio pulseaudio-module-bluetooth 2>/dev/null || true
    sudo apt-get autoremove -y 2>/dev/null || true
    success "PulseAudio removed"
else
    success "PulseAudio not installed — nothing to do"
fi

section "PipeWire audio stack"
# pipewire-pulse + pulseaudio-utils are both required:
#   pipewire-pulse  — PulseAudio compatibility socket (VLC routes through this)
#   pulseaudio-utils — provides pactl client tool; also completes the PipeWire
#                      PA compatibility layer. Without it VLC cannot find any
#                      audio output even if pipewire-pulse is installed.
# Note: pulseaudio-utils does NOT start the PulseAudio daemon on Trixie.
apt_install pipewire pipewire-pulse pipewire-alsa pipewire-audio \
            wireplumber pulseaudio-utils

# Ensure PipeWire services are enabled and running for the current user
systemctl --user enable pipewire pipewire-pulse wireplumber 2>/dev/null || true
systemctl --user start  pipewire pipewire-pulse wireplumber 2>/dev/null || true
sleep 2

# pactl requires an active desktop session — it will always fail during install.
# This is normal. PipeWire activates automatically on first desktop login.
if pactl info &>/dev/null 2>&1; then
    success "PipeWire PulseAudio socket active"
else
    info "PipeWire will activate on first desktop login — this is normal during install"
fi

section "Python packages"
pip_install python-vlc mutagen requests spotipy

# Verify VLC binding works
if python3 -c "import vlc" 2>/dev/null; then
    success "python-vlc OK"
else
    warn "python-vlc import failed — VLC may need reinstalling"
fi

success "Core install complete"

# =============================================================================
# HiFiBerry Digi HAT — audio configuration
# =============================================================================
section "Audio HAT configuration"
# Install a targeted sudoers rule so PulseBox can write /etc/asound.conf and
# /boot/firmware/config.txt without a password prompt from the GUI.
# This is scoped to cp only — not a blanket sudo grant.
SUDOERS_FILE="/etc/sudoers.d/pulsebox-audio"
# Use tee (always /usr/bin/tee on Trixie) for writing privileged files
# This avoids the cp path ambiguity (/bin vs /usr/bin) across OS versions
sudo tee "$SUDOERS_FILE" > /dev/null <<SUDOEOF
# PulseBox Audio Configurator — allow writing audio config files without password
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/tee /etc/asound.conf
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/tee /boot/firmware/config.txt
$PULSEBOX_USER ALL=(root) NOPASSWD: /bin/cp * /etc/asound.conf
$PULSEBOX_USER ALL=(root) NOPASSWD: /bin/cp * /boot/firmware/config.txt
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/cp * /etc/asound.conf
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/cp * /boot/firmware/config.txt
$PULSEBOX_USER ALL=(root) NOPASSWD: /sbin/reboot
$PULSEBOX_USER ALL=(root) NOPASSWD: /sbin/shutdown
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/sbin/reboot
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/sbin/shutdown
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/tee /etc/systemd/system/hifiberry-tx-source.service
$PULSEBOX_USER ALL=(root) NOPASSWD: /bin/cp * /etc/systemd/system/hifiberry-tx-source.service
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/cp * /etc/systemd/system/hifiberry-tx-source.service
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/systemctl daemon-reload
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/systemctl enable hifiberry-tx-source.service
$PULSEBOX_USER ALL=(root) NOPASSWD: /usr/bin/systemctl disable hifiberry-tx-source.service
SUDOEOF
sudo chmod 440 "$SUDOERS_FILE"
if sudo visudo -c -f "$SUDOERS_FILE" 2>/dev/null; then
    success "Sudoers rule written: $SUDOERS_FILE"
else
    warn "Sudoers rule syntax error — removing to be safe"
    sudo rm -f "$SUDOERS_FILE"
fi

# Audio HAT setup is handled inside PulseBox via Modules -> Audio Output Configurator.
# It detects your HAT overlay from /boot/overlays/, writes config.txt, /etc/asound.conf
# and the WirePlumber priority rule — all from within the app after first boot.
info "Audio HAT configuration is handled in PulseBox -> Modules -> Audio Output Configurator"
info "Launch PulseBox after install and configure your HAT from there."
info "It will write config.txt, /etc/asound.conf and WirePlumber settings automatically."
echo ""

# =============================================================================
# SECTION 2 — CD Ripping
# =============================================================================
header "2 / 8  CD Ripping (abcde)"

if ask "Install CD ripping support?" "y"; then
    section "CD tools"
    apt_install abcde cdparanoia cd-discid eject

    section "Audio codecs"
    ask "  Install FLAC (lossless — recommended)?" "y" && apt_install flac        || true
    ask "  Install MP3  (lame)?"                   "y" && apt_install lame        || true
    ask "  Install M4A  (fdkaac)?"                 "n" && apt_install fdkaac      || true

    success "CD ripping ready — configure in Modules → CD Ripper"
else
    info "Skipping CD ripping"
fi

# =============================================================================
# SECTION 3 — DVD Ripping
# =============================================================================
# Disable exit-on-error for this entire section — MakeMKV build tools
# return non-zero in many normal situations (already installed, no match, etc.)
# and we must not let them kill the installer.
set +euo pipefail

header "3 / 8  DVD Ripping (MakeMKV + HandBrakeCLI)"

if ask "Install DVD ripping support?" "y"; then

    # --- libdvdcss2 ----------------------------------------------------------
    section "libdvdcss2 (copy protection bypass)"

    # Pre-answer the libdvd-pkg debconf question before installing to prevent hang
    echo "libdvd-pkg libdvd-pkg/build boolean true" | sudo debconf-set-selections
    echo "libdvd-pkg libdvd-pkg/post-invoke_hook-install boolean true" | sudo debconf-set-selections

    apt_install libdvd-pkg

    echo ""
    echo -e "  ${BOLD}${YELLOW}NOTE: The next step downloads and compiles libdvdcss2 from source.${RESET}"
    echo -e "  ${YELLOW}This takes 2-5 minutes on a Raspberry Pi and produces a lot of output.${RESET}"
    echo -e "  ${YELLOW}It is NOT frozen -- please wait for the success message below.${RESET}"
    echo ""
    info "Running dpkg-reconfigure libdvd-pkg -- streaming output..."
    echo ""

    # Log everything to a file as well as the terminal
    DVDCSS_LOG=$(mktemp /tmp/libdvdcss_build_XXXXXX.log)
    info "Build log saved to: $DVDCSS_LOG"
    echo ""

    DVDCSS_OK=true
    sudo DEBIAN_FRONTEND=noninteractive dpkg-reconfigure libdvd-pkg 2>&1         | tee "$DVDCSS_LOG" || DVDCSS_OK=false

    echo ""
    if $DVDCSS_OK; then
        success "dpkg-reconfigure finished cleanly"
    else
        # Exit code 1 is common and often non-fatal -- check if the library landed
        if ldconfig -p 2>/dev/null | grep -q "libdvdcss"; then
            success "libdvdcss2 present (dpkg-reconfigure returned non-zero but library found -- non-fatal)"
            DVDCSS_OK=true
        else
            warn "dpkg-reconfigure failed and libdvdcss2 was not found in the library cache."
            warn "Full build log: $DVDCSS_LOG"
            warn "Retry manually:  sudo dpkg-reconfigure libdvd-pkg"
            warn "Or try:          sudo apt install libdvdcss2   (if a repo carries it)"
        fi
    fi

    # Refresh linker cache and confirm
    sudo ldconfig 2>/dev/null || true
    echo ""
    if ldconfig -p 2>/dev/null | grep -q "libdvdcss"; then
        LIB_PATH=$(ldconfig -p 2>/dev/null | grep "libdvdcss" | awk "{print \$NF}" | head -1)
        success "libdvdcss2 confirmed in linker cache: $LIB_PATH"
    else
        warn "libdvdcss2 not visible to ldconfig -- DVD decryption may not work."
        warn "Check the build log: $DVDCSS_LOG"
    fi


    # --- HandBrakeCLI --------------------------------------------------------
    section "HandBrakeCLI"
    apt_install handbrake-cli
    if command -v HandBrakeCLI &>/dev/null; then
        success "HandBrakeCLI $(HandBrakeCLI --version 2>&1 | head -1)"
    else
        warn "HandBrakeCLI not found in PATH after install"
    fi


    # --- MakeMKV -------------------------------------------------------------
    section "MakeMKV"
    echo ""
    echo -e "  ${BOLD}Choose MakeMKV install method:${RESET}"
    echo -e "   1) Build from source  ${GREEN}(recommended — installs to /usr/bin, ~10 min on Pi 5)${RESET}"
    echo -e "   2) Skip MakeMKV       (DVD ripping will not work)"
    echo ""
    info "Snap is NOT used — its sandbox blocks the SCSI commands MakeMKV"
    info "needs to read some DVD drives (HL-DT-ST GT-series and similar)."
    echo ""

    MAKEMKV_CHOICE="1"
    if ! $CORE_ONLY; then
        read -rp "$(echo -e "${YELLOW}  ?${RESET} Choice [1/2]: ")" MAKEMKV_CHOICE
        MAKEMKV_CHOICE="${MAKEMKV_CHOICE:-1}"
    fi
    if $INSTALL_ALL; then MAKEMKV_CHOICE="1"; fi

    if [[ "$MAKEMKV_CHOICE" == "1" ]]; then
        # ── Option 2: Build from source ───────────────────────────────────────
        section "MakeMKV — build from source (fallback)"
        apt_install build-essential pkg-config libc6-dev libssl-dev \
            libexpat1-dev libavcodec-dev libgl1-mesa-dev qtbase5-dev zlib1g-dev curl

        # MakeMKV moved the latest Linux tarball off the main /download/ path,
        # so the proven previous ("-1") release is fetched from /download/old/.
        # Probe candidates newest-first across both locations; update this list
        # as new releases land (keep the top entry one behind the newest).
        MAKEMKV_VER=""
        MAKEMKV_BASE=""
        MAKEMKV_CANDIDATES="1.18.3 1.18.2 1.18.1 1.18.0 1.17.9 1.17.8"

        info "Probing MakeMKV servers (old-releases page first)..."
        for CANDIDATE in $MAKEMKV_CANDIDATES; do
            for BASE in "https://www.makemkv.com/download/old" \
                        "https://www.makemkv.com/download"; do
                PROBE_URL="${BASE}/makemkv-oss-${CANDIDATE}.tar.gz"
                HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
                    --max-time 10 --head "$PROBE_URL" 2>/dev/null || echo "000")
                if [[ "$HTTP_CODE" == "200" ]]; then
                    MAKEMKV_VER="$CANDIDATE"
                    MAKEMKV_BASE="$BASE"
                    success "Found MakeMKV $MAKEMKV_VER at $BASE"
                    break 2
                fi
            done
            info "  $CANDIDATE — not found on either page"
        done

        if [[ -z "$MAKEMKV_VER" ]]; then
            warn "Could not auto-detect a valid MakeMKV version."
            warn "Check https://www.makemkv.com/download/ for the current release."
            ask_input "Enter MakeMKV version to build (e.g. 1.18.3)" ""
            MAKEMKV_VER="$REPLY"
            if [[ -z "$MAKEMKV_VER" ]]; then
                warn "No version entered — skipping MakeMKV."
                MAKEMKV_CHOICE="3"
            fi
        fi

        if [[ "$MAKEMKV_CHOICE" == "1" ]]; then
            info "Building MakeMKV $MAKEMKV_VER — this takes several minutes on a Pi..."
            BUILD_DIR=$(mktemp -d)
            info "Build directory: $BUILD_DIR"
            echo ""

            BUILD_OK=true
            pushd "$BUILD_DIR" > /dev/null

            # ── Helper: download and validate a tarball ───────────────────────
            download_and_check() {
                local PKG="$1"
                local VER="$2"
                local TARBALL="${PKG}.tar.gz"
                # Try the page the probe succeeded on first, then the other.
                local BASES="${MAKEMKV_BASE:-https://www.makemkv.com/download/old}"
                if [[ "$BASES" == *"/old" ]]; then
                    BASES="$BASES https://www.makemkv.com/download"
                else
                    BASES="$BASES https://www.makemkv.com/download/old"
                fi

                for BASE in $BASES; do
                    local URL="${BASE}/${PKG}-${VER}.tar.gz"
                    info "Downloading ${PKG}-${VER}.tar.gz from ${BASE} ..."
                    rm -f "$TARBALL"
                    HTTP_CODE=$(curl -L --progress-bar -w "%{http_code}" \
                        -o "$TARBALL" "$URL" 2>/dev/null || echo "000")
                    [[ "$HTTP_CODE" == "200" ]] || { rm -f "$TARBALL"; continue; }

                    FTYPE=$(file -b "$TARBALL" 2>/dev/null || echo "unknown")
                    case "$FTYPE" in
                        *gzip*|*tar*|*POSIX*) : ;;
                        *) rm -f "$TARBALL"; continue ;;
                    esac
                    tar tzf "$TARBALL" > /dev/null 2>&1 || { rm -f "$TARBALL"; continue; }

                    info "Extracting ${PKG} ..."
                    tar xzf "$TARBALL"
                    return 0
                done
                warn "Could not download ${PKG}-${VER} from /download or /download/old."
                warn "Check https://www.makemkv.com/download/old/ for the current file."
                return 1
            }

            # ── 1. makemkv-oss  (./configure && make && make install) ─────────
            if $BUILD_OK; then
                if download_and_check "makemkv-oss" "$MAKEMKV_VER"; then
                    cd "makemkv-oss-${MAKEMKV_VER}"
                    info "Configuring makemkv-oss ..."
                    if ! ./configure 2>&1 | tail -5; then
                        warn "makemkv-oss configure failed"
                        BUILD_OK=false
                    fi
                    if $BUILD_OK; then
                        info "Compiling makemkv-oss ($(nproc) cores — may take several minutes) ..."
                        if ! make -j"$(nproc)"; then
                            warn "makemkv-oss make failed"
                            BUILD_OK=false
                        fi
                    fi
                    if $BUILD_OK; then
                        info "Installing makemkv-oss ..."
                        sudo make install
                        success "makemkv-oss installed"
                    fi
                    cd ..
                else
                    BUILD_OK=false
                fi
            fi

            # ── 2. makemkv-bin  (make install DESTDIR=/ — no configure step) ──
            # makemkv-bin ships pre-built ARM/x86 binaries; it has no configure
            # script.  The Makefile copies them straight to the install prefix.
            if $BUILD_OK; then
                if download_and_check "makemkv-bin" "$MAKEMKV_VER"; then
                    cd "makemkv-bin-${MAKEMKV_VER}"
                    info "Installing makemkv-bin (pre-built binary — no compile step) ..."
                    # Accept the EULA non-interactively then install
                    if ! sudo make -f makefile.linux install; then
                        warn "makemkv-bin install failed"
                        BUILD_OK=false
                    else
                        success "makemkv-bin installed"
                    fi
                    cd ..
                else
                    BUILD_OK=false
                fi
            fi

            popd > /dev/null
            rm -rf "$BUILD_DIR"

            if $BUILD_OK && command -v makemkvcon &>/dev/null; then
                success "MakeMKV $(makemkvcon --version 2>&1 | head -1) built and installed"
            else
                warn "MakeMKV build did not complete — see output above."
                warn "Manual guide: https://www.makemkv.com/forum/viewtopic.php?t=224"
            fi
        fi
    fi

    if [[ "$MAKEMKV_CHOICE" == "2" || "$MAKEMKV_CHOICE" == "3" ]]; then
        warn "Skipping MakeMKV — DVD ripping will not work until it is installed."
        info "To install later, re-run:  sudo bash install.sh"
        info "Choose option 1 to build MakeMKV from source."
    fi

    # --- MakeMKV beta key (auto-fetch) ---------------------------------------
    # Detect makemkvcon at any of its possible install locations
    MAKEMKV_PRESENT=false
    for _mkv_path in /usr/local/bin/makemkvcon /usr/bin/makemkvcon; do
        if [[ -x "$_mkv_path" ]]; then
            MAKEMKV_PRESENT=true
            break
        fi
    done
    if $MAKEMKV_PRESENT; then

        MAKEMKV_CONF_DIR="$PULSEBOX_HOME/.MakeMKV"
        MAKEMKV_CONF_FILE="$MAKEMKV_CONF_DIR/settings.conf"
        MAKEMKV_KEY_API="https://cable.ayra.ch/makemkv/api/api.php?f=txt"
        MAKEMKV_KEY_SCRIPT="/usr/local/bin/makemkv_update_key.sh"

        echo ""
        info "Fetching current MakeMKV beta key..."

        # Known current key — used as fallback if all API sources fail
        # Valid until end of July 2026. Update this when it expires.
        KNOWN_KEY="T-BSaJ6gwgMx4eIggWkVYXiVP_6zehm7WAO9dEydvzOHFHoZ6YQ82BL5cGpYDxvyRWnS"

        # Source 1: cable.ayra.ch API (works from Pi — IP allowlisted, not cloud)
        MAKEMKV_KEY=$(curl -s --max-time 10             -A "Mozilla/5.0 (X11; Linux aarch64; rv:109.0) Gecko/20100101 Firefox/115.0"             "$MAKEMKV_KEY_API" 2>/dev/null             | grep -oE "T-[A-Za-z0-9_@-]{20,}" | head -1 || true)

        # Source 2: MakeMKV forum post (official source)
        if [[ -z "$MAKEMKV_KEY" ]]; then
            info "cable.ayra.ch did not return a key — trying MakeMKV forum..."
            MAKEMKV_KEY=$(curl -s --max-time 15                 -A "Mozilla/5.0 (X11; Linux aarch64; rv:109.0) Gecko/20100101 Firefox/115.0"                 "https://forum.makemkv.com/forum/viewtopic.php?t=1053" 2>/dev/null                 | grep -oE "T-[A-Za-z0-9_@-]{20,}" | head -1 || true)
        fi

        # Source 3: MakeMKV purchase page (also shows current key)
        if [[ -z "$MAKEMKV_KEY" ]]; then
            info "Forum not available — trying makemkv.com/buy..."
            MAKEMKV_KEY=$(curl -s --max-time 15                 -A "Mozilla/5.0 (X11; Linux aarch64; rv:109.0) Gecko/20100101 Firefox/115.0"                 "https://www.makemkv.com/buy/" 2>/dev/null                 | grep -oE "T-[A-Za-z0-9_@-]{20,}" | head -1 || true)
        fi

        # Fallback: use hardcoded known key
        if [[ -z "$MAKEMKV_KEY" ]]; then
            warn "Could not fetch key from any source — using bundled key."
            warn "This key is valid until end of July 2026."
            warn "If it has expired, update it at: https://forum.makemkv.com/forum/viewtopic.php?t=1053"
            MAKEMKV_KEY="$KNOWN_KEY"
        fi

        # Write the key
        sudo -u "$PULSEBOX_USER" mkdir -p "$MAKEMKV_CONF_DIR"
        printf "app_Key = \"%s\"\n" "$MAKEMKV_KEY" |             sudo -u "$PULSEBOX_USER" tee "$MAKEMKV_CONF_FILE" > /dev/null
        success "Beta key written to $MAKEMKV_CONF_FILE"
        info "Key: $MAKEMKV_KEY"

        # Also update the renewal script with the same key as seed
        info "Updating renewal script with current key..."

        # --- Install monthly auto-renewal script -----------------------------
        info "Installing monthly key renewal script at $MAKEMKV_KEY_SCRIPT ..."
        sudo tee "$MAKEMKV_KEY_SCRIPT" > /dev/null <<KEYEOF
#!/usr/bin/env bash
# Auto-updates the MakeMKV beta key from cable.ayra.ch
# Installed by PulseBox installer. Runs monthly via cron.
CONF_DIR="\$HOME/.MakeMKV"
CONF_FILE="\$CONF_DIR/settings.conf"
API_URL="https://cable.ayra.ch/makemkv/api/api.php?f=txt"
LOG="/var/log/makemkv_key_update.log"

mkdir -p "\$CONF_DIR"
KEY=\$(curl -s --max-time 15 -A "PulseBox-KeyUpdater/1.0" "\$API_URL" 2>/dev/null | grep -oE 'T-[A-Za-z0-9_@-]{20,}' | head -1)
if [[ -n "\$KEY" ]]; then
    echo "app_Key = \"\$KEY\"" > "\$CONF_FILE"
    echo "\$(date '+%Y-%m-%d %H:%M') Updated MakeMKV key: \$KEY" >> "\$LOG"
    echo "Key updated: \$KEY"
else
    echo "\$(date '+%Y-%m-%d %H:%M') Failed to fetch key" >> "\$LOG"
    echo "Failed to fetch key — will retry next run"
    exit 1
fi
KEYEOF
        sudo chmod +x "$MAKEMKV_KEY_SCRIPT"
        success "Key renewal script installed at $MAKEMKV_KEY_SCRIPT"

        # --- Add monthly cron job for the pi user ----------------------------
        CRON_JOB="0 9 1 * * $MAKEMKV_KEY_SCRIPT >> /var/log/makemkv_key_update.log 2>&1"
        EXISTING_CRON=$(sudo -u "$PULSEBOX_USER" crontab -l 2>/dev/null || true)
        if echo "$EXISTING_CRON" | grep -q "makemkv_update_key"; then
            info "Monthly cron job already present — skipping"
        else
            (echo "$EXISTING_CRON"; echo "$CRON_JOB") |                 sudo -u "$PULSEBOX_USER" crontab -
            success "Monthly key renewal cron job added (1st of each month at 09:00)"
        fi
    fi

    success "DVD ripping ready — configure in Modules -> DVD Ripper"
else
    info "Skipping DVD ripping"
fi

# =============================================================================
# SECTION 4 — TMDb API key
# =============================================================================
# Re-enable strict error handling after the MakeMKV section
set -euo pipefail

header "4 / 8  TMDb Poster Art"

if ask "Configure TMDb API key for online movie poster art?" "y"; then
    info "Get a free key at: https://www.themoviedb.org/settings/api"
    ask_input "  TMDb API key (leave blank to skip)" ""
    TMDB_KEY="$REPLY"
    if [[ -n "$TMDB_KEY" ]]; then
        CONFIG_FILE="$PULSEBOX_HOME/.pulsebox_config.json"
        if [[ -f "$CONFIG_FILE" ]]; then
            # Inject into existing config
            python3 - "$CONFIG_FILE" "$TMDB_KEY" << 'PYEOF'
import json, sys
path, key = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
cfg["tmdb_api_key"] = key
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("  Updated existing config")
PYEOF
        else
            # Write a minimal config containing just the key
            sudo -u "$PULSEBOX_USER" python3 - "$CONFIG_FILE" "$TMDB_KEY" << 'PYEOF'
import json, sys
path, key = sys.argv[1], sys.argv[2]
with open(path, "w") as f:
    json.dump({"tmdb_api_key": key}, f, indent=2)
PYEOF
        fi
        success "TMDb key saved to $CONFIG_FILE"
    else
        info "Skipped — enter key later in Modules → DVD Ripper"
    fi
else
    info "Skipping TMDb configuration"
fi

# =============================================================================
# SECTION 5 — Bluetooth
# =============================================================================
header "5 / 8  Bluetooth"

if ask "Install Bluetooth support?" "y"; then
    apt_install bluetooth bluez
    sudo systemctl enable bluetooth
    sudo systemctl start bluetooth
    success "Bluetooth enabled — configure in Modules → Bluetooth Manager"
else
    info "Skipping Bluetooth"
fi

# =============================================================================
# SECTION 6 — Web Remote
# =============================================================================
header "6 / 8  Web Remote"

# No packages needed — uses Python stdlib
info "Web Remote uses Python's built-in http.server — no packages needed."
info "It starts automatically when the module loads."
info "Default port: 8080   Access: http://<pi-ip>:8080"
success "Web Remote ready — configure port in Modules → Web Remote"

# =============================================================================
# SECTION 7 — Spotify Connect (librespot / Raspotify)
# =============================================================================
header "7 / 8  Spotify Connect (personal use only)"

warn "librespot reverse-engineers Spotify's internal protocol."
warn "This is against Spotify's Developer Terms of Service."
warn "For personal, non-commercial use only. Requires Spotify Premium."
echo ""

if ask "Install Spotify Connect (Raspotify + librespot)?" "n"; then
    section "Raspotify"
    apt_install curl
    info "Running Raspotify installer…"
    curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
    success "Raspotify installed"

    section "spotipy (Now-Playing metadata — optional)"
    if ask "  Install spotipy for track metadata / album art?" "y"; then
        pip_install spotipy
        success "spotipy installed"
        echo ""
        info "To enable metadata:"
        info "  1. https://developer.spotify.com/dashboard → Create App → Web API"
        info "  2. Add redirect URI: http://localhost:8765/callback"
        info "  3. Modules → Spotify → Connect tab → paste Client ID → Start Polling"
    fi

    success "Spotify Connect ready — configure in Modules → Spotify"
else
    info "Skipping Spotify Connect"
fi

# =============================================================================
# SECTION 8 — Autostart
# =============================================================================
header "8 / 8  Autostart"

echo ""
echo -e "  ${BOLD}Choose autostart method:${RESET}"
echo -e "   1) LXDE autostart  (starts with the desktop — simplest)"
echo -e "   2) systemd service (starts with the system — more robust)"
echo -e "   3) Skip autostart"
echo ""

AUTOSTART_CHOICE="3"
if ! $CORE_ONLY && ! $INSTALL_ALL; then
    read -rp "$(echo -e "${YELLOW}  ?${RESET} Choice [1/2/3]: ")" AUTOSTART_CHOICE
    AUTOSTART_CHOICE="${AUTOSTART_CHOICE:-3}"
fi
if $INSTALL_ALL; then AUTOSTART_CHOICE="2"; fi

case "$AUTOSTART_CHOICE" in
    1)
        LXDE_DIR="/etc/xdg/lxsession/LXDE-pi"
        AUTOSTART_FILE="$LXDE_DIR/autostart"
        sudo mkdir -p "$LXDE_DIR"
        # Launch whichever is newer — compiled build or Python source.
        AUTOSTART_LINE="@bash $PULSEBOX_DIR/scripts/pulsebox_launcher.sh"
        # Remove any old direct-python entry so we don't double-launch.
        sudo sed -i "\|@python3 $PULSEBOX_DIR/pulsebox.py|d" \
            "$AUTOSTART_FILE" 2>/dev/null || true
        if grep -qF "$AUTOSTART_LINE" "$AUTOSTART_FILE" 2>/dev/null; then
            info "LXDE autostart entry already present"
        else
            echo "$AUTOSTART_LINE" | sudo tee -a "$AUTOSTART_FILE" > /dev/null
            success "Added to $AUTOSTART_FILE"
        fi
        ;;
    2)
        SERVICE_FILE="/etc/systemd/system/pulsebox.service"
        sudo tee "$SERVICE_FILE" > /dev/null << SVCEOF
[Unit]
Description=PulseBox Jukebox
After=graphical.target

[Service]
User=$PULSEBOX_USER
Environment=DISPLAY=:0
WorkingDirectory=$PULSEBOX_DIR
# Launcher starts the compiled build if it's newer than the source, else the
# Python source. PULSEBOX_DIR_SRC pins where the source lives.
Environment=PULSEBOX_DIR_SRC=$PULSEBOX_DIR
ExecStart=/usr/bin/env bash $PULSEBOX_DIR/scripts/pulsebox_launcher.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
SVCEOF
        sudo systemctl daemon-reload
        sudo systemctl enable pulsebox
        success "systemd service installed: $SERVICE_FILE"
        info "Start now with: sudo systemctl start pulsebox"
        ;;
    *)
        info "Skipping autostart — run manually with: python3 $PULSEBOX_DIR/pulsebox.py"
        ;;
esac

# =============================================================================
# Kiosk display optimisation (labwc / Wayland)
# =============================================================================
# On Raspberry Pi OS the desktop wallpaper (pcmanfm) and taskbar (wf-panel-pi)
# keep compositing *behind* a fullscreen app, which makes a kiosk noticeably
# slower than the same app in a plain window. A dedicated PulseBox appliance
# doesn't need them. A user labwc autostart is MERGED with the system one (it
# doesn't replace it), so we can't just omit those lines — we start what we
# need and kill the desktop/panel a few seconds after they spawn.
if [[ "$AUTOSTART_CHOICE" != "3" ]] && command -v labwc >/dev/null 2>&1; then
    section "Kiosk display optimisation (labwc)"
    LABWC_DIR="$PULSEBOX_HOME/.config/labwc"
    LABWC_AUTOSTART="$LABWC_DIR/autostart"
    sudo -u "$PULSEBOX_USER" mkdir -p "$LABWC_DIR"
    if [[ -f "$LABWC_AUTOSTART" ]] && \
       ! grep -q "PulseBox kiosk" "$LABWC_AUTOSTART" 2>/dev/null; then
        sudo -u "$PULSEBOX_USER" cp "$LABWC_AUTOSTART" \
            "$LABWC_AUTOSTART.bak.$(date +%s)" 2>/dev/null || true
        info "Backed up existing labwc autostart"
    fi
    sudo -u "$PULSEBOX_USER" tee "$LABWC_AUTOSTART" >/dev/null <<'LWEOF'
# PulseBox kiosk — drop the desktop wallpaper and panel so they don't composite
# behind the fullscreen app and slow it down. Delete this file to restore the
# normal Raspberry Pi desktop.
/usr/bin/kanshi &
( sleep 3
  pkill -f "lwrespawn.*wf-panel-pi"; pkill -f "lwrespawn.*pcmanfm-pi"
  pkill -f wf-panel-pi; pkill -f pcmanfm
) &
/usr/bin/lxsession-xdg-autostart
LWEOF
    success "Installed labwc kiosk autostart ($LABWC_AUTOSTART)"
    info "  Desktop wallpaper + panel are dropped for a faster kiosk."
    info "  Restore the normal desktop any time with:"
    info "    rm $LABWC_AUTOSTART"
fi

# =============================================================================
# Verify PipeWire is running
# =============================================================================
section "Audio verification"
if command -v pactl &>/dev/null; then
    SINKS=$(pactl list sinks short 2>/dev/null | wc -l)
    if [[ "$SINKS" -gt 0 ]]; then
        success "PipeWire running — $SINKS sink(s) detected"
        pactl list sinks short 2>/dev/null | while read -r line; do
            info "  $line"
        done
    else
        warn "No PipeWire sinks detected — check audio setup"
    fi
else
    warn "pactl not found — cannot verify PipeWire"
fi

# =============================================================================
# Quick tool check
# =============================================================================
section "Tool availability summary"

check_tool() {
    local name="$1"
    local cmd="${2:-$1}"
    if command -v "$cmd" &>/dev/null; then
        success "$name"
    else
        warn "$name — NOT FOUND"
    fi
}

check_tool "python3"
check_tool "python-vlc"       "python3 -c 'import vlc'"   || true
python3 -c "import vlc"  2>/dev/null && success "python-vlc (importable)" \
                                     || warn    "python-vlc (import failed)"
check_tool "vlc"
check_tool "mpv"
check_tool "ffmpeg"
check_tool "abcde"          "abcde"
check_tool "HandBrakeCLI"   "HandBrakeCLI"
check_tool "makemkvcon"   "makemkvcon"
check_tool "bluetoothctl"   "bluetoothctl"
check_tool "raspotify"      "systemctl is-enabled raspotify" || true
systemctl is-enabled raspotify &>/dev/null 2>&1 \
    && success "raspotify (service enabled)" \
    || info    "raspotify (not installed)"

# =============================================================================
# Create required directories
# =============================================================================
section "Creating required directories"

sudo -u "$PULSEBOX_USER" mkdir -p "$PULSEBOX_DIR/assets"
success "assets/ created"

# --- Scoped passwordless sudo for the few service commands PulseBox runs -----
# The DLNA plugin frees the minidlna port and the AirPlay plugin starts/stops
# shairport-sync via `sudo -n systemctl ...`. On a password-protected sudo
# those silently fail. Grant ONLY those exact commands passwordless — not
# blanket sudo — so the plugins can manage their services cleanly.
section "Service-control permissions"
SUDOERS_FILE="/etc/sudoers.d/pulsebox"
SYSTEMCTL="$(command -v systemctl || echo /usr/bin/systemctl)"
sudo tee "$SUDOERS_FILE" >/dev/null <<EOF
# Installed by PulseBox — scoped, passwordless service control only.
$PULSEBOX_USER ALL=(root) NOPASSWD: $SYSTEMCTL disable --now minidlna, $SYSTEMCTL stop minidlna, $SYSTEMCTL start shairport-sync, $SYSTEMCTL stop shairport-sync, $SYSTEMCTL restart shairport-sync, $SYSTEMCTL start roonbridge, $SYSTEMCTL stop roonbridge, $SYSTEMCTL restart roonbridge
EOF
sudo chmod 0440 "$SUDOERS_FILE"
if sudo visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
    success "Installed scoped sudo rule ($SUDOERS_FILE)"
else
    warn "sudoers rule failed validation — removing it to be safe"
    sudo rm -f "$SUDOERS_FILE"
fi

# =============================================================================
# AirPlay 2 receiver (nqptp + Shairport Sync)
# =============================================================================
# AirPlay 2 isn't available as an apt package — Shairport Sync and its timing
# daemon nqptp must be compiled from source. The helper does the whole job
# (build, config with the metadata pipe, D-Bus policy, systemd service).
# A failure here must not abort the rest of the install, so it's tolerant.
section "AirPlay 2 receiver"

AIRPLAY_SCRIPT="$PULSEBOX_DIR/scripts/install_airplay.sh"
if [[ ! -f "$AIRPLAY_SCRIPT" ]]; then
    warn "scripts/install_airplay.sh not found — skipping AirPlay 2 setup"
elif command -v shairport-sync >/dev/null 2>&1 && \
     shairport-sync -V 2>/dev/null | grep -qi "airplay2"; then
    success "Shairport Sync with AirPlay 2 already installed — skipping build"
    info "  $(shairport-sync -V 2>/dev/null)"
else
    info "Building nqptp + Shairport Sync from source (takes a minute)..."
    if bash "$AIRPLAY_SCRIPT" "$AIRPLAY_NAME"; then
        success "AirPlay 2 receiver installed"
    else
        warn "AirPlay 2 setup did not complete."
        warn "Everything else is installed; re-run it later with:"
        warn "    bash $AIRPLAY_SCRIPT"
    fi
fi

# Pre-seed country flags into the radio cache, if the bundle ships them.
# After an initial run the flags live in ~/.pulsebox_radio_cache as
# flag_<cc>.png. Drop those files into assets/flags/ before building/installing
# and this step copies them in, so the Radio page shows flags immediately
# without downloading them on first use. Nothing happens if the folder is absent.
RADIO_CACHE="$PULSEBOX_HOME/.pulsebox_radio_cache"
if [[ -d "$PULSEBOX_DIR/assets/flags" ]] && \
   compgen -G "$PULSEBOX_DIR/assets/flags/flag_*.png" >/dev/null 2>&1; then
    sudo -u "$PULSEBOX_USER" mkdir -p "$RADIO_CACHE"
    sudo -u "$PULSEBOX_USER" cp "$PULSEBOX_DIR/assets/flags/"flag_*.png "$RADIO_CACHE/" 2>/dev/null || true
    FLAG_COUNT=$(find "$PULSEBOX_DIR/assets/flags" -name 'flag_*.png' | wc -l)
    success "Seeded $FLAG_COUNT country flags into the radio cache"
else
    info "No bundled flags to seed (they'll download on first use)"
fi

# =============================================================================
# Optional: compile to a standalone binary (Nuitka)
# =============================================================================
# PulseBox runs fine from Python source; compiling just makes it start faster.
# Only offered on an interactive install — an unattended "--all" run skips it
# so it doesn't silently add 10-20 minutes. Failure-isolated: a bad build never
# aborts the installer, and the app still runs from source.
if ! $INSTALL_ALL && ! $CORE_ONLY; then
    header "Optional: compile to a faster binary"
    echo -e "  PulseBox runs fine from Python source. Compiling with Nuitka"
    echo -e "  produces a standalone binary that starts faster. It takes"
    echo -e "  roughly 10-20 minutes on a Pi 5. You can also do it any time"
    echo -e "  later with:  bash $PULSEBOX_DIR/build.sh"
    echo ""
    if ask "Compile the binary now?" "n"; then
        section "Compiling with Nuitka — this will take a while…"
        COMPILE_OK=true
        # Build as the PulseBox user (not root) so dist/ is user-owned and the
        # source path resolves correctly. build.sh self-installs Nuitka if
        # needed (already present from the deps step above).
        sudo -u "$PULSEBOX_USER" bash "$PULSEBOX_DIR/build.sh" || COMPILE_OK=false
        if $COMPILE_OK && [[ -x "$PULSEBOX_DIR/dist/pulsebox" ]]; then
            success "Compiled binary built: $PULSEBOX_DIR/dist/pulsebox"
            info "The launcher runs the compiled build automatically when present."
        else
            warn "Compile did not complete — PulseBox still runs from source."
            warn "Retry any time with:  bash $PULSEBOX_DIR/build.sh"
        fi
    else
        info "Skipping compile — PulseBox will run from Python source."
    fi
fi

# =============================================================================
# Done
# =============================================================================
header "Installation complete"

echo ""
success "PulseBox is ready to run."
echo ""
echo -e "  ${BOLD}Start PulseBox:${RESET}"
echo -e "  cd $PULSEBOX_DIR"
echo -e "  python3 pulsebox.py"
echo ""
echo -e "  ${BOLD}Web Remote:${RESET}  http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo -e "  ${BOLD}Cast to PulseBox:${RESET}"
echo -e "  AirPlay 2 — pick '$AIRPLAY_NAME' from the AirPlay menu on your"
echo -e "              iPhone, iPad or Mac."
echo -e "  Android   — use BubbleUPnP / Hi-Fi Cast / VLC and pick 'PulseBox'."
echo -e "  Enable each under Plugins in PulseBox."
echo ""
if [[ "$AUTOSTART_CHOICE" == "2" ]]; then
    echo -e "  ${BOLD}Start service:${RESET}  sudo systemctl start pulsebox"
    echo ""
fi
echo -e "  ${BOLD}README:${RESET}  $PULSEBOX_DIR/README.md"
echo ""
