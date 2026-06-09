# roverc — 卒研プロジェクト

## このプロジェクトは何か

学部生（就職予定）の卒業研究のため、M5StickC Plus2 + RoverC（メカナム車）+ PC（Python, LAN経由）を題材に、研究テーマ・実装プラットフォームを構築する。

## Information Architecture

`CLAUDE.md` stays compact. Detail lives in one of the four stores below — when adding information, pick the one whose purpose matches.

- **Docs** — Long-lived knowledge that is expensive to obtain or reproduce, and that collaborators need to share. Code design decisions (settled through dialogue), experiment results (long to run), literature surveys (long to research), development policies (settled through dialogue). *(The plugin is agnostic to the documentation tool — Zola, mdBook, Sphinx, plain markdown in `docs/`, etc. Each project picks its own.)*
- **Memory** (`.claude/memories/` project; `~/.claude/memories/` global) — The AI self-reinforcement and reference system. Entries are markdown files in three subfolders: `static/unfold/` (always injected, with how-to detail), `static/fold/` (always injected as pointer only — for handoff notes, file trees, reference material that should be available every session), `dynamic/` (injected on semantic match with the prompt). One entry = one theme. Global memories cross projects.
- **Skills** (`.claude/skills/<name>/SKILL.md`) — Named bundles of task-specific instructions with tool-permission scoping. Each `SKILL.md` has a `description` explaining what the skill does and `allowed-tools` listing the tools it may use. Examples from this plugin: `commit`, `pull-request`, `issues`, `clean-branch`. **Skills do not always fire on their own**, so a project's `CLAUDE.md` typically keeps a dispatch table (e.g. *"When committing, use `lab-tool-cc-plugin:commit`"*) to reinforce invocation. Those reinforcement entries are load-bearing and belong in `CLAUDE.md` — they are not candidates for offloading.
- **CLAUDE.md** (this file) — Kept compact. Project-specific operating context that must load in every session: what the project is, which stores exist, and a dispatch table pointing to the relevant store or skill for each concern. Behavior-shaping rules belong in Memory, not here.

## ストア / スキル ディスパッチ

- **コミット作成時** → `lab-tool-cc-plugin:commit` を使用
- **PR 作成・操作時** → `lab-tool-cc-plugin:pull-request` を使用
- **Issue 操作時** → `lab-tool-cc-plugin:issues` を使用
- **Memory エントリ操作時** → `lab-tool-cc-plugin:memory` を使用
- **プラットフォーム bring-up 詳細** → `docs/platform_bringup.md`
- **カメラストリーミング debug 知見**（MJPEG / urllib `read1` coalescing / Wire1 self-heal / I2C bus recovery / XCLK 8MHz 等）→ `docs/camera_streaming_lessons.md`
- **別ライン研究プログラム候補（進学予定学生向け）**（ステレオ前提。現行リグ = 前方単眼では非搭載、将来ライン）→ `docs/async_stereo_interpolation.md`
- **魚眼カメラ + IMU による VIO 速度推定ロードマップ**（魚眼前提。現行リグ = 前方単眼では非搭載、将来ライン）→ `docs/fisheye_vio_roadmap.md`
- **RoverC ハード仕様** → `docs/roverc_datasheet.pdf` / `docs/roverc_pro_datasheet.pdf` / `docs/roverc_i2c_protocol.pdf`
- **StickC Plus2 schematic** → `docs/stickc_plus2_schematic.pdf`

## 関係者

- **指導者**（このリポジトリのオーナー）：複雑系・スワーム研究の経験を持つ。研究プログラムの設計者
- **学生**（実装担当）：プログラミングほぼ未経験、ハードよりを好む、就職予定（D進なし）

## 作業分担

### 指導者作業範囲：プラットフォーム bring-up
プラットフォーム基盤（RoverC 遠隔操作 → カメラ単眼 → 時間同期 → 性能評価。旧段4「ステレオ化」は 2026-06-09 の単眼化で廃止）を**指導者単独で完遂**し、学生に検証済プラットフォームとして引き渡す。詳細は `docs/platform_bringup.md`。

### 学生作業範囲：Mediator 層
引き渡されたプラットフォーム上で、複数オペレータ UI、同期記録、ベースライン Mediator 実装、実験運営、データ品質検証、卒論執筆を担当する。詳細は本ファイル「卒研第一セルの構成」「学生作業の年間スケジュール」。

## 関連ドキュメント

- `docs/platform_bringup.md`: 指導者作業のプラットフォーム bring-up 5段ロードマップ
- `docs/async_stereo_interpolation.md`: 別ライン研究プログラム候補（進学予定学生向けに温存。本卒研 = Mediator プログラムとは独立）
- `docs/fisheye_vio_roadmap.md`: Timer Camera F + IMU 単眼魚眼 VIO による速度推定ロードマップ（platform bring-up と独立、段3 完了後に着手可）

## 研究の構造

### 上位フレーム（指導者の長期研究プログラム）

**「競合する複数のプレイヤー間を適切に調停するシステムの構築」**

具体的には：複数オペレータが1台のロボットを同時操縦する際の **Mediator（調停器）** の設計を、Mediator × Domain の二次元経験マップとして埋めていく長期プログラム。

理論的背景は社会選択理論（Arrow の不可能性定理など）。完璧な調停器は存在しないという演繹的事実の上で、**経験的に「良い Mediator の条件」を抽出**することが最終目標。

### 大きなゴール（複数学生・年単位）

**学習型 Mediator（NNベース融合器）の構築と評価**

NNに（状況 + 複数オペレータの指示）を入力し、誰のどの指示にどう重みをかけるか、または最終指令そのものを生成させる。クラウドソーシング集約（Dawid-Skene系）の実時間化・連続値化に相当。

### 今年の卒研（学生の責任範囲）

**学習型 Mediator 研究のためのデータ収集プラットフォーム構築**

学生はML本体には触れず、その**前段階**を担当する。具体的には3層：

1. **データ収集環境の構築手段の確立**
   - 物理タスク環境の標準化（障害物配置、コース、ゴール）
   - 校正手順、再現性のための文書化
   - 成果物：実験プロトコル + チェックリスト + 校正手順書

2. **データ収集プラットフォーム（システム）**
   - 複数オペレータが入力できるUI（ジョイスティック / ブラウザ / キーボード）
   - StickC経由でのRoverC指令送信
   - 同期取りつつ全データ記録（各人の入力、ロボット状態、タスク状況、時刻）
   - ML学習に使える形式での出力（CSV / JSONL / parquet など）
   - 成果物：Pythonライブラリ + UI + ドキュメント

3. **実データの収集と検証**
   - プラットフォームを使った実セッション運営
   - データ品質検証（ノイズ、欠損、同期ずれの定量化）
   - **ベースラインとして固定 Mediator 数種**を実装し、データを揃える（averaging, dominance, voting あたり2-3種）
   - 成果物：データセット + 品質レポート + ベースライン結果

## 卒論の物語

> **問題**：学習型Mediator研究は複数オペレータの高品質な実時間データを必要とするが、既存収集はad hoc・ハプティックデバイス前提で敷居が高く再現性が低い。
>
> **目的**：低コストハード（RoverC級）で、複数オペレータの実時間操縦データを再現性良く収集できるプラットフォームと方法論を確立する。
>
> **成果**：上記3層 + 初期データセット + ベースライン Mediator 結果。
>
> **位置付け**：本基盤は、後続の学習型Mediator研究を可能にする。Mediator × Domain 経験マップ構築の第一歩である。

## 卒研第一セルの構成

### ハード
- RoverC × 1
- M5StickC Plus2 × 1（運用、2026-04-26 確定）
- M5StickC 無印（予備、swap 用）
- M5Stack Timer Camera X × 5（運用1 = 前方単眼 on RoverC、予備4。**2026-06-09 に前方ステレオ2台構成から単眼1台へ変更**）
- M5Stack Timer Camera F × 1（魚眼、在庫。現行リグ非搭載。`docs/fisheye_vio_roadmap.md` の将来ライン用に温存）
- PC × 1（Python開発）
- 入力装置：ジョイスティック or キーボード × 2人分

### 通信
- LAN経由、Python ↔ StickC ↔ RoverC（I2C）
- プロトコルは UDP + msgpack を想定（要再検討）
- 同期精度は明示的に測定対象とする

### 同期方式（前方単眼カメラ + ロボット状態）
**2026-06-09 変更**：前方ステレオ2台構成から前方単眼1台へ変更。カメラ間ペアリング（左右フレームの時刻整合）は不要になり、残る同期課題は **単一カメラフレーム ↔ ロボット状態（指令・telemetry）の時刻整合** のみ（記録データでカメラフレームとモータ指令を揃えるのに必要）。
- **方針：ソフトウェアタイムスタンプ + 有線 I2C 時刻同期**（2026-04-26 確定、単眼でも有効）
- StickC Plus2 を I2C master、RoverC STM32（0x38）に加えて前方カメラ1台を slave として同バスに乗せる構成（カメラ：0x40）。RoverC HAT バス (Plus2 P1 STICKIO: pin 3=G26/SCL, pin 5=G0/SDA、無印 StickC と同一配置、`stickc_plus2_schematic.pdf` で確認済) → RoverC Grove ポート → カメラ HY2.0-4P (GPIO 13=SCL, 4=SDA) で配線、既存 Grove ケーブル流用、追加配線なし（単眼化でケーブルスプライス不要）
- RoverC 公式ドキュメントの対応表記は「StickC / StickC Plus」のみで Plus2 名は未掲載だが、HAT ピン配列が無印と完全一致するため電気的に互換
- StickC は 1Hz 程度で master 時刻（`esp_timer_get_time()`）をカメラへブロードキャスト書き込み
- Timer Camera X はフレームごとに `camera_fb_t.timestamp` を記録、master 時刻オフセットを適用して PC 側でロボット状態と時刻整合
- 同期ジッタは実測して評価軸として報告（卒論の「データ品質」評価項目と整合）
- I2C bus 占有率は 5% 未満想定（モータ 50Hz × 5byte + 時刻 1Hz × 8byte）、競合リスク低
- ベンチテスト計画は `docs/platform_bringup.md` の 段3・段5 を参照
- **却下案**（履歴。ステレオ期の2台精密同期検討だが、設計判断の記録として保持）：
  - GPIO trigger（2026-04-25 却下）：OV3660 FSIN が Timer Camera X 基板上にパッド化されていない、esp32-camera ドライバも外部トリガ非対応（詳細はハード調査結果セクション）
  - ESP-NOW 時刻同期（2026-04-26 却下）：カメラ DMA × ESP-NOW 同居が Timer Camera X で未検証、有線 I2C なら同等以上の精度を低リスクで達成可能と判断。ESP-NOW 関連の調査結果はハード調査結果セクションに参考情報として保持

### ベースライン Mediator（実装対象）
1. 単純平均（averaging）
2. 重み付きブレンド / マスタースレーブ（dominance factor）
3. （余裕があれば）競合検知付き、または投票

学習型 Mediator は本卒研の対象外（次年度以降）。

### 評価軸
- **データ品質**：同期ジッタ（カメラ↔ロボット状態）、欠損率、信号対雑音比（深度精度はステレオ廃止に伴い対象外）
- **プラットフォーム評価**：再現可能性、設定容易性、拡張性
- **ベースライン Mediator 評価**：タスク達成度、対立頻度、脱落耐性

### 開発環境
- **エディタ**：Zed（指導者選好、IDE は使わない）
- **ESP32 ビルド・書き込み・モニタ**：arduino-cli（Arduino IDE 不要）
- **必要コア**：`esp32:esp32`（Espressif、ボードマネージャ URL: `https://espressif.github.io/arduino-esp32/package_esp32_index.json`）
- **必要ライブラリ**：M5Unified（Plus2 を含む StickC 系全対応）、esp32-camera は `esp32:esp32` 同梱
- ボード FQBN：
  - StickC Plus2: `esp32:esp32:m5stack_stickc_plus2`（Espressif core 3.x で追加されている想定。無ければ `m5stack_stickc_plus` で代替し M5Unified の自動ボード検出に任せる。段1 で `arduino-cli board listall | grep stickc` で確認）
  - StickC 無印（予備）: `esp32:esp32:m5stack_stickc`
  - Timer Camera X: `esp32:esp32:m5stack_timer_cam`
- PlatformIO / ESP-IDF への移行は卒研本実装段で再検討、ベンチテスト段では arduino-cli で完結

### 学生作業の年間スケジュール（目安）

前提：プラットフォーム bring-up（RoverC 遠隔操作・カメラ単眼・時間同期・性能評価。旧ステレオ化段は単眼化で廃止）は指導者が完遂し、検証済の状態で学生に引き渡される（`docs/platform_bringup.md`）。学生は Mediator 層に集中する。

- **4〜6月**：Python 基礎 + 提供プラットフォーム使用法習得 + Arduino 軽め（StickC スケッチを読める程度） + 物理実験環境のラフ設計開始
- **6〜8月**：物理実験環境構築（コース、障害物、ゴール、前方単眼カメラマウント） + カメラセットアップと運用手順の文書化
- **8〜10月**：複数オペレータ UI 実装（PC 側、キーボード × 2 を最低構成、ジョイスティック・ブラウザは余力で） + 同期記録パイプライン（Python、全ストリーム CSV/JSONL/parquet 出力）
- **10〜12月**：ベースライン Mediator 実装（averaging / dominance / voting） + 実セッション運営とデータ収集
- **1〜2月**：データ品質検証（同期ジッタ、欠損率、信号対雑音比） + 卒論執筆

## 制約と前提

- 学生はプログラミング未経験 → ML訓練・複雑なシステム設計は不可、API利用や軽量実装中心
- 1年完遂が必須（D進前提の重い理論は不可）
- 「見える形」を重視（実機が動く映像、デモ性のある成果）
- 研究色より工学・実証寄り。論文化は卒研段階では狙わない
- ML本体の試行錯誤は次年度以降の学生 or 指導者の責任範囲

## 設計判断のメモ

- **新規性は卒研段階では「プラットフォームの再現性・低コスト性・ML対応設計」で出す**。手法的新規性は無理に追わない
- **継続性はストーリーで引き寄せる**。「学習Mediatorに進める基盤を作った」が次の学生・研究者の興味を引く
- **卒論Introには上位抽象（Mediator設計の一般問題）と長期ゴール（学習Mediator）を置き、本文は基盤構築と測定**に集中
- 学生の就職向けには「データ収集基盤の設計・実装」「実時間ロボットシステム構築」として打ち出せる

## 既存研究の状況（要点）

詳細サーベイ済み。要点：

- **Multi-Operator Single-Robot (MOSR) teleoperation** は2009年以降成熟（Feth, Khademian, Sirouspour 系）
- **集約方式の比較**は Salam et al. (AAMAS 2015) と Nguyen et al. (HRI 2025) で部分的に既出
- **Tele-Actor / Spatial Dynamic Voting** (Goldberg, ICRA 2002) が投票型集約の源流
- **Policy blending formalism** (Dragan & Srinivasa, IJRR 2013) が融合の数学的雛形
- **Takagi et al. (eLife 2019, 東工大)** が3-4人ハプティック協調の決定的実証（日本発）
- **直接競合**：Nguyen et al. HRI 2025（信頼度共有1方式のみ → Mediator多様性で差別化可）
- **AF447 BEA報告書**：averaging Mediator失敗の実例として Intro 必須
- **空白セル**：Mediator × Domain の系統的経験マップ、社会選択理論ベースMediatorの実時間制御への翻訳、低コスト実証プラットフォーム
- **本卒研の position**：学習型Mediator研究のための基盤構築。直接競合なし

### 必読5本（学生に渡す）
1. Feth et al. (2009) — MOSR用語と基本構図
2. Goldberg & Song (ICRA 2002) — Tele-Actor / SDV
3. Salam et al. (AAMAS 2015) — 集約方式経験比較（最近接競合）
4. Dragan & Srinivasa (IJRR 2013) — policy blending formalism
5. Takagi et al. (eLife 2019) — 日本発、ハプティック多人数協調

加えて Losey et al. (2018) Appl. Mech. Rev. のarbitrationレビュー、AF447 BEA報告書。

### 融合方式ごとの先行研究（卒論本文・関連研究節で使う）

| 融合方式 | 代表研究 | 備考 |
|---|---|---|
| 算術平均 | Airbus サイドスティック / AF447 BEA 報告書 | 失敗事例として強い |
| 重み付き連続ブレンド（人間-人間） | Khademian & Hashtrudi-Zaad (IEEE/ASME 2011, T-RO 2013) | dominance factor α |
| 重み付き連続ブレンド（人間-AI） | Dragan & Srinivasa (IJRR 2013) | policy blending formalism、Mediator の数学的雛形 |
| 部分空間分割 | Malysz & Sirouspour (IJRR 2011) | projective force mapping |
| 空間動的投票 | Goldberg & Song (ICRA 2002) | Tele-Actor / SDV |
| 投票方式の経験比較 | Salam et al. (AAMAS 2015) | Leader / Average / Median |
| ハプティック機械結合 | Takagi et al. (eLife 2019) | 3-4 人結合で性能向上 |
| 無秩序 vs 多数決 | Twitch Plays Pokemon 系研究 | anarchy vs democracy 経験比較 |
| 信頼度重み融合 | Nguyen et al. (HRI 2025) | 自己申告信頼度、N=100 実験、直接競合 |
| トークン受け渡し | da Vinci dual console 関連 | 完全切替型 |

### ロボティクス全般トレンド（卒研スコープ外、Intro の背景・対比に使えるかも）

サーベイ済み（2026-04 時点）。本卒研は採用しないが、選定理由を残す：
- VLA / 基盤モデル：学部生範囲外（資源・経験不足）
- ヒューマノイド：ハードが届かない、指導者観点で短命と判断
- 模倣学習 / 拡散ポリシー：学生のCS経験で実装重い
- モバイルマニピュレーション：アーム無し、複雑系から遠い
- Sim-to-Real：学生の興味として却下
- 触覚センシング：学生の興味として却下
- スワーム：指導者の経験 gap が大きく継承不可
- エッジAI / TinyML：学生の興味として却下

## ハード調査結果（実装時に参照）

### M5StickC Plus2 の外部 GPIO（運用機）
- ESP32-PICO-V3-02、Flash 8MB + PSRAM 2MB、バッテリ 200mAh
- 内部 I2C（IMU、RTC、PMU 等）: SCL=GPIO 22, SDA=GPIO 21（M5Unified `In_I2C`）
- Grove ポート（Port A、HY2.0-4P）: SCL=GPIO 33, SDA=GPIO 32（M5Unified `Ex_I2C` 標準割当）
- HAT 8ピンヘッダ（P1 STICKIO）配列：pin1=GND, pin2=5VOUT, pin3=G26, pin4=G36, pin5=G0, pin6=BAT, pin7=3V3, pin8=5VIN（schematic 確認済、無印 StickC と同一）
- TFT で占有: G15, G13, G14, G12, G5, G27（無印より画面が大きく G13 が使えなくなった点注意）
- RoverC HAT 互換：pin3 G26=SCL / pin5 G0=SDA で動作、jumper 切替なし

### M5StickC無印 の外部 GPIO（予備機）
- Grove ポート（4ピン HY2.0）: GPIO 32, GPIO 33（+ 5V/GND）— 半田なし
- 底面 8ピン HY2.0 HAT: 同 GPIO 32/33 を共有 + 追加ピン（G0, G26, G36, G25）
- 内部空き GPIO: 0, 26, 36（半田作業必要）
- 外部に出ているのは GPIO 32, 33 の2本のみ
- RoverC HAT は 無印で G26=SCL, G0=SDA 接続が定石

### M5Stack Timer Camera X
- センサ: OV3660、3MP（最大 2048x1536）
- ESP32-D0WDQ6-V3 + 8MB PSRAM
- BM8563 RTC 内蔵（低消費電力スリープ用）
- 底面 HY2.0-4P ポート: SCL=GPIO 13, SDA=GPIO 4, 5V, GND
  - **半田なしで GPIO 4/13 にアクセス可能**
  - I2C ラベルだが ESP32 側で汎用 GPIO として転用可能
- カメラドライバ占有 GPIO: XCLK=27, SCCB=25/23, RESET=15, データ=32/35/34/5/39/18/36/19, VSYNC=22, HREF=26, PCLK=21
  - **GPIO 4/13 はカメラに使われていない、汎用入力として使える**

### OV3660 外部トリガモード（不可確認済 2026-04-25）
- Timer Camera X 公式 schematic（M5TimerCAM PDF）確認結果：**OV3660 FSIN ピンが基板上にパッド化されていない**。基板改造でも引き出し不可
- espressif/esp32-camera ドライバ：`sensors/ov3660.c` `ov3660_regs.h` に FSIN/trigger/strobe の記述ゼロ。Issue #192（"Precise Frame Sync with 2 ESP32 Cameras"）はメンテナ me-no-dev が「不可能」コメントで stale
- HY2.0 ポートの GPIO 4/13 は ESP32 にのみ接続、OV3660 とは絶縁
- 結論：ハードウェア外部トリガによる同期は本ハード構成では実現不能
- 採用する代替：ソフトウェアタイムスタンプ + ESP-NOW 時刻同期（同期方式セクション参照）

### ソフトトリガ精度（参考）
- センサは XCLK でフリーラン、`esp_camera_fb_get()` はキューから完了済みフレームを返すだけ（撮影起動ではない）
- 取得モード：`CAMERA_GRAB_LATEST` で常に最新 N 枚を保持
- （ステレオ期の課題）2台のカメラの VSYNC 位相は独立、同時 `fb_get()` してもフレーム周期（30fps で ~33ms）以内のズレが発生 → 単眼化でカメラ間ペアリングは不要
- 解決：フレームごとに `camera_fb_t.timestamp`（VSYNC 直後のタイムスタンプ、`struct timeval`）を記録、後処理でロボット状態と最近接整合（ステレオ期は左右フレームの最近接ペアリングにも使用）

### 関連プロジェクト
- **espressif/esp32-camera Issue #192** "Precise Frame Sync with 2 ESP32 Cameras" — 2台精密同期の議論（stale）
- **ESPNowCam** (hpsaturn) — ESP-NOW + WiFi-raw のストリーマ、1:N ブロードキャスト。**対応ボード一覧に Timer Camera X は含まれず**（FreenoveS3, XIAO S3, M5UnitCamS3 等の S3 系中心）
- **PanoCama** (Hackaday) — デュアル ESP32-CAM のステレオパノラマ + OpenCV disparity
- **Stereo Depth Perception on ESP32 S3** (Hackster) — ESP32-S3 単体 2 カメラ
- Timer Camera X 専用のステレオ・複数台同期事例は調査範囲では見つからず

### ESP-NOW（時刻同期プロトコル）
- 2.4GHz 無線ハードウェアを WiFi と共有するが、AP 接続・IP スタックは不要。WiFi ドライバの中の一機能として実装
- 250 バイト/パケット上限、broadcast または MAC 指定 unicast、典型遅延 数百μs〜数ms
- M5StickC 系（Plus 含む）：**動作確認済み**（先例：teastainGit/RoverC-StickCPlus-ESP_NOW-Remote-Control、vkichline/BugController）。Plus2 は同 ESP32 系 SoC で原理的に動作可能、参考としてのみ
- Timer Camera X：**未検証**。ESPNowCam 対応一覧外、カメラ DMA × WiFi 干渉報告あり（espressif/esp32-camera issue #620、`fb_count=2` で回避）
- API は `WiFi.mode(WIFI_STA)` で無線部を起こし `esp_now_init()` 呼ぶ流れ。AP 接続なしでも動く
- AP 併用時の制約：ESP-NOW チャネルは AP と同一、modem-sleep でパケット落ちあり（Espressif FAQ 明記）
- 既存ライブラリ：ESPNowTimeSync（jensb1、作者主張 ±10〜50μs、第三者測定なし）、ESPNowMeshClock（Hemisphere-Project）、Espressif 公式 ESP-NOW サンプル
- ハードタイムスタンプ取得不可：`esp_now_register_send_cb()` / `recv_cb()` は WiFi タスク経由の呼び出し、電波送出/受信の物理瞬間は取れない。コールバック内 `esp_timer_get_time()` が最早取得点

### camera_fb_t タイムスタンプ
- `esp_camera.h` の `camera_fb_t` 構造体に `struct timeval timestamp` フィールドあり
- 値：「フレームの最初の DMA バッファが書き始められた時刻」（VSYNC 直後）、起動からの経過時間
- ロボット状態（StickC 側 millis 系）と比較するには共通時計（**有線 I2C で StickC から配信されるマスタ時刻**）への変換が必要（ステレオ期は複数機間比較にも使用）

## 検討から外したテーマ（参考）

却下済みなので再提案不要：
- Sim-to-Real、触覚センシング、スワーム、エッジAI（学生個人の興味として却下）
- ヒューマノイド（短命と判断）
- モバイルマニピュレーション（複雑系から遠い）
- VLA・拡散ポリシーの本格学習（学生のCS経験不足で実装不可）
- World Model / Free Energy Principle / 力学系解析（理論重すぎ、D進前提）
- スワーム発展（指導者の経験との gap が大きすぎて継承不可）
- Embodied Cognition（アリだが資料更新コスト・engagement リスクあり）
- Physical Reservoir Computing（ハードより過ぎ）
- 時刻同期＆遅延特性研究単独（理論寄り過ぎ、地味 → ただしデータ品質測定として組み込み）

## まだ決まっていないこと

- ベースラインMediator の最終本数（2 or 3 or 4）
- 入力装置の選定（ジョイスティック種別、ブラウザUIの有無）
- 被験者実験（IRB範囲内 or 自己実験のみ）の有無 — 自己実験＋研究室メンバーで回す方針が現実的
- 学生のPython学習リソース・ペース
- データセットの公開方針（ライセンス、形式、公開先）
- 上位研究プログラム（Mediator × Domain マップ）として指導者が公式に進めるかどうか

## エンジニアリング作業時の方針

- 直接答えてから補足する（質問にいきなり長い前置きを置かない）
- 学生作業範囲かどうかを意識して提案する（指導者作業 / 学生作業を区別）
- ハード触れる作業を優先候補に入れる（学生の好み）
- 実装提案では「未経験者でも踏める階段」になっているかを意識する
- データ品質と再現性を常に評価軸に含める（収集基盤としての価値の核）
