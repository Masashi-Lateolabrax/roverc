# プラットフォーム bring-up 計画（指導者作業）

最終更新: 2026-04-29

## 位置付け

学生卒研の前段階として、**指導者が単独で実施**するハード・ソフトウェア基盤整備。各段で動く成果物を残し、学生に検証済プラットフォームとして引き渡す。学生は引き渡し後、Mediator 層（複数オペレータ UI、同期記録、ベースライン Mediator、実験運営）に集中できる。

## 現状（2026-04-29 時点）

| 段 | 状態 | 備考 |
|---|---|---|
| 段1 RoverC 遠隔操作 | **完了** | pygame UI、UDP server、4 相 polynomial モータモデル（PR #8 で even-Lukács に確定）、CMA-ES キャリブレーション基盤 |
| 段2 カメラ単眼 → ステレオ | **完了**（段4 を吸収） | 0x40 / 0x41 multi-slave 構成、StickC I2C-mediated discovery、HTTP `/jpg` 配信。stage 4 のステレオ化は同 iteration で実施済み |
| 段3 カメラ時間同期 | **未着手** | `Wire1.onReceive` のエントリポイントは camera firmware に既存。実装は handoff note の Stage-3 セクション参照 |
| 段4 ステレオ校正 | **部分完了** | 2 台稼働は段2 と一体で完了。OpenCV `stereoCalibrate` による校正実施・reproject error 計測は未実施 |
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

### 段4: ステレオ化（カメラ増設）

**目的**: 2 台ステレオリグ + 校正

**技術構成**:
- 2 台目を同 I2C バスに slave 0x41 で追加（0x38 RoverC, 0x40 Cam L, 0x41 Cam R の multi-slave）
- ステレオマウント設計（基線長、剛性、RoverC 搭載位置）
- OpenCV `cv2.stereoCalibrate` で校正

**成果物**:
- ステレオリグ（3D モデル + 実機）
- 校正パラメータ + 校正手順書
- 時刻整合ステレオペア配信パイプライン

**完了基準**: チェッカーボード校正済、reproject error < 1px

---

### 段5: 性能評価

**目的**: プラットフォーム全体の特性化

**評価項目**:
- 同期ジッタ実測（ms/μs スケール）
- フレームレート、欠損率
- エンドツーエンドレイテンシ（Python コマンド → モータ動作 → 画像反映）
- 校正精度（reproject error）
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
1. 動作確認済ハード（StickC Plus2 + RoverC + ステレオカメラリグ）
2. ファームウェア（StickC Plus2 スケッチ、カメラスケッチ、書き込み手順）
3. Python ラッパライブラリ（`roverc.move(...)`, `cameras.get_synced_pair()` 等）
4. 校正ファイル + 校正手順書
5. 性能レポート（既知の限界・未検証項目を明示）

学生はこの上に Mediator 層（複数オペレータ UI、同期記録、ベースライン Mediator、実験運営）を構築する。

---

## バッテリテレメトリ

5 個の独立給電デバイス（StickC Plus2、RoverC、左右 + 魚眼カメラ）の電池切れを silent failure ではなく可視化するため、teleop UI の入力パネル下端に 5 並びのバッテリ widget を表示する。

| デバイス | 取得経路 | 値 |
|---|---|---|
| StickC Plus2 | `M5.Power.getBatteryVoltage/Level/isCharging`、25Hz telemetry の trailer に同梱（PR #16, magic 0xD2） | mV / % / charging |
| 左 / 右 / 魚眼カメラ | カメラ側 `analogReadMilliVolts(38) * (R28+R29)/R29`（R28=1.37K, R29=2.67K → ×1.513、Sch_M5TimerCAM.pdf 参照）、I2C status frame の `[8..9]` に LE で乗せて StickC が `~1Hz` JSON で PC へ伝搬（PR #17, 分圧比 fix は PR #19） | mV |
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
        │                                         │ (AXP192 が 5V→電池充電)
        │                                         └─ 内蔵 200mAh LiPo
        │
        ├── Grove I2C① (SCL/SDA/5V/GND) ──┐
        │                                  │ 3-way 分岐
        ├── Grove I2C② (SCL/SDA/5V/GND) ──┤  (現状は I2C① 1ヶ所からハンダ splice)
        │                                  │
        ├── Servo S1/V/G (Pro 専用)         │
        ├── Servo S2/V/G (Pro 専用)         │
        │                                  ▼
        │                          各 Timer Camera X / F の Grove (HY2.0)
        │                              5V → VSYS_VIN (D8 を介さない直結)
        │                                       │
        │                                       ▼
        │                               3V3 LDO → ESP32 / OV3660
        │
        └─[未配線: USB-C pigtail]─► 各カメラ USB-C VBUS
                                       │
                                       ▼
                                  TP4057 充電 IC (VCC=VUSB_VCC, USB のみ)
                                       │
                                       ▼
                                   J4 内蔵 LiPo (140mAh)
                                       │
                                       └─ D6 (1N5819) ─► VSYS_VIN
```

### Battery sharing / fallback の性質

各カメラの VSYS_VIN は **D6 (LiPo→VSYS) と D8 (USB-C→VSYS) の schottky OR** で食わされている. これにより:

- **RoverC が生きている間**: Grove 5V (および将来の USB-C pigtail) が VSYS_VIN を駆動. 内蔵 LiPo は使われず, USB-C 経由なら同時に充電される
- **RoverC が落ちた瞬間**: VSYS_VIN が 5V → 0V に向かう途中で D6 が導通開始 → カメラは内蔵 LiPo (140mAh) で動き続ける. 数秒〜数分の延命

StickC Plus2 も同様に AXP192 が「外部給電 (= HAT 5V) > 電池電圧」のとき自動切替するので, RoverC が落ちても StickC は内蔵 200mAh LiPo で生き残る.

つまり **RoverC = 主電源 + 各小電池に充電を流す親, 子はそれぞれ内蔵電池で短時間 graceful degrade** という構造が**ハードレベルで既に成立している**. これを teleop UI で可視化したのが PR #18 の battery strip + RoverC `isCharging` proxy. RoverC が落ちると `isCharging=False` → "DYING" 表示 → 操縦者は数十秒内に安全停止判断ができる.

### 電流予算

公式データシートに RoverC の boost 出力電流仕様の記載なし. ただし M5 が 2 つの Grove I2C ポートを「expansion module 用」と明示している以上, base 負荷 (motors + StickC) + 数百 mA の expansion 余裕は specced されているはず.

連続電流の試算 (推測, ±20%):

| 負荷 | 連続値 |
|---|---|
| 4 N20 モータ 中速 | ~400 mA |
| 3 カメラ VSYS run | ~600 mA |
| StickC | ~80 mA |
| **base 合計** | **~1.1 A** |
| 3 カメラ充電 (CC phase, **初回 ~30 分のみ**) | +714 mA |

motor stall (4 A peak, 数十 ms transient) は出力キャパで吸収される領域なので連続電流の議論からは除外.

### 充電電流の実態は瞬間的でない

TP4057 は CC-CV 充電で, 「714 mA 連続」状態は **LiPo が空のときの最初 ~30 分だけ**. その後は CV phase で電流が漸減 (238 → 24 mA), 完了後は termination で 24 mA 以下に落ちる. 通常セッション (前回満充電済) では充電電流は数 mA レベル. つまり:

- 初回充電セッション: base 1.1 A + 充電 0.7 A = **1.8 A** (~30 分のみ)
- 2 セッション目以降: base 1.1 A + 充電 ~0.05 A = **~1.1 A** 平常運用

1〜2 A 級 boost に対して初回はギリギリ, 以降は余裕という見立て.

### USB-C 充電 pigtail mod (採用予定)

カメラの内蔵 LiPo は **TP4057 が VUSB_VCC (USB のみ) からしか充電できない** ため, Grove 5V だけで運用していると毎セッション self-discharge し続ける (issue #12 で 0.28V 観測の主因). mod 内容:

1. 100均の充電専用 USB-C ケーブルを剥いて 5V/GND 2 線を引き出し
2. RoverC の Grove 5V (or Servo V) から 3-way 分岐
3. 各カメラの USB-C ポートに食わせる
4. 内蔵 LiPo が常時充電 + schottky-OR battery sharing topology が機能

過剰な保護回路 (直列抵抗 / polyfuse / TP4057 R30 swap) は **基本不要**. base 負荷に Grove I2C 経由で 600 mA 既に流していて RoverC が動いている以上, 同じ等価負荷を USB-C 経由で追加するだけ. ピグテイルの物理的な耐絶縁を確保するために **半田部に熱収縮 + ホットボンド**で機械保護する方が, polyfuse 入れるより実用的なリスク低減になる.

### 必要な検証

- **初回充電セッション**: 全カメラ空状態で USB-C pigtail 接続. 30 分間 RoverC Grove 5V を multimeter で連続測定. sag しなければ完了, 以降この件は気にしない. sag (5V → < 4.5V) したら下記の運用ルールに切替

### sag が出た場合の運用 fallback (zero-mod)

- **充電ローテ運用**: 同時通電する pigtail は 1 本だけ. teleop UI の battery widget で最低電圧のカメラに繋ぎ替え. 走行中は全 pigtail 抜き
- **オフローバー充電**: pigtail 全廃. セッション前後に各カメラを USB ハブで個別充電. ハード変更ゼロ, 手間は増える

### TP4057 R30 mod (基本は不要だが SMD work 楽しいなら)

各カメラ基板を開けて R30 (5.1 K) を 10 K – 15 K に交換すれば ICHRG が 238 mA → 80 – 120 mA に低減. 3 台分でも 240 – 360 mA. **初回充電セッションの sag リスクをほぼゼロにする予防的 mod**だが, 上記の検証で問題が出なければ実施不要.

---

## タイムライン

学生作業（CLAUDE.md「学生作業の年間スケジュール」）との整合確認用：

| 段 | 完了 | 学生側で前進可能になるもの |
|---|---|---|
| 段1 | 2026-04 | Python クライアント側の WASD UI（学生が複数オペレータ UI のベースに発展可） |
| 段2 | 2026-04 | 単眼画像での視覚的タスク試行 |
| 段3 | TBD | 時刻整合データ取得 |
| 段4 | 2026-04（部分） | ステレオデータ取得（校正手順は TBD） |
| 段5 | TBD | プラットフォーム全部 |

---

## リスクと escalation 経路

### Grove 5V 容量未確定
- 段1〜段3 で実測。不足時はバッテリ直タップ + カメラ側 boost コンバータに escalate
- RoverC バッテリ実装：3.7V 700mAh（16340、Pro 仕様準拠）。同時運用 15〜30 分見込み
- StickC Plus2 内蔵バッテリ：200mAh（無印 80–95mAh の 2 倍以上）→ Plus2 単体駆動時のセッション持続が改善
- 詳細: CLAUDE.md ハード調査結果セクション

### I2C bus 競合
- StickC Plus2 master が RoverC（0x38）+ カメラ（0x40, 0x41）を駆動
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
