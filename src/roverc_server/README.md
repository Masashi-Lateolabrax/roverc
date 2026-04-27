# roverc_server

StickC Plus2 firmware: WiFi STA + UDP receiver + I2C bridge to the RoverC HAT.

## Wiring

The RoverC HAT plugs into the bottom 8-pin header of the StickC Plus2:

- SDA = G0 (header pin 5)
- SCL = G26 (header pin 3)
- RoverC slave address: 0x38, motor speed registers 0x00..0x03 (signed int8)

## Build

```sh
# from repo root, after editing config.json
arduino-cli lib install ArduinoJson
./flash.sh                  # auto-detect port
./flash.sh /dev/ttyACM0     # explicit port
./flash.sh --list           # show connected boards
```

`secrets.h` is generated from `config.json` (gitignored). Re-run `uv run scripts/gen_secrets.py` whenever WiFi credentials, port, or control parameters change.

## UI

LCD on boot:

```
OK <SSID>
IP <a.b.c.d>
PORT <port>
```

After packets start arriving:

```
age   12 ms     <- green; turns red when > FAILSAFE_MS
rx 1234
m   60   60
    60   60
```

Buttons:

- A: reconnect WiFi
- B: stop motors and force failsafe

## Wire format (UDP)

Single JSON packet, UTF-8:

```json
{"t": 1714200000.123, "vx": 0.40, "vy": 0.00, "wz": 0.00, "mx": 60}
```

- `vx` forward, `vy` strafe right, `wz` yaw rate; all in [-1, 1].
- `mx` (optional) per-packet motor cap (0..127). If absent, the server falls
  back to compile-time `MAX_MOTOR` from `secrets.h`.
- Server applies mecanum inverse kinematics, scales to `[-mx, mx]`, and writes
  4 signed bytes to RoverC at 50 Hz.
- No packet within `FAILSAFE_MS` -> all motors set to 0.

## Bring-up notes

- Mecanum sign convention varies by chassis wiring. If a wheel spins the wrong way, flip the corresponding `SIGN_M*` constant in `roverc_server.ino`.
- Start with `control.max_motor` low (60) in config.json, raise after sign verification.
- DHCP may give a different IP on each boot. Read it from the LCD and pass it to `teleop.py` (prompt or `--host`).
