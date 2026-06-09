# 魚眼カメラ + IMU による速度推定ロードマップ

最終更新: 2026-04-29

> **注記 (2026-06-09)**: 実験プラットフォームは 2026-06-09 に **前方単眼1台 (no fisheye)** へ変更。Timer Camera F（魚眼）は現行リグ非搭載・在庫温存となり、本ロードマップは将来ライン。再開には魚眼カメラの追加搭載が必要。

## 位置付け

本卒研の主題（Mediator プログラム + データ収集基盤）から見ると **副次的な改善経路**。データ品質の評価軸の一つとして「速度推定精度」を持ち、ベースライン Mediator の実験条件としても活きる。

本ドキュメントは Timer Camera F（魚眼、在庫）を RoverC に追加搭載して **単眼 fisheye VIO で速度推定する** 方針のロードマップを述べる。`docs/platform_bringup.md` の 5 段とは独立で、段3（時刻同期）完了後にいつでも着手可能。

## 動機

`feature/telemetry-6dof` で StickC の MPU6886 全 6 軸を PC まで流す経路が確立した。素朴に加速度を二重積分すると車体振動とバイアスドリフトで数秒で破綻する。これは MEMS IMU の本質的限界で、最新研究も以下 3 路線で対処している：

1. **ZUPT 高度化**: エネルギーベース静止検出 + EKF で bias 共同推定。chassis IMU 単独の上限は MEMS で km 当たり数十 m 級（RINS-W, Brossard 2019）
2. **学習型 IMU オドメトリ**: TLIO / IONet / RoNIN は IMU 短時間ウィンドウから変位を回帰。要訓練データ
3. **視覚慣性融合 (VIO)**: 視覚で IMU drift を恒久的に補正。低コストハードで研究グレードに到達できる唯一の経路

本プラットフォームは既にカメラを搭載するため経路 3 が自然。Timer Camera F (OV3660 + ~150° 魚眼) は VIO 用センサとして特性が良く、ステレオ運用の Timer Camera X とは役割分離できる。

## 魚眼を選ぶ理由

- 広 FOV により回転と並進の分離が線形に近づく
- フレーム端に特徴が残るため速い旋回でも tracking 失敗が起きにくい
- モノキュラ + IMU で完結、ステレオ機構不要
- OpenVINS / VINS-Fusion / ROVIO が equidistant / Kannala-Brandt モデルを標準サポート
- EuRoC, KAIST など主要 VIO ベンチが魚眼 + IMU で取られているため文献の手法を直接流用可能

## マウント設計

### 推奨構成: 高所マスト + 軽い俯角

- 取付高さ: 車体上面 + **10〜15 cm**
- 俯角: **15〜25°**（床面が画角下半分、遠景が上半分）
- マスト: アルミロッド + 樹脂ブラケット、**短く太く剛性高く**。10 cm 以下なら振動許容、それ以上は CFRP / 二点支持要
- 給電: USB-C 別電源 or RoverC 5V タップ（電圧降下要実測）

### 代替候補と特性

| 案 | 並進観測性 | 回転観測性 | 振動 | 自己遮蔽 | 用途 |
|---|:-:|:-:|:-:|:-:|---|
| 前方水平 | 弱 | 強 | 低 | 低 | 操縦視点・障害物 |
| 前方俯角直付け | 強 | 強 | 低 | 中 | バランス VIO |
| **マスト高所俯角 (推奨)** | **強** | **強** | **中** | **低** | **VIO 標準** |
| 真下 (Nadir) | 最強 | 弱 | 低 | 低 | 床テクスチャ専用 |
| 真上 (天頂) | 弱 | 強 | 低 | 無 | 屋内 ceiling-PDR |

物理的根拠:
- 並進観測性は近距離特徴の parallax で決まる（床面が画角に入ると optical flow が並進速度に直接比例）
- 回転観測性は遠景特徴の角速度で決まる（壁面・蛍光灯がヨー / ピッチを高 SNR で出す）
- 両方欲しいなら近距離床 + 遠距離壁を同時に画角に入れる構図 = 高所 + 軽い俯角

### Timer Camera F 固有の論点

- レンズ歪みパラメータの出荷時校正なし → 自前でチェッカーボード校正が必須（OpenCV `fisheye::calibrate` または Kalibr）
- VIO 安定性のため自動露出は固定推奨（魚眼は照明ムラを拾いやすい）
- 段2 ステレオで使った Timer Camera X 用スケッチが流用可能な前提（要検証）

## 実装フェーズ

### フェーズ 1: ハード搭載 + 配信確認

- マスト製作と RoverC への取付
- Timer Camera X 用 firmware 流用、HTTP `/jpg` で PC 受信確認
- 振動評価: 既存 6-DoF telemetry の accel z 高周波 PSD でマスト共振点を実測

### フェーズ 2: カメラ校正と時系列データ取得

- 魚眼歪みパラメータ取得（チェッカーボード or AprilGrid）
- 段3 時刻同期（StickC master → カメラへ I2C 配信）と組合せて、画像フレーム + IMU を共通時間軸でログ
- 校正データセット（数分の手押し走行）作成

### フェーズ 3: VIO 立ち上げ

候補ライブラリ:
- **OpenVINS** (RPNG、modular MSCKF、魚眼サポート、ROS 依存軽い) ← 本命
- **VINS-Fusion** (HKUST、stereo / mono、大規模実績、ROS 重め)
- **MSCKF VIO** (KumarRobotics、軽量だが mono-fisheye サポート要確認)

PC で VIO デーモンを走らせ、状態（位置・速度・姿勢・bias）を UDP で teleop に流す構成。teleop の est ベクトルは naïve 積分から VIO 由来に切替。

### フェーズ 4: 評価と統合

- 直線走行・旋回・8 の字での drift 定量化
- ベースライン Mediator 実験での速度推定精度（卒論「データ品質」評価軸の一つ）
- 失敗モード（特徴量乏しい床、強照明、急加速）の整理

## 副次効果

- 操縦者がこの俯瞰映像を見ながら指令を出す UX を Mediator 実験条件に組み込める（視覚情報の Mediator 入力源としての価値）
- 既存ステレオ Timer Camera X は前方水平のまま操縦視点 + 段4 ステレオ深度用、F は VIO 専用、と役割分離できる
- 学生にとってハード作業（マスト製作・校正）が増えるため好み的に整合

## スコープ外（明示）

- 学習型 IMU オドメトリ（TLIO / IONet）の自前訓練 — 訓練データ規模・計算資源不足、別ライン候補
- ESP32 上での VIO 実装 — 計算量的に不可能、VIO は PC 専任
- VIO そのものの新規アルゴリズム研究 — 本卒研の主題ではない、既存ライブラリの統合まで

## 参考文献

- Brossard et al. "RINS-W: Robust Inertial Navigation System on Wheels." (arXiv 1903.02210)
- Liu et al. "TLIO: Tight Learned Inertial Odometry." (arXiv 2007.01867)
- Chen et al. "Deep Learning for Inertial Positioning: A Survey." (arXiv 2303.03757)
- Geneva et al. "OpenVINS: A Research Platform for Visual-Inertial Estimation." ICRA 2020
- Qin et al. "VINS-Fusion: A Robust and Versatile Multi-Sensor Visual-Inertial State Estimator." (HKUST)
