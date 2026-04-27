# python_client

Keyboard teleop for RoverC over UDP.

## Requirements

Python 3.9+ via [uv](https://docs.astral.sh/uv/). Uses pygame for keyboard input
(true KEYDOWN/KEYUP, no terminal auto-repeat issues) and as the host for future
on-window camera display.

## Setup

```sh
# from repo root
cp config.example.json config.json
# edit config.json: set wifi.ssid / wifi.password / server.port
```

The StickC's IP is assigned by DHCP and shown on its LCD on each boot, so it
lives outside `config.json` and is supplied to the client at run time.

## Run

```sh
# prompt for server IP at startup
uv run src/python_client/teleop.py --config config.json

# pass the LCD-shown IP directly
uv run src/python_client/teleop.py --config config.json --host 192.168.1.123

# override per-packet motor cap (default: config.control.max_motor)
uv run src/python_client/teleop.py --host 192.168.1.123 --max-motor 80

# per-motor trim "front_left,front_right,rear_left,rear_right"
uv run src/python_client/teleop.py --host 192.168.1.123 --trim "0.7,0.7,1.0,1.0"
```

## Keys

| key | action |
|---|---|
| `w` / `s` | forward / backward (vx) |
| `a` / `d` | strafe left / right (vy, mecanum) |
| `q` / `e` | rotate CCW / CW (wz) |
| `space` | immediate stop |
| `Esc` / `Ctrl-C` | quit (sends stop) |

Multiple keys may be held; the client composes them additively. Keys auto-release after ~150 ms of no key repeat (typical OS auto-repeat is 30 ms).

## Wire format

Sends one JSON packet at `control.rate_hz`:

```json
{"t": 1714200000.123, "vx": 0.40, "vy": 0.00, "wz": 0.00, "mx": 60}
```

`mx` is the per-packet motor cap (0..127); the server uses it to scale mecanum
output. If absent, the server falls back to the compile-time `MAX_MOTOR`.

The server (StickC Plus2) applies failsafe (`control.failsafe_ms`) -- if the client stops sending, motors stop within that window.
