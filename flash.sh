#!/usr/bin/env bash
# Regenerate secrets, compile, and upload an Arduino sketch.
#   ./flash.sh src/roverc_server                  # auto-detect port
#   ./flash.sh src/camera_node /dev/ttyACM0       # explicit port
#   ./flash.sh --list                             # only show connected boards
#   PORT=/dev/ttyACM1 ./flash.sh src/camera_node  # override port via env
#
# The sketch directory must contain an `fqbn.txt` whose first line is the FQBN.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

usage() {
  cat >&2 <<EOF
usage: $0 <sketch_dir> [/dev/ttyXXX]
       $0 --list

Examples:
  $0 src/roverc_server
  $0 src/camera_node /dev/ttyACM0
EOF
  exit 2
}

case "${1:-}" in
  --list|-l)
    arduino-cli board list
    exit 0
    ;;
  ""|-h|--help)
    usage
    ;;
esac

SKETCH="${1%/}"
if [[ ! -d "$SKETCH" ]]; then
  echo "Sketch directory not found: $SKETCH" >&2
  exit 1
fi

FQBN_FILE="$SKETCH/fqbn.txt"
if [[ ! -f "$FQBN_FILE" ]]; then
  echo "Missing $FQBN_FILE -- create it with the board FQBN on the first line." >&2
  exit 1
fi
FQBN="$(awk 'NR==1 { sub(/[[:space:]]+$/, ""); print; exit }' "$FQBN_FILE")"
if [[ -z "$FQBN" ]]; then
  echo "$FQBN_FILE is empty" >&2
  exit 1
fi

# Port selection: positional arg > $PORT > auto-detect single USB serial.
if [[ $# -ge 2 ]]; then
  PORT="$2"
fi

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
    echo "Plug in the board, or specify: $0 $SKETCH /dev/ttyXXX" >&2
    exit 1
  fi
fi

if [[ ! -e "$PORT" ]]; then
  echo "Port $PORT does not exist. Plug in the board and try again." >&2
  echo
  arduino-cli board list >&2
  exit 1
fi

echo "Sketch: $SKETCH"
echo "FQBN:   $FQBN"
echo "Port:   $PORT"

uv run --no-project python3 scripts/gen_secrets.py
arduino-cli compile --fqbn "$FQBN" --libraries "$REPO_ROOT/lib" "$SKETCH"
arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"
