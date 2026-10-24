#!/usr/bin/env bash
#
# install_roonbridge.sh — install Roon's official, free RoonBridge endpoint.
#
# RoonBridge turns this Pi into a Roon endpoint (networked audio zone). It is
# Roon Labs' own binary; this script just runs their official installer for
# 64-bit ARM (Raspberry Pi OS on Pi 4/5). You still need a Roon Core on your
# LAN and a Roon subscription.
#
set -euo pipefail

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64) RB_ARCH="linuxarmv8" ;;
    armv7l|armv6l) RB_ARCH="linuxarmv7hf" ;;
    x86_64)        RB_ARCH="linuxx64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

INSTALLER="roonbridge-installer-${RB_ARCH}.sh"
URL="https://download.roonlabs.com/builds/${INSTALLER}"

echo "→ Architecture: $ARCH  ->  $RB_ARCH"
echo "→ Downloading RoonBridge installer from Roon Labs..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"

if ! curl -fL --retry 3 -o "$INSTALLER" "$URL"; then
    echo "Download failed. Check https://roonlabs.com/downloads for the" >&2
    echo "current RoonBridge installer for $RB_ARCH." >&2
    exit 1
fi

chmod +x "$INSTALLER"
echo "→ Running Roon's installer (installs the 'roonbridge' service)..."
sudo "./$INSTALLER"

echo ""
echo "✓ RoonBridge installed."
echo "  It runs as the 'roonbridge' service and starts at boot."
echo "  In Roon: Settings > Audio — your Pi appears there; click Enable."
echo "  Audio plays through the HiFiBerry."
