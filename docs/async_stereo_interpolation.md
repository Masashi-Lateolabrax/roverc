# 非同期ステレオ + 補間方式特性化プログラム（候補）

**位置付け**：本リポジトリ（roverc）の主題である Mediator プログラムとは **独立した別研究プログラム** として温存する。現 B4 学生（就職予定・ハード寄り）には重く、進学予定学生 or 多年度多人数体制が前提。本ドキュメントは将来該当学生が現れた時の出発点。

最終更新：2026-04-26

---

## 1. 上位フレーム

低コストハードでハードウェア同期不可能な複数カメラ系から、生成モデル / NN による per-camera 時間補間を介して有用な深度情報を取り出す手法を、**精度 × モデルサイズ × エッジ適用性** の 3 軸で網羅的に特性化する。

長期ゴール：補間モデルの「**サイズ感の決定マップ**」を構築し、ESP32-S3 級〜Jetson 級〜PC 級それぞれで実用可能な depth pipeline の境界を経験的に確定する。

---

## 2. 研究問題

```
Cam1 [I_t1, I_t2] ─→ NN interpolator ─→ I_T^Cam1 ┐
                                                    ├─→ Stereo Depth at time T
Cam2 [I_t3, I_t4] ─→ NN interpolator ─→ I_T^Cam2 ┘

t1 < T < t2,  t3 < T < t4
```

各カメラの補間器は **per-camera blind**（他方カメラの画像を見ない）で動作する。

問い：
- 補間器ファミリ（古典 optical flow / RIFE / FILM / IFRNet / EMA-VFI / video diffusion）と同期ジッタ Δt・シーン運動 v の組合せは深度誤差 ε にどう効くか
- per-camera blind 制約下でどこまで精度が出るか
- 精度 vs モデルサイズ vs 推論コストのフロンティアはどこに引けるか
- エッジ実装（量子化・蒸留）でどの境界が生き残るか

---

## 3. 既存研究の位置（必読）

### 直接の架構先行研究
- **Zhou, C. & Tao, H. "Dynamic Depth Recovery from Unsynchronized Video Streams." CVPR 2003**（IEEE doc 1211490）
  - 提案アーキテクチャと同じ 3 段：時刻オフセット推定 → 同期ペア合成 → ステレオ計算
  - 補間は古典 optical flow ベース
  - **本プログラムの基礎参照**として扱う。敵ではなく出発点

### 理論側
- **Höpken, M. & Grinberg, M. "Modeling Depth Estimation Errors for Side Looking Stereo Video Systems." IEEE IV 2006**（Fraunhofer, EU APROSYS）
  - 同期誤差 → 深度誤差の解析モデルを車載ステレオで構築
  - Δt-v-ε マップの理論的雛形

### 直近の近接研究
- **Seyfu, A. & Yang, R. "A Stereo Synchronization Method for Consumer-Grade Video Cameras." Sensors 25(17):5535, 2025**
  - 消費者向けアクションカメラ 2 台、ハード同期なし
  - 粗フレーム同期 + 多項式サブフレーム補間で再同期、変位計測で評価
  - ESP32 級より 1 段上のコスト帯。**精読必須**
- **Ding et al. "SEVFI-Net: Video Frame Interpolation with Stereo Event and Intensity Camera." IEEE TPAMI 2024**（arXiv:2307.08228）
  - event カメラ + RGB カメラの async stereo VFI
  - 本プログラムは RGB-RGB 想定なので直接競合ではない、隣接
- **4DSloMo (SIGGRAPH Asia 2025, arXiv:2507.05163)**
  - 意図的 stagger 撮影 + video diffusion で 4D Gaussian artifact 修復
  - 「故意 async + 神経モデル後処理」の精神は最も近い 2020 年代論文
  - 対象は 4D 復元、stereo depth ではない

### Rolling shutter stereo（隣接数学）
- Saurer et al. ICCV 2013
- Fan & Dai T-PAMI 2021（arXiv:2003.10866）
- Lao et al. arXiv:2006.07807
- 行間 Δt の数学が inter-camera Δt にそのまま転用可

### 比較対象になる VFI ファミリ
- RIFE（Huang et al., ECCV 2022）
- FILM（Reda et al., ECCV 2022）
- IFRNet（Kong et al., CVPR 2022）
- EMA-VFI（Zhang et al., CVPR 2023）
- 拡散ベース：MCVD、LDMVFI 等
- 古典 optical flow（Farneback、TV-L1）= ベースライン
- 軽量 / エッジ向け：蒸留版・量子化版

---

## 4. 評価軸

### 4.1 精度軸：Δt-v-ε マップ
- 横軸：同期ジッタ Δt（実測 ESP-NOW + camera_fb_t.timestamp ベース、または合成データで制御）
- 縦軸：シーン/カメラ運動 v（並進・回転を分離）
- 値：深度誤差 ε（GT に対する RMSE / median error / outlier ratio）
- 各 (Δt, v) セルで補間器ファミリ別の値をマップ化

### 4.2 サイズ軸：精度フロンティア
- パラメータ数、FLOPs、メモリフットプリント
- 同精度を達成する最小モデル
- パレート曲線

### 4.3 エッジ軸：実機適用性
- PC 推論レイテンシ
- Jetson 級でのリアルタイム性
- ESP32-S3 級への蒸留・量子化可能性
- 電力・熱制約

---

## 5. 卒論物語のテンプレート

> **背景**：Zhou & Tao (2003) は非同期ステレオを「per-camera 時間補間 + 同期ペア合成 + ステレオ計算」の 3 段で解く architecture を確立した。しかし当時の補間手法は古典 optical flow に限られ、22 年経った現在まで **補間器選択の系統評価は行われていない**。
>
> **問題**：現代の NN ベース VFI（RIFE / FILM / IFRNet / video diffusion 等）は外挿能力・遮蔽処理・大運動耐性で古典フローを大きく上回るが、これらが Zhou-Tao 架構の補間スロットに入った時の depth 推定特性は未知。低コスト機材・低速ロボ regime では特に未探索。
>
> **目的**：補間器選択 × 同期ジッタ × シーン運動 → 深度誤差の関係を、ESP32 級の実機で系統測定する。さらに精度 × サイズ × エッジ適用性の 3 軸でフロンティアを描く。
>
> **貢献**：(1) 公開可能な低コスト async stereo bench、(2) 補間器ファミリ別 Δt-v-ε マップ、(3) 古典フロー（Zhou-Tao 2003 ベースライン）vs NN VFI の比較、(4) サイズ-精度フロンティア、(5) エッジ実装可能性の評価。

---

## 6. 多年度多人数プログラム分解

| 段 | 担当 | 主要成果物 |
|---|---|---|
| **B4 (第1世代)** | ハード構築 + データ収集 + 古典 + 軽量 NN ベースライン | 公開ベンチ + データセット + Δt-v-ε マップ初版 |
| **B4 並行 / M1** | 補間器ファミリ網羅比較、精度軸 | 「Zhou-Tao 架構で最強の NN VFI」決定 |
| **M2** | 蒸留・量子化、サイズ軸 | サイズ-精度フロンティア |
| **D** | エッジ実装（ESP32-S3 / Jetson 級）、実機ロボ知覚有用性検証 | エッジ深度 pipeline + 統合論文 |

各段で独立論文化が成立。最終 D 論文で統合。

---

## 7. 投稿先候補

| 段階 | 候補 |
|---|---|
| 国内速報 | MIRU、SSII、RSJ |
| 国際 workshop | ICRA / IROS workshops、CVPR / ICCV workshops |
| 国際本会議 | 3DV、IROS、ICRA、CVPR/ICCV |
| 国際ジャーナル | IJCV、CVIU（精度寄り） / IJRR、RA-L（ロボ寄り） / IEEE T-IE、IEEE IoT-J（エッジ寄り） |

---

## 8. ハード前提（roverc 既存資産から流用可）

- M5StickC × 1（時刻マスタ、ESP-NOW broadcast）
- M5Stack Timer Camera X × 2（ステレオ運用、予備 3 台あり）
- M5StickC × RoverC（メカナム車）= データ収集用低速ロボ
- ESP-NOW + camera_fb_t.timestamp ベースの時刻同期
- 詳細は `../CLAUDE.md` のハード調査結果参照

別ハード（Jetson、ESP32-S3 評価キット、より高解像度カメラ等）は M/D 段階で必要に応じ追加。

---

## 9. リスクと未解決事項

### 9.1 サーベイ未網羅領域
- CVPR/ICCV/ECCV/3DV/ICRA 2024–2025 全本文ベタ読み未実施
- ワークショップ論文・修論・MIRU 等で誰かが部分的にやっている可能性あり
- VIO/SLAM 文脈の learned 時間 upsampling 系未チェック
- 日本語論文未チェック
- **本プログラム実質開始時に再サーベイ必須**

### 9.2 「so what」の弱さ
- 「RIFE のほうが optical flow より良かった」だけだと薄い
- regime 別条件分け（屋内 vs 屋外、高速 vs 低速、訓練分布内外）で深さを出す必要

### 9.3 NN VFI の訓練分布外破綻仮説
- 屋内 RoverC の遅速 + メカナム特有の斜行は汎用 VFI 訓練データに少ない
- 想定外運動で破綻する可能性あり、**負の結果でも価値**

### 9.4 GT 深度取得手段未決
- LIDAR、構造化光、Aruco マーカ + 既知配置、シミュレーション併用 etc
- B4 第1世代の最初の判断事項

### 9.5 並行体制の前提
- 多年度多人数前提なので、配属見込み・プログラム継続意志を指導者側で固める必要

---

## 10. Mediator プログラムとの関係

完全独立。本リポ（roverc）は Mediator プログラム第一セルとして運用中。本プログラムが立ち上がる際は別リポへの分離を推奨。

ただしハード資産（RoverC、Timer Camera X、StickC、ESP-NOW 同期実装、camera_fb_t.timestamp 取得コード等）は共有可能。Mediator プログラムが先行構築するハード基盤を、本プログラムは流用できる。
