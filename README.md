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

各輪は 4 相機械 `IDLE → KICK → STEADY → BRAKE → IDLE` を回り、各相の中で

```
p_norm(s, t) = s · f(s, t) + g(s, t)
out = clamp(p_norm · max_motor)        // I2C 送信値
```

を計算する。`s` は per-wheel メカナム混合値の正規化値（`[-1, 1]`）、`t` は
相相対時刻（秒）。`f` と `g` は 2 変数多項式（次数 ≤ 3 in s and t）で、
1 セルあたり `4×4 + 4×4 = 32` 自由度。セル数は `4 wheels × 2 dirs × 3 phases = 24`、
全体で **768 自由度**。

CMA-ES が JSON で永続化される 768 次元ベクトルを最適化する。
`coefs/identity.json` がスカラ恒等のベースライン
（KICK/STEADY: `a[0][0]=1`、BRAKE: 全 0）。

```sh
uv run python scripts/make_identity_coefs.py coefs/identity.json
```
