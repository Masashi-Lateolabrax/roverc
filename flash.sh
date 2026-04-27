#!/usr/bin/env bash
# Regenerate secrets, compile, and upload the StickC Plus2 sketch.
#   ./flash.sh                  # auto-detect port
#   ./flash.sh /dev/ttyACM0     # explicit port (positional)
#   ./flash.sh --list           # only show connected boards
#   PORT=/dev/ttyACM1 ./flash.sh   # override port via env
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

FQBN="esp32:esp32:m5stack_stickc_plus2"
SKETCH="src/roverc_server"

case "${1:-}" in
  --list|-l)
    arduino-cli board list
    exit 0
    ;;
  "") ;;
  /*) PORT="$1" ;;
  *)
    echo "usage: $0 [--list | /dev/ttyXXX]" >&2
    exit 2
    ;;
esac

# Resolve and verify the port BEFORE the slow gen/compile so we fail fast
# when the StickC isn't plugged in.
if [[ -z "${PORT:-}" ]]; then
  matches=$(arduino-cli board list | awk '/\(USB\)/ { print $1 }')
  count=$(printf '%s\n' "$matches" | grep -c '^/dev/' || true)

  if [[ "$count" == "1" ]]; then
    PORT="$matches"
    echo "Auto-selected port: $PORT (only USB serial device)"
  else
    echo "Cannot auto-pick a port (found $count USB serial device(s))." >&2
    echo
    arduino-cli board list >&2
    echo
    echo "Plug in the StickC, or specify: ./flash.sh /dev/ttyXXX" >&2
    exit 1
  fi
fi

if [[ ! -e "$PORT" ]]; then
  echo "Port $PORT does not exist. Plug in the StickC and try again." >&2
  echo
  arduino-cli board list >&2
  exit 1
fi

uv run --no-project python3 scripts/gen_secrets.py
arduino-cli compile --fqbn "$FQBN" "$SKETCH"
arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"
