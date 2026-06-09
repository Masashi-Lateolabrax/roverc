# 卒研テーマ: 低コスト・高ロバストなメカナム運動キャリブレーション

最終更新: 2026-06-09（前方ステレオ + 下向き魚眼 → 前方単眼 + マーカー構成へ再設計）

## 設計変更メモ (2026-06-09)

実験プラットフォームを **前方単眼カメラ1台 (no fisheye, no stereo)** へ変更したことに伴う本テーマの再設計。**指導者の確認が必要な研究判断を含む**:

- **ground truth 源の置換**: 旧 Tier 1 = 前方ステレオ SLAM の metric ego-motion / 旧 Tier 2 = 下向き魚眼 optical flow。両ハードとも非搭載になったため、**前方単眼カメラ + 既知サイズの fiducial マーカー (ArUco / AprilTag) を solvePnP して metric ego-motion を得る方式**へ統一。単一の前方カメラが Tier 1・Tier 2 両方の計測を担う（リグはむしろ簡素化）。
- **保持する貢献**: intrinsic (床非依存) / contact (床依存) の二段分離と異床面 portability の実証 ((c)(d)) は単一前方カメラでも成立し、本テーマの主貢献として維持する。
- **後退する主張**: 旧 pitch の「fiducial 無し・onboard カメラのみ」((b)) は、metric scale を得るためにマーカーが必要になるため**取り下げ**。マーカーは印刷紙の疎な設置で、Springer 系の常設多眼天井カメラ + ルーム校正に比べれば依然はるかに低インフラだが、「外部インフラ完全ゼロ」ではなくなる点は正直に記す。
- **代替案 (要判断)**: マーカーを使わず **巻尺 + 床テープのみを ground truth** にする選択肢もある（外部インフラ完全ゼロを保てるが、UMBmark 古典に近づき camera-as-feedback の新規性が薄れ、データ密度も落ちる）。本ドキュメントは現状 **前方単眼 + マーカー** を主案として記述。指導者が巻尺主案を採るなら本文を差し替える。

以下本文は上記再設計を反映済み。旧ステレオ/魚眼設計は git 履歴 (〜2026-06-08) を参照。

## 位置付け

学生の卒研主題。algorithmic な novelty は薄い。**方法論的・実証的貢献**として組む engineering paper 型の卒論。長期的な研究プログラム (Mediator 系・ステレオ系等) との接続は post-hoc で付け足し可能で, 本テーマ単体で完結する形.

本ドキュメントは 2026-04-30 のサーベイを踏まえて, テーマの位置・推しポイント・実装フェーズを記す.

## 推しポイント (3 properties)

本研究が提案する手法は次の 3 性質を持つ:

1. **実用的** — 学部生一人で組める. 必要物はロボット + 前方単眼カメラ + 印刷マーカー + PC + 巻尺 + 床テープ. チェックリスト化された手順
2. **低コスト** — モーションキャプチャ・天井多眼カメラ・専用 PCB は不要. 追加ハードは前方単眼カメラ (既にプラットフォーム搭載) と印刷した fiducial マーカーのみ. マーカーは常設インフラではなく印刷紙の疎な設置
3. **高ロバスト** — 床面が変わっても通用. intrinsic (車体固有) / contact (接触固有) パラメータを原理分離し, 前者は床間で移植可, 後者のみ床ごとに再校正

副次的長所 (脚注扱い): 利用可能センサが限られた環境 (encoder 無し / アクチュエータファーム封印 / 整備された実験室がない) でも動く. 本卒研の実演事例 (M5StickC Plus2 + RoverC + 前方 Timer Camera X 単眼) はその典型.

> **補足 (2026-06-09)**: 旧設計は「fiducial 無し・下向きカメラのみ」を低コスト性の核に置いていたが, 単眼化で metric scale 確保のためマーカーを導入したため, 性質 2 から「外部インフラ完全ゼロ / fiducial 無し」の主張は外した. 主貢献は性質 3 (二段分離 + 床移植性) に重心を移す.

## 動機

メカナムロボットの運動キャリブには本質的な**床依存性**の問題がある:

- **ロボット固有 (intrinsic) パラメータ**: ホイール半径, ホイール配置, PWM → トルク写像, モータ間 systematic 偏差. **床に依存しない**
- **接触固有 (contact-dependent) パラメータ**: 摩擦係数, slip rate, ホイール-床相互作用, 路面振動. **床ごとに変わる**

UMBmark 系のオフライン手法は両者を一括同定するため, 別の床に移ると無効化する**環境ロック**を生む. 本研究はパラメータを intrinsic / contact に**分離設計**し, 前者を Tier 1 (機体ごと 1 回) で, 後者を Tier 2 (床ごと 1 回) で分担する. これにより**校正床と運用床の不一致**を吸収する.

加えて, 学部生でも組める手順という制約を満たすため, 校正は**オフライン batch**に限定する (走行中のオンラインフィードバックは扱わない).

## algorithmic novelty について

新規アルゴリズムは狙わない. マーカー検出と pose 推定 (`cv2.aruco` / AprilTag, `cv2.solvePnP`), カメラ intrinsic 校正 (`cv2.calibrateCamera`) はいずれも既存ライブラリを使用. 本研究の貢献は:

- **手順の組合せ**: 限定センサ集合下で intrinsic/contact 二段分離キャリブを成立させる recipe の提示
- **実証**: 異床面 portability を交差検証で示す
- **ドキュメント化**: 他人が follow できる procedure として残す

「algorithmic novelty 薄, 方法論 + 実証 + ドキュメント化が貢献」型の engineering paper として位置付ける.

## Deployment requirements 比較

センサ要求と環境ロックの構造で先行研究と並べると:

| 既往 / 本研究 | ロボット側要求 | 環境側要求 | 環境ロック |
|---|---|---|---|
| UMBmark / Borenstein 1996 | エンコーダ | 校正コース (床) | **校正床ロック** (別の床で精度劣化) |
| Hu 2003 (光学マウス搭載) | 専用光学マウス PCB + 固定焦点光学設計 | 床 (テクスチャあれば任意) | 無 (ただし固定光学系が前提) |
| Deyle 2010 (研究グレードオムニ) | カスタム制御スタック + onboard カメラ + エンコーダ | 研究室床 (任意) | 無 (ただしセンサ集合が広い) |
| **Springer 2020 (天井カメラ系)** | フィデューシャルマーカー貼付 | **天井多眼カメラ + ルーム座標系校正 + 照明制御 + 永続設置** | **部屋ロック** |
| **Lee 2020 (VIWO online calibration)** | エンコーダ + IMU + カメラ | 走行床 (任意) | 無 (ただし encoder 必須, intrinsic と contact を単段で同時推定) |
| **Bittencourt 2015 (床色領域切替)** | エンコーダ | 訓練済み床色クラス | **床クラスロック** (新しい床は再学習) |
| **本研究 (Tier 1 + Tier 2, ともに offline)** | onboard 前方単眼カメラのみ (encoder 無し) | 床 + 印刷 fiducial マーカーの疎な設置 (常設インフラ不要) | **薄い** (intrinsic は移動可, contact のみ床ごとに数十分の再校正 + マーカー再設置) |

本研究のセル: **ロボット側センサが狭い (encoder 無し, 前方単眼のみ) + 環境側要求が軽い (印刷マーカーのみ, 常設設備不要) + 床ごとの再校正コストが二段分離で軽い**. Springer 系の常設天井カメラ + ルーム校正と異なり, マーカーは印刷紙で持ち運び・撤去が容易.

## 既存研究との位置関係

サーベイ済み (2026-04-30, 四回). novelty は薄いが, 次の組合せでの先行例は無い:

```
(a) ホイールエンコーダ無し
+ (b) onboard 前方単眼カメラ + 疎な印刷マーカーを sole feedback (常設外部インフラ無し)
+ (c) intrinsic / contact 分離による offline 二段構造 (機体ごと 1 回 + 床ごと 1 回)
+ (d) 異床面 portability の実証
```

各軸単独の先行研究は存在する. **連立**が学部生レベルで実演されていない点に, ささやかな contribution がある. 主軸は (c)(d) の二段分離 + 床移植性. (b) は単眼化で fiducial を使う形に後退したが, 常設インフラ (天井カメラ・mocap) 不要という低コスト性は維持.

- 既存の online wheel calibration (Lee 2020 VIWO 系) は encoder 必須で単段 filter. 本研究は encoder 無しで二段分離.
- 既存の床依存性対応 (Bittencourt 2015 IROS) は床ごとに全パラメータ再学習. 本研究は intrinsic を Tier 1 で固定し, Tier 2 のみ床ごとに回す.

### 主要先行研究と本研究の差

| # | 著者・年 | 内容 | 本研究との関係 |
|---|---|---|---|
| 1 | Borenstein & Feng 1996 (UMBmark) | 差動駆動の bidirectional-square systematic error 評価 | オフライン土台として引用 |
| 2 | Hu et al. 2003 (Mechatronics) | メカナムの視覚デッドレコ。光学マウス 2 個 | 別センサクラス, 二段分離なし |
| 3 | Censi & Roy 2008 / 2013 (T-RO) | 差動駆動 + センサ位置のオンライン同時校正 | 単段オンライン古典 |
| 4 | Dille, Grocholsky, Singh 2009 (FSR) | 屋外下向き optical flow オドメ | 床面 flow を ground truth に使う系統 |
| 5 | Killpack/Deyle 2010 (IROS) | オムニロボット "Cody" の視覚オドメ + 制御 | エンコーダ持ち, 視覚は augmentation |
| 6 | Bonarini et al. 2005 系 | 光学マウスのオドメ伝統 | センサ系統の citing |
| 7 | **Bittencourt 2015 (IROS)** | 床色 SVM で領域分類 → 領域ごとに係数を持つ online calibration | **床依存性を陽に扱う直接競合**。差別化 = 二段分離 (intrinsic 固定) vs 床ごと全再学習 |
| 8 | **Springer 2020 (IJCAS)** | 外部多眼天井カメラを sole feedback、エンコーダ無しでオムニ制御 | 環境ロック型。差別化 = onboard sensor のみで運用可 |
| 9 | **Lee, Eckenhoff, Geneva, Huang 2020 (IROS)** | VIWO with online calibration。intrinsic + extrinsic 同時推定 | **思想的に最も近い理論先行例**。差別化 = (i) encoder 必須でない / (ii) 二段分離 |
| 10 | Sousa et al. 2022 (JIRS, OptiOdom) | 任意 steering geometry の generic offline calibration | OptiTrack ground truth = 校正室ロック前提。比較対象 |
| 11 | Lin et al. 2022/2023 (MDPI) | 下向き fisheye → 平面像化 → KLT で VO | Tier 2 ハード構成と最も近い。置換型なので競合せず補完 |
| 12 | Cabrera-Ponce et al. 2024 (MDPI) | 下向き単眼で hybrid VO | 置換型, 校正補助ではない |
| 13 | De Giorgi et al. 2024 (MDPI Robotics) | 差動駆動の online calibration in low traction | Censi 系の延長, 差動駆動 |
| 14 | Manzl et al. 2024 (Mech. Mach. Theory) | メカナム orthotropic friction model + 実験検証 | **intrinsic / contact 分離の物理 justification** |

### 必読・全文取得すべき文献

- **Lee et al. 2020 (IROS)** — 最近接理論先行例
- **Bittencourt et al. 2015 (IROS)** — 床依存性を扱う直接競合
- **Manzl et al. 2024 (Mech. Mach. Theory)** — Tier 1/Tier 2 分離の物理 justification
- **Sousa et al. 2022 (JIRS, OptiOdom)** — Tier 1 比較対象
- **Lin et al. 2023 (MDPI Appl. Sci.)** — Tier 2 直接的先行例

### リスクと軽減

- **Lee 2020 VIWO** は思想的に近いが encoder 必須・単段 filter. 本研究との重なりは限定的
- **Bittencourt 2015** は床依存性を扱う先行例. 二段分離 (intrinsic 固定) で素朴 multi-region と差別化
- **Hu 2003 + Deyle 2010** はセンサ集合・前提が異なるため直接競合から外れる

## 卒論 Intro 用 pitch (1 文)

> A practical, low-cost, and robust offline calibration recipe for mecanum mobile robots, using only an onboard front-facing monocular camera together with sparse printed fiducial markers for metric ground-truth ego-motion — built on a principled separation of robot-intrinsic (floor-independent) and contact-dependent (floor-specific) parameters into a two-tier offline procedure, validated for portability across multiple floor surfaces.

## キャリブの設計空間とスコープ

センサ構成に対する設計空間 (2026-04-30 対話で整理):

| モード | センサ | 計算 | 難易度 | 数学 |
|---|---|---|---|---|
| (i) 常設下向き魚眼 | 車載魚眼 | PC or ESP32 | 低 | 床面 optical flow 直比例 |
| (ii) **キャリブ時のみ装着の下向きカメラ** | 一時マウント治具 | PC or ESP32 | 低 | (i) と同一 |
| (iii) 元から下向きカメラ持ち | デバイス側 | — | 低 | (i) と同一 |
| (iv-stereo) **正面ステレオ + SLAM** | 車載ステレオ | PC | 中 | Stereo SLAM (ORB-SLAM3 等) で metric ego-motion |
| (iv-mono) 正面単眼 + マーカー | 単眼 + AprilTag/ArUco | PC or ESP32 | 中 | `solvePnP` 再投影誤差 |
| (v) 正面単眼のみ | 単眼単独 | PC | 高 | monocular SfM, scale ambiguous |

本卒研で扱うモード (2026-06-09 単眼化後):
- **(iv-mono) 正面単眼 + マーカー (solvePnP で metric ego-motion)** — 主案. 単一の前方カメラが Tier 1・Tier 2 両方の ego-motion 計測を担う
- **巻尺 + 床テープ** — sanity check 兼, マーカー不採用時の代替 ground truth

(ii) 下向き魚眼・(iv-stereo) ステレオは現行リグ非搭載のため対象外。(i)(iii) 下向きカメラ系と (v) 単眼単独 (scale ambiguous, 学生範囲外) も本筋ではないが, 設計空間としては議論で触れる。

実装は階層化 (intrinsic / contact 分離設計を反映, ともに offline batch). 単眼化後は **前方単眼 + マーカー (solvePnP)** が Tier 1・Tier 2 共通の ego-motion ground truth 源:

| 階層 | 担当パラメータ | 床依存度 | 計算場所 | フィードバック源 | 精度目標 | 運用 |
|---|---|---|---|---|---|---|
| **Tier 1** | intrinsic (ホイール半径・配置, PWM→運動写像, モータ偏差) | **床非依存** | PC | 前方単眼 + マーカー solvePnP の metric ego-motion を基準床で ground truth に | mm 級 | offline batch, 機体ごとに 1 回 |
| **Tier 2** | contact-dependent (slip, 摩擦, 路面振動) | **床ごとに再校正** | PC | 同じ前方単眼 + マーカーで床ごとに実 ego-motion を測定 (指令 vs 実測の差に slip が現れる) → 補正テーブル fit | cm 級 | offline batch, 床ごとに 1 回 (数十分) |
| **Tier 3** | クロス検証 | — | PC | 異床面での Tier 1 共有 + Tier 2 差し替えの portability 検証 | — | 卒論の主要評価章 |

**オンライン feedback 制御は本研究のスコープ外** (学部卒の範囲外と判断). 走行中の補正適用は事前に fit した補正テーブルを参照する形に限定.

スコープから除外:
- 時間同期校正, オペレータ入力校正 (それぞれ別のサブテーマ。ステレオ内外部校正は単眼化で対象外)
- 階層化のうち (v) monocular SfM 単独 (scale ambiguous, 学生範囲外)
- ESP32 上での重い視覚処理 (マーカー検出・solvePnP は PC 専任)

### 本質的限界（明示すべき）

前方単眼 + マーカーの solvePnP は **マーカーがカメラ視野内に十分な見えで写っていること**を前提とする:

- ✅ 動作: マーカーが FOV 内・適度な照明・非遮蔽・適度な距離と角度
- ❌ 失敗: マーカーが FOV 外, 強い斜め・遠距離で角点検出が劣化, 暗所・強反射でコントラスト不足, 遮蔽

これは ego-motion 計測のスコープ限界であり, 卒論で明示すべき。運用上は校正コースの起点〜終点に沿ってマーカーを配置し, 走行区間で常に 1 枚以上が見える設計にする (チェックリスト項目)。

> 旧 (下向き魚眼) 設計では「床面に視覚テクスチャがあること (木目・目地・汚れ等; 鏡面・均一塗装で失敗)」が限界だった。マーカー方式はテクスチャ非依存になる代わりにマーカー可視性に依存する, というトレードオフ。巻尺 + 床テープ ground truth はこの限界を持たない (が手動・低密度)。

## 評価軸

学生との対話で確定 (2026-04-30):

- **基本指標**: 1m 移動指令時の到達位置誤差（巻尺 + 床テープで測定）
- **派生指標**:
  - 直進指令での横ずれ
  - 旋回指令での角度誤差
  - 補正あり / なしでの誤差分布比較
  - 異なる作業者 2-3 人 (指導者・学生・研究室メンバー) で同じチェックリストを回したときの結果分散 = **手順の再現性そのもの**
  - セットアップ所要時間
  - チェックリスト遵守 vs 不遵守でのアブレーション

ground truth 取得手段の階層 (2026-06-09 単眼化後):
- **巻尺 + 床テープ**: 手軽、数 mm 精度、学生に優しい (フェーズ初期の sanity check, マーカー不採用時の代替主案)
- **正面単眼 + マーカー solvePnP (Tier 1・Tier 2 共通)**: 既知サイズの ArUco/AprilTag を撮影し `cv2.solvePnP` で metric なカメラ pose → ego-motion を取得。本研究の主要 ground truth 経路。scale は既知マーカー寸法で確定 (単眼単独の scale ambiguity を回避)
- 旧 **正面ステレオ SLAM / 魚眼 VIO** は現行リグ非搭載のため不使用 (git 履歴参照)

## 実装フェーズ（学生作業範囲、年間スケジュール対応）

### フェーズ 1 (4–6 月): 基礎習得 + 物理コース設計

- Python 基礎 + プラットフォーム使用法習得
- StickC スケッチを読む程度の Arduino
- 物理キャリブコースの設計（直線レーン、回転中心マーカー、起点と終点の床テープ）
- ground truth 計測方法の確定（巻尺 + 床テープ運用ルール）

### フェーズ 2 (6–8 月): Tier 1 (オフライン高精度) の確立

- 前方単眼カメラの intrinsic 校正 (チェッカーボード + `cv2.calibrateCamera`)
- ArUco/AprilTag マーカー (既知サイズ) を校正コース沿いに設置, `cv2.solvePnP` で metric なカメラ pose → ego-motion を PC 側で取得
- UMBmark 風コース実装（メカナム向け：直進 + 横移動 + 回転の 3 種）
- PWM 指令 → 実運動マッピングをマーカー solvePnP ego-motion で計測 (何回も走らせてヒストグラム化)
- systematic error の分離と補正テーブル生成
- 補正前後の 1m 誤差比較 (solvePnP 軌跡 + 巻尺で検証)

### フェーズ 3 (8–10 月): Tier 2 (offline contact 補正) の実装

- 同じ前方単眼 + マーカーで床ごとに実 ego-motion を測定 (新たなハードは不要, フェーズ 2 の計測系を流用)
- 一定 PWM 指令で走らせ, 指令運動 vs 実測 ego-motion の対応点を床ごとに収集 (差分に slip / 摩擦が現れる)
- offline batch で contact 補正テーブル fit (最小二乗線形)
- 補正あり / なしの 1m 誤差比較

### フェーズ 4 (10–12 月): Tier 3 クロス検証 + 再現性評価

- **異床面 portability 検証**: 同じ Tier 1 補正テーブルを 3 床面 (タイル / カーペット / 木) で共有, Tier 2 のみ床ごとに再 fit. それぞれで 1m 誤差を比較. intrinsic / contact 分離設計の妥当性検証
- 異なる作業者 2-3 人で同じチェックリストを回す → 結果分散の定量化 = **手順の再現性そのもの**
- セットアップ所要時間の計測

### フェーズ 5 (1–2 月): 卒論執筆

- Intro: 床依存性問題 → 二段 offline 分離による解 → 推しポイント 3 性質
- 関連研究: Lee 2020 (VIWO online calibration), Bittencourt 2015 (床色領域切替), Springer 2020 (天井カメラ系), Hu 2003 (光学マウス系), Manzl 2024 (orthotropic friction) を主軸に positioning
- 手法: Tier 1 (前方単眼 + マーカー solvePnP ground truth による intrinsic fit) + Tier 2 (同計測系で床ごとの contact 補正テーブル fit) + Tier 3 (異床面 portability 検証)
- 結果: 1m 誤差 (補正前後 / 異床面) / 作業者間再現性 / セットアップ時間
- 議論: 二段 offline 分離の他構成への展開可能性, オンライン化の将来課題, post-hoc に上位応用 (Mediator / 配送 / 教育用途等) を任意で接続

## スコープ外 (明示)

- **オンライン feedback 制御**: 学部卒の範囲外. Tier 2 は offline batch 補正テーブル fit に限定
- **EKF ベースのオンライン kinematic パラメータ推定** (Censi & Roy 系): 学生範囲外
- **ESP32 上での重い視覚処理**: マーカー検出・solvePnP は PC 専任 (Tier 1)
- **新規 marker/pose 推定アルゴリズムの発明**: 既存ライブラリ (`cv2.aruco` / AprilTag / `cv2.solvePnP`) を使用
- **時間同期校正, オペレータ入力校正**: 別テーマ, 本卒研では触れない (ステレオ内外部校正は単眼化で対象外)
- **monocular SfM 単独でのキャリブ (設計空間 (v))**: scale ambiguous, 学生範囲外

## 卒論の構造 (単独で完結する形)

- **問題**: 低コストで, 床面を変えても通用する, 学部生でも組めるメカナム運動キャリブの手順は何か
- **目的**: 上記性質を満たす offline 二段キャリブレシピを確立し, 異床面 portability を実証する
- **解**: intrinsic (床非依存) / contact (床依存) の原理分離 + Tier 1 (前方単眼 + マーカー solvePnP ground truth による intrinsic fit) / Tier 2 (同計測系で床ごとの contact 補正テーブル fit) / Tier 3 (異床面検証)
- **成果**: 動く校正系 + 1m 誤差等の精度実測 + 異床面 portability 結果 + 作業者間再現性 + ドキュメント化された手順
- **位置付け**: algorithmic novelty は薄いが, 推しポイント 3 性質 (実用 + 低コスト + 高ロバスト) を満たす方法論的・実証的貢献. 長期応用 (Mediator / 教育用途 / ドローン等) は post-hoc に接続可能

## 関連ドキュメント

- `docs/platform_bringup.md`: 指導者作業のプラットフォーム bring-up 5 段ロードマップ
- `docs/fisheye_vio_roadmap.md`: 魚眼 + IMU VIO による速度推定（魚眼ハードは現行単眼リグ非搭載、将来ライン）
- `docs/async_stereo_interpolation.md`: 別ライン研究プログラム候補（ステレオ前提、進学予定学生向け）

## 参考文献

- Borenstein, J. & Feng, L. "UMBmark: A Benchmark Test for Measuring Odometry Errors in Mobile Robots." (1995)
- Hu, X. et al. "Visual dead-reckoning for motion control of a Mecanum-wheeled mobile robot." *Mechatronics* (2003)
- Lin, P.-C. & Shih, C.-L. (関連メカナムキャリブ拡張)
- Doroftei, I. et al. (関連オムニ車輪校正)
- Censi, A., Franchi, A. et al. "Simultaneous Calibration of Odometry and Sensor Parameters." *IEEE T-RO* (2013)
- Roy, N. & Thrun, S. "Online Self-Calibration for Mobile Robots." *ICRA* (1999)
- Dille, M., Grocholsky, B., Singh, S. "Outdoor downward-facing optical flow odometry." *FSR* (2009)
- Killpack, M., Deyle, T., Anderson, C., Kemp, C. "Visual odometry and control for an omnidirectional mobile robot." *IROS* (2010)
- Bonarini, A., Matteucci, M., Restelli, M. (光学マウスベースオドメ系, 2005 era)
- Springer / IJCAS 2020 "Velocity control with visual feedback" (外部マルチカメラ + エンコーダ無しオムニ制御)
- MDPI Robotics 2024 (差動駆動オンライン校正、低トラクション)
