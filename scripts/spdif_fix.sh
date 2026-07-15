#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# PulseBox S/PDIF transmit fix — installer fragment.
#
# Forces a HiFiBerry Digi (wm8804) to transmit the Pi's own audio ('Tx Source'
# = 'AIF') instead of passing through its silent S/PDIF input, and saves the
# ALSA state so it survives a clean boot.
#
# Card-agnostic and idempotent: if no card on this Pi exposes a 'Tx Source'
# control (USB DAC, I2S DAC HAT, HDMI, no HAT...), it detects nothing and exits
# 0 without touching anything. Safe to run in any build, including one whose
# sound card you don't know in advance.
#
# Paste this into your main install script, or run it standalone with sudo:
#     sudo bash scripts/spdif_fix.sh
# ---------------------------------------------------------------------------
set -u

CONTROL="Tx Source"
TARGET="AIF"

find_card() {
  # Print the name of the first card exposing the 'Tx Source' control.
  while read -r _idx name; do
    [ -z "${name:-}" ] && continue
    if amixer -c "$name" scontrols 2>/dev/null | grep -q "$CONTROL"; then
      printf '%s\n' "$name"
      return 0
    fi
  done < <(awk -F'[][]' '/\[/{print $1, $2}' /proc/asound/cards 2>/dev/null)
  return 1
}

if ! command -v amixer >/dev/null 2>&1; then
  echo "[spdif] amixer not found (install alsa-utils). Skipping." >&2
  exit 0
fi

CARD="$(find_card || true)"
if [ -z "${CARD:-}" ]; then
  echo "[spdif] No 'Tx Source' control on any card — not a HiFiBerry Digi. Nothing to do."
  exit 0
fi

echo "[spdif] HiFiBerry Digi found on card '$CARD' — setting '$CONTROL' = '$TARGET'."
amixer -c "$CARD" sset "$CONTROL" "$TARGET" >/dev/null

# Persist so a clean reboot keeps it. (Runtime re-assert in the app covers the
# projector / HDMI re-probe case that alsactl alone can't.)
if command -v alsactl >/dev/null 2>&1; then
  alsactl store "$CARD" 2>/dev/null || alsactl store 2>/dev/null || true
  echo "[spdif] ALSA state saved."
fi

echo "[spdif] Done. PulseBox re-asserts AIF at startup and before each track too."
