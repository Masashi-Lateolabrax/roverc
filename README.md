# roverc

M5StickC Plus2 が RoverC（メカナム車）を I2C で駆動し、Timer Camera X 2 台で
ステレオ JPEG を WiFi 配信、PC（Python）から操縦・校正・記録する。

## ハードウェア

- M5StickC Plus2 ×1（運用機 / UDP server）
- M5StickC（無印、予備）
- RoverC（メカナム車 + STM32 ハット、I2C 0x38）
- M5Stack Timer Camera X ×2（前方ステレオ、I2C 0x40 / 0x41 でアドレス分離）
- PC ×1（Python 3.9+、`uv`）
- 入力：キーボード ×1（最低構成）

## セットアップ

### 1. リポジトリ + secrets

```sh
git clone <repo>
cd roverc
cp config.example.json config.json
# config.json を編集: wifi.ssid / wifi.password / server.port
```

### 2. Python 依存

```sh
uv sync                # ランタイム依存のみ
uv sync --group dev    # ruff + pyright も含める
```

ランタイム依存：`pygame`（teleop UI）、`numpy` + `cma`（calibrate の CMA-ES ループ）。
dev 依存：`ruff`（lint + import 整列）、`pyright`（型検査）。設定は `pyproject.toml` の
`[tool.ruff]` / `[tool.pyright]` セクション。

### 3. arduino-cli + コア

```sh
arduino-cli core install esp32:esp32     # ボードマネージャ URL: https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install M5Unified ArduinoJson
```

各サブスケッチ直下に `fqbn.txt` がある（Plus2 は `esp32:esp32:m5stack_stickc_plus2`、Timer Camera X は `esp32:esp32:m5stack_timer_cam`）。

## ファーム書き込み

```sh
# StickC Plus2（teleop server）
./flash.sh src/roverc_server

# Timer Camera X（左右どちらも）
./flash.sh src/camera_node /dev/ttyACM0
```

`flash.sh` はポート自動検出。複数台繋がっている時は明示指定。

## 操縦

```sh
# StickC LCD に出る IP を直接渡す
uv run src/python_client/teleop.py --host 192.168.1.123

# 校正済の多項式係数を起動時にプッシュする
uv run src/python_client/teleop.py --host 192.168.1.123 --coefs coefs/identity.json
```

3 ウィンドウ（input / settings / cameras）が開く。input 窓にフォーカスして以下：

| key | action |
|---|---|
| `w` / `s` | forward / backward (vx) |
| `a` / `d` | strafe left / right (vy) |
| `q` / `e` | rotate CCW / CW (wz) |
| `space` | immediate stop |
| `Enter` | apply settings |
| `Esc` / `Ctrl-C` | quit |

複数キー同時押し可。settings 窓のスライダ（trim / kick / framesize / quality）を変更したら **Apply** ボタンで反映。

## 校正（CMA-ES、自動）

`teleop.py` で手動 trim を回す代わりに、平らな床に置いた状態で
`calibrate.py` を流して per-wheel の (3,3) 多項式係数を学習させる。

```sh
# 世代数と個体数を直接指定。
# 5 candidates × 10 trials × ~3.5s/trial ≈ 175s/世代、10 世代で 30 分弱。
uv run python src/python_client/calibrate.py \
    --host 192.168.1.123 \
    --generations 10 --pop-size 5 \
    --out coefs/v1.json

# 既存の校正結果から再開 / 洗練
uv run python src/python_client/calibrate.py \
    --host 192.168.1.123 \
    --generations 20 --pop-size 5 \
    --init-coefs coefs/v1.json \
    --out coefs/v2.json
```

各候補は `--n-trials`（既定 10）の trial（ランダム direction × 1.5s 駆動 +
1.5s 解放）で評価され、コストは
`α·∫|gz| during drive + β·∫|gz| during release`（β=2、解放時の残留 yaw を強く
ペナルティ）。`--out` は毎ジェネレーション上書きされるので、`Ctrl-C` で中断
しても直前のベストはそこに残っており、`--init-coefs <out>` で再開できる。

校正後：

```sh
uv run src/python_client/teleop.py --host <IP> --coefs coefs/v1.json
```

研究データ収集セッションは **校正済係数を固定** で運用する（platform
dynamics の非定常性を避ける、卒研の主旨「再現可能なデータ収集」に直結）。

## モータ補正モデル

各輪は 4 相機械 `IDLE → KICK → STEADY → BRAKE → IDLE` を回り、相ごとに：

```
KICK    : p = s · f_k(t)         f_k(0)   = 0,        f_k(T_k) = k
STEADY  : p = k · s
BRAKE   : p = s_pre · f_b(t)     f_b(0)   = k,        f_b(T_b) = 0
out     = clamp(p · max_motor)   // I2C 送信値
```

`s` は per-wheel メカナム混合値の正規化値（`[-1, 1]`）、`s_pre` は
STEADY → BRAKE 遷移時のスナップショット、`t` は相相対時刻（秒）。
`k` は per (wheel, dir) の STEADY 利得（モータ強度差を吸収）。

`f_k`、`f_b` は **`[0, T]` 上の偶数次 Lukács 形式 + BRAKE 入力反転**で
表現し、非負性を区間 `[0, T]` 内に過不足なく構造保証する：

```
f_k(t) = t² · q_k(t)² + t(T_k − t) · r_k(t)²              on [0, T_k]
f_b(t) = (T_b−t)² · q_b(T_b−t)² + t(T_b−t) · r_b(T_b−t)²  on [0, T_b]
```

導出：偶数次 Lukács `f(x) = q(x)² + x(T−x)·r(x)²` に `f(0) = 0` を課すと
`q(0) = 0` ⇒ `q = x · q̃` ⇒ `f = x² · q̃² + x(T−x) · r²`。BRAKE は入力反転
`s = T_b − t` で `f̃(0) = 0` の境界に揃え、KICK と同型に扱う。

`q_k`、`r_k`、`q_b`、`r_b` は次数 `m_order − 1`（default 2、`--m-order`
で 1〜2 可変）の **符号自由**な多項式。`f` 多項式の次数は `2·m_order`
（default で 4、ファーム `POLY_MAX_ORDER = 5` 内）。境界条件は
`Q(1) = √(k)/T` という線形制約 1 本ずつに翻訳。

per (wheel, dir) の **自由パラメータ**（CMA-ES 最適化対象）：
`1 (k) + 2·((m−1) (q free) + m (r free)) = 4·m − 1`。m=2 で **7 / cell ×
8 cells = 56 次元**。CMA-ES の Hansen λ デフォルトは `4 + ⌊3·ln(56)⌋ = 16`。
`α`（q の target 正規化）と `β`（r の target 正規化）は無次元化されており、
identity ベクトルは `[1, 1, 1, 0, 1, 1, 0]`（cell ごと）。

**`[0, T]` 限定の非負性**：以前の `t·g²` 形式は `g²` が `ℝ` 全体で非負を
要求するため過剰制約だったが、`q² + t(T−t)·r²` 形式は `t(T−t)` 因子が
区間 `[0, T]` でのみ非負（区間外では負）になり、実必要条件と一致。
`r` の係数は自由に負を取れる。これにより「立ち上がってから内部で軽く
だれて再度 k へ戻る」「線形ランプに小さな bulge を加える」型の形状が
区間内非負を保ったまま表現可能。

`coefs/identity.json` は線形ベースライン：`k = 1`、`q_k = r_k = q_b = r_b
= √(1)/T` 定数 → `f_k(t) = t/T_k`、`f_b(t) = 1 − t/T_b`。

```sh
uv run python scripts/make_identity_coefs.py coefs/identity.json
uv run python scripts/make_identity_coefs.py coefs/identity_m1.json --m-order 1
```
