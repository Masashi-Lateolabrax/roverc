# python_client

PC 側コード。ユーザ向けの起動・運用手順は **トップレベル `../../README.md`** を見る。
ここは Python 実装ノートとワイヤフォーマット詳細だけ。

## モジュール構成

| File | Role |
|---|---|
| `teleop.py` | pygame マルチウィンドウ UI、UDP モーション送信、カメラ表示 |
| `calibrate.py` | CMA-ES ループによる多項式係数の自動校正 |
| `roverc.py` | `RoverCClient`（UDP socket、JSON / binary 多重化、telemetry rx loop） |
| `camera.py` | `CameraRegistry` + `CameraStream`（HTTP MJPEG 受信） |
| `coefs.py` | 多項式係数の dataclass、JSON I/O、binary chunk encoder、CMA-ES vector pack/unpack |
| `telemetry.py` | 0xD2 packet パーサ + thread-safe ring buffer |
| `widgets.py` | pygame の Slider / Button / ChoiceRow |

## 依存関係

`pyproject.toml` で管理。`pygame`（teleop）、`numpy` + `cma`（calibrate）。

## ワイヤフォーマット

PC ↔ StickC は単一 UDP ポート（既定 4210）で 4 種多重化。

### PC → StickC

#### motion（JSON）

```json
{"t": 1714200000.123, "vx": 0.40, "vy": 0.00, "wz": 0.00}
```

`control.rate_hz` で連送。途絶 `control.failsafe_ms` でファーム側 failsafe。

#### cfg envelope（JSON）

```json
{"cfg": {
  "mx":   60,
  "kdur": 100,
  "bdur": 100,
  "tel":  false,
  "tf":   [1, 1, 1, 1],
  "tb":   [1, 1, 1, 1],
  "kf":   [1, 1, 1, 1],
  "kb":   [1, 1, 1, 1]
}}
```

- `mx`: per-tick motor cap, 0..127
- `kdur` / `bdur`: KICK / BRAKE 相の duration（ms）
- `tel`: 25 Hz binary telemetry の enable
- `tf` / `tb` / `kf` / `kb`: legacy スカラ trim（4 輪、fwd/bwd × steady/kick）。
  ファーム内では対応する STEADY/KICK セルの `a[0][0]` だけを書き換える。
  既存の teleop slider はこの経路で動く

#### polynomial chunk（binary, 132 B）

```
[0]        magic 0xC0
[1]        wheel  (0..3 = FL FR RL RR)
[2]        dir    (0=fwd, 1=bwd)
[3]        phase  (0=KICK, 1=STEADY, 2=BRAKE)
[4..67]    a[16] little-endian float, row-major (j*4+k)   -- f-poly
[68..131]  b[16] little-endian float, row-major           -- g-poly
```

24 chunks（4 × 2 × 3）で全更新。`(wheel, dir, phase)` でべき等。
`coefs.push_to_firmware()` は各 chunk を 2 回送り（ESP32 LWIP rx queue が浅い、
~6-8 packets）、8 ms 間隔でペースする。非有限値はファーム側で reject + Serial log。

### StickC → PC

#### camera state（JSON, ~1 Hz）

```json
{"cam": {"left":  {"ip": "192.168.1.42", "port": 80, "ok": true},
         "right": {"ip": "192.168.1.43", "port": 80, "ok": true}}}
```

ファーム I2C プローブが拾った左右カメラの IP / port / `camera_ok` を伝搬。
`null` は不在。

#### telemetry（binary 73 B, 25 Hz when `cfg.tel = true`）

```
[0]       magic 0xD2
[1..4]    uint32 LE  millis()                     -> fw_t_ms
[5..8]    float       gx_dps      (raw gyro X, deg/s)
[9..12]   float       gy_dps      (raw gyro Y, deg/s)
[13..16]  float       gz_dps      (raw gyro Z, deg/s)
[17..20]  float       ax_g        (raw accel X, g; M5Unified default unit)
[21..24]  float       ay_g        (raw accel Y, g)
[25..28]  float       az_g        (raw accel Z, g)
[29..32]  uint8[4]    phase       (PH_IDLE=0, KICK=1, STEADY=2, BRAKE=3)
[33..48]  float[4]    s_pre       (BRAKE-entry snapshot of normalised s)
[49..52]  int8[4]     motor       (commanded I2C value)
[53..68]  float[4]    s_norm      (current-tick normalised m_i, ∈ [-1, 1])
[69..70]  uint16 LE   vbat_mv     (StickC battery voltage, mV)
[71]      uint8       bat_pct     (0..100; 0xFF = unknown)
[72]      uint8       charging    (0=no, 1=yes; 0xFF = unknown)
```

`telemetry.parse(raw)` → `TelemetryPacket`（`pc_t` は受信時の `time.monotonic()`）。
`TelemetryQueue` がバッファ。`calibrate.py` は trial 毎に `drain()` して
コスト計算する。
