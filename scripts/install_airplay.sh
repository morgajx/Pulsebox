#!/usr/bin/env bash
#
# PulseBox — AirPlay 2 setup helper
# =================================
# Builds and installs nqptp + Shairport Sync with AirPlay 2 and metadata, then
# configures Shairport Sync to write the metadata pipe that the PulseBox
# "AirPlay 2 Receiver" plugin reads. (Transport controls additionally need the
# optional D-Bus interface — see the note by the ./configure line.)
#
# AirPlay 2 is NOT available as an apt package — it must be compiled from
# source. This takes a while on a Pi (the autoreconf/make steps especially).
#
# Usage:   bash scripts/install_airplay.sh
#
set -euo pipefail

# --- who will Shairport Sync run as -----------------------------------------
# Run it as the same user that runs PulseBox so the metadata pipe is readable
# and PipeWire audio output works within that user's session.
RUN_USER="${SUDO_USER:-$USER}"
PIPE="/tmp/shairport-sync-metadata"
NAME="${1:-PulseBox}"

echo "=================================================================="
echo " PulseBox AirPlay 2 setup"
echo "   Shairport Sync will run as: $RUN_USER"
echo "   AirPlay name:               $NAME"
echo "   Metadata pipe:              $PIPE"
echo "=================================================================="
echo ""

# --- build dependencies ------------------------------------------------------
echo "→ Installing build dependencies…"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git build-essential autoconf automake libtool pkg-config \
    libdaemon-dev libpopt-dev libconfig-dev \
    libasound2-dev avahi-daemon libavahi-client-dev \
    libssl-dev libsoxr-dev libplist-dev libsodium-dev \
    libgcrypt20-dev uuid-dev \
    libavutil-dev libavcodec-dev libavformat-dev \
    xxd xmltoman libglib2.0-dev

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# --- nqptp (AirPlay 2 timing daemon) ----------------------------------------
echo ""
echo "→ Building nqptp (AirPlay 2 timing daemon)…"
git clone --depth 1 https://github.com/mikebrady/nqptp.git
cd nqptp
autoreconf -fi
./configure --with-systemd-startup
make -j"$(nproc)"
sudo make install
sudo systemctl enable --now nqptp
cd "$WORK"

# --- Shairport Sync ----------------------------------------------------------
echo ""
echo "→ Building Shairport Sync with AirPlay 2 + metadata + D-Bus…"
git clone --depth 1 https://github.com/mikebrady/shairport-sync.git
cd shairport-sync
autoreconf -fi
# Known-working AirPlay 2 build (ALSA output). Do NOT add --with-pw here — it
# isn't a valid option on the current source and breaks the build.
# --with-dbus-interface enables the RemoteControl interface that the PulseBox
# overlay's transport buttons (play/pause/next/prev) drive.
./configure --sysconfdir=/etc \
    --with-alsa \
    --with-avahi \
    --with-ssl=openssl \
    --with-systemd \
    --with-airplay-2 \
    --with-metadata \
    --with-dbus-interface \
    --with-soxr

make -j"$(nproc)"
sudo make install

# Verify the built binary actually reports AirPlay 2.
if /usr/local/bin/shairport-sync -V 2>/dev/null | grep -qi "airplay2"; then
    echo "   ✓ AirPlay 2 build confirmed:"
    /usr/local/bin/shairport-sync -V
else
    echo "   !! WARNING: the built shairport-sync does NOT report AirPlay 2."
    echo "      Check the ./configure output above for '--with-airplay-2'."
fi
cd "$WORK"

# --- configuration -----------------------------------------------------------
echo ""
echo "→ Writing /etc/shairport-sync.conf…"

# Target the same HiFiBerry/DAC card PulseBox uses, if we can find one.
ALSA_DEV="$(aplay -l 2>/dev/null | awk -F'[ :]+' '
  /hifiberry|iqaudio|dacplus|audioinjector|allo|justboom/ && !/hdmi|vc4|bcm2835/ {print $3; exit}')"
ALSA_BLOCK=""
if [[ -n "$ALSA_DEV" ]]; then
    ALSA_BLOCK="
alsa = {
    output_device = \"plughw:CARD=$ALSA_DEV\";
};"
    echo "   Using ALSA device: plughw:CARD=$ALSA_DEV"
fi

sudo tee /etc/shairport-sync.conf >/dev/null <<EOF
general = {
    name = "$NAME";
    output_backend = "alsa";
    // Play AirPlay at full scale and let PulseBox's hardware volume slider be
    // the single master control — otherwise the sender's volume attenuates the
    // stream *before* the DAC mixer, stacking two attenuations and making it
    // very quiet. Set to "no" if you'd rather the Apple device's slider work.
    ignore_volume_control = "yes";
};
$ALSA_BLOCK

metadata = {
    enabled = "yes";
    include_cover_art = "yes";
    pipe_name = "$PIPE";
    pipe_timeout = 5000;
};

sessioncontrol = {
    allow_session_interruption = "yes";
};
EOF

# --- D-Bus policy for the RemoteControl interface ---------------------------
# Shairport Sync runs as "$RUN_USER", so it needs permission to own the
# org.gnome.ShairportSync name on the system bus — otherwise it logs
# "could not acquire a Shairport Sync native D-Bus interface" and the
# transport controls have nothing to talk to.
echo "→ Installing D-Bus policy for the RemoteControl interface…"
sudo tee /etc/dbus-1/system.d/shairport-sync.conf >/dev/null <<EOF
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="$RUN_USER">
    <allow own="org.gnome.ShairportSync"/>
    <allow send_destination="org.gnome.ShairportSync"/>
  </policy>
  <policy context="default">
    <allow send_destination="org.gnome.ShairportSync"/>
    <allow receive_sender="org.gnome.ShairportSync"/>
  </policy>
</busconfig>
EOF
sudo systemctl reload dbus 2>/dev/null || sudo systemctl restart dbus 2>/dev/null || true

# --- systemd service (runs as the PulseBox user) ----------------------------
echo "→ Installing shairport-sync.service (runs as $RUN_USER)…"
sudo tee /etc/systemd/system/shairport-sync.service >/dev/null <<EOF
[Unit]
Description=Shairport Sync - AirPlay 2 Audio Receiver
Requires=avahi-daemon.service nqptp.service
After=avahi-daemon.service nqptp.service network-online.target sound.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/shairport-sync
User=$RUN_USER
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now shairport-sync

echo ""
echo "=================================================================="
echo " Done. Checking services:"
sudo systemctl --no-pager --lines=0 status nqptp || true
sudo systemctl --no-pager --lines=0 status shairport-sync || true
echo ""
echo " Next:"
echo "   1. In PulseBox, open Plugins → AirPlay 2 Receiver and enable it."
echo "   2. On your iPhone/iPad/Mac, pick '$NAME' from the AirPlay menu."
echo ""
echo " If you have no sound while streaming, another app may hold the sound"
echo " card. PulseBox pauses local playback on AirPlay start; if you use a"
echo " specific HiFiBerry device you can pin it by adding an alsa block to"
echo " /etc/shairport-sync.conf, e.g.:"
echo '     alsa = { output_device = "hw:CARD=sndrpihifiberry"; };'
echo " then:  sudo systemctl restart shairport-sync"
echo "=================================================================="
