#!/usr/bin/env bash
# PulseBox launcher — sets working directory so binary finds modules/ and assets/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PULSEBOX_DIR="$SCRIPT_DIR"
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/pulsebox" "$@"
