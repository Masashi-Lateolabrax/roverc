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
`f_k`、`f_b` は次数 N（default 3、`--poly-order` で 1〜5 可変）の単変数
多項式で、境界条件は phase 遷移の連続性と BRAKE 終了時の出力ゼロを表す。
`k` は per (wheel, dir) の STEADY 利得（モータ強度差を吸収）。

per (wheel, dir) の **自由パラメータ**（CMA-ES 最適化対象）：
`1 (k) + (N−1) (f_k Bernstein 内部点) + (N−1) (f_b 内部点) = 2N−1`。
N=3 で **5 / cell × 8 cells = 40 次元**。CMA-ES の Hansen λ デフォルトは
`4 + ⌊3·ln(40)⌋ = 15`。

**非負性の保証**：`k ≥ 0`、`f_k ≥ 0`、`f_b ≥ 0`（負だと相内でモータ方向が
反転する）は構造的に保証されている：

- CMA-ES 空間の 40 次元ベクトル `x ∈ R^40` を `b = x²` で写像（常に `≥ 0`）
- `f_k` と `f_b` は Bernstein 制御点で表現し、内部点（`b₁..b_{N−1}`）と
  端点（境界条件で固定）が全て非負なら凸結合として `f ≥ 0` が成立

`coefs/identity.json` は線形ベースライン：`k = 1`、`f_k(t) = t/T_k`、
`f_b(t) = 1 − t/T_b`。すべての境界条件と非負性を満たす。

```sh
uv run python scripts/make_identity_coefs.py coefs/identity.json
uv run python scripts/make_identity_coefs.py coefs/identity_n4.json --poly-order 4
```
