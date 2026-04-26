# python_client

Keyboard teleop for RoverC over UDP.

## Requirements

Python 3.9+. Stdlib only. Linux or macOS terminal (Windows is out of scope).

## Setup

```sh
# from repo root
cp config.example.json config.json
# edit config.json: set wifi.ssid / wifi.password / server.host / server.port
```

`server.host` is the IP shown on the StickC Plus2 LCD after boot.

## Run

```sh
python3 src/python_client/teleop.py --config config.json
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
{"t": 1714200000.123, "vx": 0.40, "vy": 0.00, "wz": 0.00}
```

The server (StickC Plus2) applies failsafe (`control.failsafe_ms`) -- if the client stops sending, motors stop within that window.
