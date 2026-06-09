# プラットフォーム bring-up 計画（指導者作業）

最終更新: 2026-06-09（前方ステレオ2台 → 前方単眼1台へ構成変更）

## 位置付け

学生卒研の前段階として、**指導者が単独で実施**するハード・ソフトウェア基盤整備。各段で動く成果物を残し、学生に検証済プラットフォームとして引き渡す。学生は引き渡し後、Mediator 層（複数オペレータ UI、同期記録、ベースライン Mediator、実験運営）に集中できる。

## 現状（2026-06-09 時点）

**2026-06-09 構成変更**：実験前提を前方ステレオ2台から **前方単眼1台**（no fisheye）へ変更。段4「ステレオ化」は廃止、段3 の同期はカメラ間ペアリングが不要になり「単眼フレーム ↔ ロボット状態」の整合のみに縮小。

| 段 | 状態 | 備考 |
|---|---|---|
| 段1 RoverC 遠隔操作 | **完了** | pygame UI、UDP server、4 相 polynomial モータモデル（PR #8 で even-Lukács に確定）。モータ特性（係数・相長さとも per-(wheel,dir)）は `config.json` の `motor` セクションのみで設定（CMA-ES 自動校正は 2026-06-10 に廃止、teleop の実行時調整機能も撤去） |
| 段2 カメラ単眼 | **完了**（最終構成） | 前方カメラ 0x40 単一 slave、StickC I2C-mediated discovery、HTTP `/jpg`・`/stream` 配信。単眼が最終構成（旧ステレオ2台目は撤去） |
| 段3 カメラ時間同期 | **未着手** | `Wire1.onReceive` のエントリポイントは camera firmware に既存。単眼化でカメラ間ペアリング不要、カメラ↔ロボット状態の整合のみ。実装は handoff note の Stage-3 セクション参照 |
| 段4 ステレオ校正 | **廃止**（2026-06-09） | 単眼化により不要 |
| 段5 性能評価 | **未着手** | 同期ジッタ・FPS・レイテンシ・Grove 5V ドロップ実測すべて未実施 |

**未解決の既知問題**:
- カメラストリーミングの 1 fps 退行（2026-04-28 観測、root cause 未特定、handoff note 参照）
- RoverC front_left モータの非対称性（個体差 vs 設計、未確定）

## 5段ロードマップ

### 段1: RoverC 遠隔操作（StickC Plus2 サーバ + Python クライアント）

**目的**: PC から StickC Plus2 経由で RoverC を動かせる最小構成

**技術構成**:
- StickC Plus2: WiFi STA で AP 接続、HTTP or UDP サーバ
- Python: requests / asyncio / socket でコマンド送信
- StickC ↔ RoverC は I2C（HAT バス自動接続、0x38、レジスタ 0x00–0x03 = Motor1–4 speed、`roverc_i2c_protocol.pdf` 参照）
- Plus2 P1 STICKIO ヘッダは無印と同一配列（pin3=G26/SCL, pin5=G0/SDA）、`stickc_plus2_schematic.pdf` で確認済。`Wire.begin(0, 26)` でそのまま動作見込み
- RoverC 公式 docs に Plus2 互換明記なし（後発のため更新漏れ）。電気的互換は schematic で確認済、段1 で実機動作確認を行う

**成果物**:
- StickC Plus2 スケッチ（Arduino、`Wire` で I2C、`WiFi` でサーバ）
- Python ラジコンクライアント（最初は WASD キー入力）
- 動作確認動画

**完了基準**: PC から WASD で前後・左右斜行・旋回が安定動作

---

### 段2: カメラ単眼 + Python 画像取得

**目的**: Timer Camera X 1 台で PC に画像配信

**技術構成**:
- Timer Camera X: WiFi STA、CameraWebServer 派生で MJPEG 配信
- Python: OpenCV `cv2.VideoCapture("http://.../mjpeg")` or 自前 MJPEG パーサ

**成果物**:
- Camera スケッチ（M5Stack 公式 example 派生）
- Python live ビューア
- フレームレート計測ログ

**完了基準**: VGA 30fps 安定、PC 表示遅延 < 200ms

---

### 段3: カメラ時間同期（有線 I2C master time）

**目的**: フレームごとの撮影時刻を共通時計系に整合

**技術構成**:
- StickC Plus2 を I2C master、カメラを slave（HY2.0 GPIO 13=SCL, 4=SDA を I2C 周辺、アドレス 0x40）
- StickC Plus2 が 1Hz 程度で `esp_timer_get_time()` をブロードキャスト書き込み
- カメラ側 `onReceive` でローカルタイマーオフセット計算
- 各 `camera_fb_t.timestamp` をマスタ時刻系に変換し画像と一緒に PC へ配信

**成果物**:
- StickC タイムマスタコード
- カメラ I2C slave 受信コード
- タイムスタンプ付き画像ストリーム

**完了基準**: 視野内 LED フラッシュ等でフレーム時刻整合性を実測、ジッタ < 5ms

---

### 段4: ステレオ化（カメラ増設）— **廃止（2026-06-09）**

実験前提を前方単眼へ変更したため、この段は廃止。2 台目カメラ・ステレオリグ・`cv2.stereoCalibrate`・時刻整合ペアリングはいずれも不要。

旧ステレオ設計を将来再導入する場合は git 履歴（〜2026-06-08）を参照。

---

### 段5: 性能評価

**目的**: プラットフォーム全体の特性化

**評価項目**:
- 同期ジッタ実測（ms/μs スケール）
- フレームレート、欠損率
- エンドツーエンドレイテンシ（Python コマンド → モータ動作 → 画像反映）
- 校正精度（単眼 intrinsic の reproject error、マーカー pose 推定精度。旧ステレオ reproject は廃止）
- Grove 5V ドロップ実測（バッテリ容量検証、Option A→B エスカレーション判断材料）
- セッション持続時間（バッテリ持ち、モータ + カメラ同時動作下）

**成果物**:
- 性能レポート（CLAUDE.md「データ品質」評価軸と整合）
- 学生向けプラットフォーム使用ドキュメント
- 既知の限界・未検証項目リスト

**完了基準**: 全評価項目に数値、再現可能性確認、Grove 5V 容量判定済

---

## 学生引き渡し時の interface

引き渡し成果物:
1. 動作確認済ハード（StickC Plus2 + RoverC + 前方単眼カメラ）
2. ファームウェア（StickC Plus2 スケッチ、前方カメラスケッチ `camera_node_front`、書き込み手順）
3. Python ラッパライブラリ（`roverc.move(...)`, 前方カメラの時刻付きフレーム取得 等）
4. 校正ファイル + 校正手順書
5. 性能レポート（既知の限界・未検証項目を明示）

学生はこの上に Mediator 層（複数オペレータ UI、同期記録、ベースライン Mediator、実験運営）を構築する。

---

## バッテリテレメトリ

3 個の独立給電デバイス（StickC Plus2、RoverC、前方カメラ）の電池切れを silent failure ではなく可視化するため、teleop UI の入力パネル下端に 3 並びのバッテリ widget を表示する（2026-06-09 の単眼化前は左右+魚眼を含む 5 並びだった）。

| デバイス | 取得経路 | 値 |
|---|---|---|
| StickC Plus2 | `M5.Power.getBatteryVoltage/Level/isCharging`、25Hz telemetry の trailer に同梱（PR #16, magic 0xD2） | mV / % / charging |
| 前方カメラ | カメラ側 `analogReadMilliVolts(38) * (R28+R29)/R29`（R28=1.37K, R29=2.67K → ×1.513、Sch_M5TimerCAM.pdf 参照）、I2C status frame の `[8..9]` に LE で乗せて StickC が `~1Hz` JSON で PC へ伝搬（PR #17, 分圧比 fix は PR #19） | mV |
| RoverC | **直読み不可**（公式 I2C プロトコルにバッテリレジスタなし、STM32F030 ファームは閉じ／書き換え非実用）。代理として StickC `isCharging` を見る — RoverC が HAT バスに 5V を供給している間 True、バッテリが落ちると 5V が崩れて False に flip する | OK / DYING |

### RoverC proxy の信頼境界

`isCharging` は外部給電が電池電圧を上回ると True を返すので, RoverC の電池が生きている間は HAT バス 5V 経由で True が返る. 電池が落ちて 5V レールが崩壊すると False に切り替わる. ただし以下の状況では proxy が誤る:

- StickC が USB ケーブル経由でも給電されているとき（開発時）→ RoverC の状態に関係なく True に張り付く
- RoverC のメインスイッチが OFF のとき → False（誤って "DYING" 表示になるが事実上 OFF）

代替案: RoverC BAT+ レールから StickC ADC ピン（HAT 8 ピンヘッダの G36 / G25）へ直結ハンダ → `analogRead`. 初手では実装せず, proxy の不足が観測されたら escalate.

### 閾値

- StickC %: ≥ 30 GREEN / 10–30 YELLOW / < 10 RED
- カメラ mV: ≥ 3800 GREEN / 3500–3800 YELLOW / < 3500 RED（1S Li-ion 想定で保守的）
- 値が読めない / probe 未着 → グレー "—"

---

## 電力 topology

各デバイスは独立して電池を持っているが, 配線で繋がる結果として **schottky-OR されたシステム** になる. 設計意図としての power flow は以下:

```
 RoverC 16340 (700mAh)
        │
        ▼
   boost converter (5V, 容量未公表 ≦ ~2A 推定)
        │
        ├── HAT 8-pin (G26/G0/5V/GND) ─────► StickC Plus2
        │                                         │ (HAT 5VIN は動作電源のみ.
        │                                         │  TP4057 充電パスは USB-C VBUS 経由)
        │                                         └─ 内蔵 200mAh LiPo
        │
        ├─[USB-C pigtail]─► StickC USB-C VBUS → TP4057 VCC → BAT → 200mAh LiPo (充電)
        │   ↑ 採用予定 mod (1 本のみ). カメラへの pigtail は不採用 (理由は後述)
        │
        ├── Grove I2C① (SCL/SDA/5V/GND) ──┐
        │                                  │ (単眼化で 1 本直結.
        ├── Grove I2C② (SCL/SDA/5V/GND)    │  旧ステレオ/魚眼期は I2C① から
        │                                  │   3-way ハンダ splice だった)
        ├── Servo S1/V/G (Pro 専用)         │
        ├── Servo S2/V/G (Pro 専用)         │
        │                                  ▼
        │                          前方 Timer Camera X の Grove (HY2.0)
        │                              5V → VSYS_VIN (動作電源, 充電パスではない)
        │                                       │
        │                                       ▼
        │                               3V3 LDO → ESP32 / OV3660

  (各カメラ内部詳細, 参考)
                          USB-C VBUS (通常未配線, off-rover 充電時のみ)
                                       │
                              ┌────────┴────────┐
                              ▼                 ▼
                         TP4057 VCC         D8 (1N5819)
                         (USB-C 経由限定)         │
                              │                  ▼
                          BAT ピン        VSYS_VIN ──► 3V3 LDO ──► ESP32 / OV3660
                              │                  ▲
                              ▼                  │
                          VBAT_IN ◄── J4 内蔵 LiPo (140mAh)
                              │
                       FET3 (PMOS) ◄─ POWER_HOLD (GPIO 33 = HIGH で ON)
                              │
                              ▼
                            VBAT ──┬── D6 (1N5819) ──► VSYS_VIN
                                   │
                                   └── R28 (1.37K) ─ GPIO 38 ADC ─ R29 (2.67K) ─ GND
                                                    (V_GPIO38 / VBAT = 0.661)
```

### Battery sharing / fallback の性質

「動作電源 (5V rail)」と「LiPo 充電パス (TP4057 VCC)」を分けて見る必要がある. **動作電源は RoverC 5V rail で全機共有されているが, LiPo 充電パスはどの子機にも HAT/Grove 経由では届かない**. これは 2026-04-30 の実測で決着している (カメラ: 全機完全枯渇まで放電後, USB-C+Grove vs Grove only の比較で USB-C のみ電圧上昇. StickC: HAT 接続のみで運用していたら何度も枯渇).

カメラ側 (Grove 5V → VSYS_VIN):
- **RoverC が生きている間**: Grove 5V が VSYS_VIN を駆動. 内蔵 LiPo は D6 逆バイアスで isolate, **充電も放電もされず固定**
- **RoverC が落ちた瞬間**: VSYS_VIN が 5V → 0V に向かう途中で D6 が導通開始 → カメラは内蔵 LiPo (140mAh) 経由で動き続ける. 満充電なら ~1 時間 (FET3 が ON なら). ただしこの間 motor は止まってるので「安全停止のための tail」用途
- **充電したい場合**: USB-C 経由でないと TP4057 に給電が届かない. 採用方針は **off-rover 個別 USB 充電** (機材側でハブ充電, 各カメラに pigtail は引かない)

StickC Plus2 側 (HAT 5VIN):
- **動作電源**: HAT 5VIN は動作電源として機能. RoverC 生きてる間は HAT 5V で system 駆動
- **LiPo 充電**: 実機で**何度も枯渇している**事実から, HAT 5V → TP4057 のチャージパスは効いていない (回路図上 +5VIN ネットに TP4057 VCC が乗ってるように見えるが, 実態として LiPo 充電は走っていない. 詳細トレース未確定だが empirical に決着済)
- **採用方針**: **USB-C pigtail 1 本だけ追加**して RoverC 5V → StickC USB-C VBUS → TP4057 VCC で内蔵 LiPo を充電する. これがあれば session 中 LiPo を満タン維持でき, RoverC 落ち時も `isCharging=False` 検知 → ~2.5 時間の単独動作で安全停止判断が可能

つまり **「動作電源は親 RoverC が全機に流す」「LiPo 充電は USB-C pigtail を引いた子機 (StickC のみ) が受け取る」「カメラ LiPo は session-local fallback (D6) のみ」**. これを teleop UI で可視化したのが PR #18 の battery strip + RoverC `isCharging` proxy. RoverC が落ちると `isCharging=False` → "DYING" 表示 → 操縦者は数十秒内に安全停止判断ができる.

### POWER_HOLD (GPIO 33) を起動時に HIGH にする必要

カメラの battery sense (R28/R29 分圧) の入力は **VBAT (FET3 の下流)** で, **VBAT_IN (J4 直結) ではない**. そのため:

- **POWER_HOLD が LOW のまま** → FET3 OFF → VBAT 浮遊 → 分圧器の入力なし → ADC は ~140 mV の floor で saturate (= UI 上 0.28V 固定で全く動かない)
- **POWER_HOLD HIGH** → FET3 ON → VBAT = VBAT_IN ≈ 電池電圧 → ADC が正しく分圧電圧を検出
- カメラ自体は Grove HAT 5V から VSYS_VIN 経由で動くので, POWER_HOLD を触らなくても普通に動作する → **動作上は気付きにくいバグ**

PR #22 で `pinMode(33, OUTPUT); digitalWrite(33, HIGH);` を `camera_main_setup` 冒頭に追加して修正済 (M5Stack 公式 `Power_Class::begin()` の初期化順と一致). 「未配線: USB-C pigtail」だった頃の vbat_mv が 3 機とも 0.28V 固定だったのは pigtail / 充電状態とは無関係で, 単にこの初期化漏れが原因.

### 電流予算

公式データシートに RoverC の boost 出力電流仕様の記載なし. ただし M5 が 2 つの Grove I2C ポートを「expansion module 用」と明示している以上, base 負荷 (motors + StickC) + 数百 mA の expansion 余裕は specced されているはず.

連続電流の試算 (推測, ±20%):

| 負荷 | 連続値 |
|---|---|
| 4 N20 モータ 中速 | ~400 mA |
| 前方カメラ 1 台 VSYS run | ~200 mA |
| StickC 動作 | ~80 mA |
| **base 合計** | **~0.7 A** |
| StickC LiPo 充電 (CC phase, **空からの初回 ~10 分のみ**) | +~100 mA |

motor stall (4 A peak, 数十 ms transient) は出力キャパで吸収される領域なので連続電流の議論からは除外. カメラ LiPo 充電電流は **0** (pigtail 不採用方針のため). 単眼化前は 3 カメラで ~600 mA / base ~1.1 A だったが, 1 台化で base 負荷が ~0.4 A 軽くなった.

### 充電電流の実態は瞬間的でない

TP4057 は CC-CV 充電で, StickC 200mAh LiPo を空から CC ~100 mA で充電する場合 ~10 分で C/2 に達して以降 CV phase で電流が漸減 (~30 → 数 mA), 完了後は termination で数 mA 以下に落ちる. **平常セッションでは StickC 充電電流は数 mA レベル**で base 負荷に埋もれる. つまり:

- 初回充電セッション: base 0.7 A + StickC 充電 0.1 A = **~0.8 A** (10 分以内)
- 平常セッション: base 0.7 A + StickC 充電 ~0.005 A = **~0.7 A**

1〜2 A 級 boost に対して常時十分余裕. カメラ pigtail 不採用 + 単眼化での base 軽量化で sag 懸念はほぼ消える.

### USB-C 充電 pigtail mod (採用予定: StickC のみ)

子機の LiPo を充電するには TP4057 VCC まで 5V を届ける必要があるが, HAT pin 8 や Grove HY2.0 5V 経由ではこのパスが繋がらない (実測確認済 2026-04-30, Grove は VSYS_VIN まで, HAT 5VIN は StickC 動作電源までで止まる). USB-C VBUS だけが TP4057 VCC への直接ルートになる.

**採用方針: StickC Plus2 に 1 本だけ pigtail を引く**.

1. 100均の充電専用 USB-C ケーブルを剥いて 5V/GND 2 線を引き出し
2. RoverC の Grove 5V (or Servo V) から 1 本分岐
3. StickC Plus2 の USB-C ポートに食わせる
4. RoverC 動作中 StickC 内蔵 200mAh LiPo が常時 top-up される

前方カメラへの pigtail は**不採用**（単眼化前のステレオ+魚眼 3 台でも同方針だった）. 理由:

- カメラ LiPo は post-RoverC-death の数十秒 〜 ~1 時間の tail にしか効かない. その間 motor は止まっているので駆動継続は不可能, 用途は安全停止 / 最後のフレーム / データ flush 程度
- StickC は逆に「落ちると操縦も I2C master も WiFi も全滅」なので最優先で守るべき LiPo
- 1 本だけにすればハーネス管理 / 半田作業 / 物理保護が最小. 充電電流も ~100 mA 程度で sag リスクほぼゼロ
- カメラを充電したいときは off-rover で USB ハブ経由個別充電 (セッション前後の運用ルール) で十分

過剰な保護回路 (直列抵抗 / polyfuse / TP4057 R30 swap) は **基本不要**. 1 本 / 100 mA レベルの追加負荷で RoverC 5V boost が sag するリスクは事実上ない. 半田部の物理保護として **熱収縮 + ホットボンド**で機械強度を確保する方が polyfuse より実用的.

### 必要な検証

- **動作確認**: pigtail 接続後, StickC を完全枯渇まで放電 → RoverC 起動して pigtail 経由で接続 → battery widget の電圧上昇を確認 (= TP4057 が VBUS から給電を受けて充電している証拠)
- **sag 監視**: pigtail 接続中に RoverC Grove 5V を multimeter で測定. 5V > 4.5V を維持していれば常用に問題なし

---

## タイムライン

学生作業（CLAUDE.md「学生作業の年間スケジュール」）との整合確認用：

| 段 | 完了 | 学生側で前進可能になるもの |
|---|---|---|
| 段1 | 2026-04 | Python クライアント側の WASD UI（学生が複数オペレータ UI のベースに発展可） |
| 段2 | 2026-04 | 単眼画像での視覚的タスク試行 |
| 段3 | TBD | 時刻整合データ取得（カメラ↔ロボット状態） |
| 段4 | 廃止（2026-06-09） | 単眼化により廃止 |
| 段5 | TBD | プラットフォーム全部 |

---

## リスクと escalation 経路

### Grove 5V 容量未確定
- 段1〜段3 で実測。不足時はバッテリ直タップ + カメラ側 boost コンバータに escalate
- RoverC バッテリ実装：3.7V 700mAh（16340、Pro 仕様準拠）。同時運用 15〜30 分見込み
- StickC Plus2 内蔵バッテリ：200mAh（無印 80–95mAh の 2 倍以上）→ Plus2 単体駆動時のセッション持続が改善
- 詳細: CLAUDE.md ハード調査結果セクション

### I2C bus 競合
- StickC Plus2 master が RoverC（0x38）+ 前方カメラ（0x40）を駆動（単眼化で slave は 1 台に減り、競合余地はさらに低下）
- バス占有率は 5% 未満想定（モータ 50Hz × 5byte + 時刻 1Hz × 8byte）
- 競合発生時はモータ更新と時刻配信のタイミング設計で分離
- 最悪ケース: カメラ用に別 I2C バス（Plus2 Grove GPIO 32/33 を使う）

### 校正ドリフト
- リグ振動・温度変化で校正パラメータがずれる可能性
- 段5 で経時変化測定、必要なら定期再校正フロー追加

### Timer Camera X 固有の検証不足
- カメラ × WiFi 同居の干渉報告あり（espressif/esp32-camera issue #620、`fb_count=2` で回避）
- 段2 で要確認

---

## 参照資料（このリポジトリ内）

- `../CLAUDE.md`: 上位プロジェクト文書、ハード調査結果詳細
- `../roverc_datasheet.pdf`: RoverC（K036）公式仕様書
- `../roverc_pro_datasheet.pdf`: RoverC-Pro（K036-B）公式仕様書
- `../roverc_i2c_protocol.pdf`: RoverC I2C コマンドプロトコル
- `../stickc_plus2_schematic.pdf`: M5StickC Plus2 公式 schematic（v0.5）
