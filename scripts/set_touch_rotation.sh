#!/usr/bin/env bash
# =============================================================================
# set_touch_rotation.sh — match the touchscreen to a rotated display.
#
# Usage:  set_touch_rotation.sh <0|90|180|270>
#
# A compositor output transform (kanshi/wlr-randr) rotates the *display* but not
# touch input, because touch mapping is a libinput concern. This writes a udev
# rule setting LIBINPUT_CALIBRATION_MATRIX on the touchscreen so touch follows
# the screen. The calibration matrix uses the same angle as the display.
#
# Must run as root (writes /etc/udev/rules.d). PulseBox calls it via a scoped
# NOPASSWD sudoers entry; it also works stand-alone with `sudo`.
#
# Touch applies after the device re-initialises — reboot (or re-plug) to apply.
# =============================================================================
set -u

DEG="${1:-0}"
RULE="/etc/udev/rules.d/99-pulsebox-touch-rotate.rules"

# Display rotation -> touch calibration matrix (same angle as the display;
# 90 deg confirmed on a Goodix DSI panel).
case "$DEG" in
    0)   MATRIX="1 0 0 0 1 0" ;;
    90)  MATRIX="0 -1 1 1 0 0" ;;
    180) MATRIX="-1 0 1 0 -1 1" ;;
    270) MATRIX="0 1 0 -1 0 1" ;;
    *)   echo "usage: $0 <0|90|180|270>" >&2; exit 2 ;;
esac

if [ "$(id -u)" != "0" ]; then
    echo "must run as root (writes $RULE)" >&2
    exit 1
fi

# Find the touchscreen's device name from sysfs.
NAME=""
for f in /sys/class/input/event*/device/name; do
    [ -r "$f" ] || continue
    n="$(cat "$f" 2>/dev/null)" || continue
    case "$n" in
        *[Tt]ouch*|*Goodix*|*ft5x06*|*EP0110*|*FocalTech*|*Capacitive*)
            NAME="$n"; break ;;
    esac
done

if [ -z "$NAME" ]; then
    echo "no touchscreen found in /sys/class/input" >&2
    exit 1
fi

# Strip a leading i2c "bus-addr " prefix (e.g. "11-005d ") so the match stays
# stable across kernels, and glob the remainder.
SHORT="$(printf '%s' "$NAME" | sed -E 's/^[0-9]+-[0-9a-fA-F]+ //')"

printf 'SUBSYSTEM=="input", ATTRS{name}=="*%s", ENV{LIBINPUT_CALIBRATION_MATRIX}="%s"\n' \
    "$SHORT" "$MATRIX" > "$RULE"

udevadm control --reload-rules
udevadm trigger >/dev/null 2>&1 || true

echo "touch calibration for ${DEG}° written to $RULE (device: $NAME)"
echo "reboot to apply."
