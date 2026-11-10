#!/usr/bin/env bash
# =============================================================================
# mount_smb.sh — mount / unmount a SMB (CIFS) network share for PulseBox.
#
#   mount_smb.sh mount   <//server/share> <mountpoint> <credfile> [persist] [vers]
#   mount_smb.sh unmount <mountpoint> [forget]
#
# Mounts with the invoking user's uid/gid (so the library is readable) using a
# root-only credentials file, never a password on the command line. With
# `persist`, an idempotent /etc/fstab entry is written so the share remounts at
# boot (x-systemd.automount + nofail + _netdev). `forget` on unmount removes it.
#
# Needs cifs-utils. Must run as root (mount + fstab) — PulseBox calls it through
# a scoped NOPASSWD sudoers entry; it also works stand-alone with `sudo`.
# =============================================================================
set -u

FSTAB="/etc/fstab"
MARK="# pulsebox-smb"

if [ "$(id -u)" != "0" ]; then
    echo "must run as root" >&2
    exit 1
fi

# uid/gid of the user who invoked sudo (so files are owned by them, not root).
U="${SUDO_UID:-1000}"
G="${SUDO_GID:-1000}"

_fstab_remove() {   # drop any existing pulsebox line for this mountpoint
    local mp="$1"
    [ -f "$FSTAB" ] || return 0
    local esc; esc="$(printf '%s' "$mp" | sed 's/[\/&]/\\&/g')"
    sed -i "\|[[:space:]]${esc}[[:space:]].*${MARK}\$|d" "$FSTAB"
}

action="${1:-}"
case "$action" in
  mount)
    SHARE="${2:-}"; MP="${3:-}"; CRED="${4:-}"; PERSIST="${5:-}"; VERS="${6:-}"
    [ -z "$SHARE" ] || [ -z "$MP" ] || [ -z "$CRED" ] && { echo "usage: mount <share> <mountpoint> <credfile> [persist] [vers]" >&2; exit 2; }
    command -v mount.cifs >/dev/null 2>&1 || { echo "cifs-utils not installed (sudo apt install cifs-utils)" >&2; exit 3; }
    [ -r "$CRED" ] || { echo "credentials file not readable: $CRED" >&2; exit 4; }

    mkdir -p "$MP"
    OPTS="credentials=${CRED},uid=${U},gid=${G},iocharset=utf8,file_mode=0664,dir_mode=0775,nofail"
    [ -n "$VERS" ] && OPTS="${OPTS},vers=${VERS}"

    if mountpoint -q "$MP"; then
        umount "$MP" 2>/dev/null || true
    fi
    if ! mount -t cifs "$SHARE" "$MP" -o "$OPTS" 2>/tmp/pb_smb_err; then
        echo "mount failed: $(cat /tmp/pb_smb_err)" >&2
        rm -f /tmp/pb_smb_err
        exit 5
    fi
    rm -f /tmp/pb_smb_err

    if [ "$PERSIST" = "persist" ]; then
        _fstab_remove "$MP"
        printf '%s %s cifs %s,_netdev,x-systemd.automount 0 0  %s\n' \
            "$SHARE" "$MP" "$OPTS" "$MARK" >> "$FSTAB"
        systemctl daemon-reload 2>/dev/null || true
    fi
    echo "mounted $SHARE at $MP"
    ;;

  unmount)
    MP="${2:-}"; FORGET="${3:-}"
    [ -z "$MP" ] && { echo "usage: unmount <mountpoint> [forget]" >&2; exit 2; }
    if mountpoint -q "$MP"; then
        umount "$MP" 2>/tmp/pb_smb_err || { echo "umount failed: $(cat /tmp/pb_smb_err)" >&2; rm -f /tmp/pb_smb_err; exit 5; }
        rm -f /tmp/pb_smb_err
    fi
    if [ "$FORGET" = "forget" ]; then
        _fstab_remove "$MP"
        systemctl daemon-reload 2>/dev/null || true
        rmdir "$MP" 2>/dev/null || true
    fi
    echo "unmounted $MP"
    ;;

  *)
    echo "usage: $0 mount|unmount ..." >&2
    exit 2
    ;;
esac
