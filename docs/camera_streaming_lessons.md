# カメラストリーミング debug 知見

stage-2 (Timer Camera X JPEG over WiFi) と stage-3 (StickC I2C-mediated 双眼) の bring-up と並走で、PC クライアント側の MJPEG 化を試した際に得た知見集。再発時の起点として残す。

## 真因だった項目

### Python urllib の "read coalescing"

- `urllib.request.urlopen` の HTTPResponse は socket を `BufferedReader` で包む
- `r.read(N)` は複数の `recv()` 結果を 1 つにまとめて返すことがある → MJPEG パースで「800ms 沈黙 → 10 フレーム同時到着」というバースト擬似現象が生じる
- **対策: `r.read1(N)`**（単発 syscall で即返す）。`src/crover_mod/camera.py` の `CameraStream._read_into` で採用済
- 切り分けには `socket.socket()` で直接 `recv()` して比較するのが決定打。生 socket レベルでは元から 80ms 間隔（12 fps）で来ていた

### `set_framesize` 単体は DMA バッファ越境を起こす

- `esp32-camera` のフレームバッファサイズは `esp_camera_init()` 時の `frame_size` で固定確保
- `s->set_framesize(s, fs)` で実行時に拡大方向（QVGA → UXGA など）に変更するとセンサだけ大きいフレームを吐き、DMA がバッファ範囲を超えて書き込み → 任意メモリ破壊
- **対策: `esp_camera_deinit()` → 新 `frame_size` で再 init**。`arduino_src/lib/camera_common/src/camera_main.cpp` の `camera_reinit()` で実装済

### OV3660 の XCLK 20MHz は同機の 2.4GHz WiFi に干渉

- 既知問題 ([espressif/arduino-esp32 #5834](https://github.com/espressif/arduino-esp32/issues/5834))
- **8MHz** に下げると干渉が消える。代償は理論最大 fps 低下（QVGA で 30 → 12-15 fps 程度）
- `arduino_src/lib/camera_common/src/camera_main.cpp` の `fill_camera_config` で 8MHz 採用済

### sync WebServer は単一スレッド

- arduino-esp32 の `WebServer` は handleClient() がブロッキング、persistent な `/stream` ハンドラが走っている間、他のエンドポイント（`/jpg`, `/control`, `/`）は応答しない
- 加えて main loop も止まるので、`announce` / `check_camera_health` / `check_i2c_slave_health` が走らない
- **対策: handle_stream 内で `esp_task_wdt_reset()` 自前呼び出し + 1Hz tick で health check と `update_i2c_response` を走らせる**
- `/control` は stream 中は届かないので、クライアントは設定変更時に stream を一度切る運用にする（今は使い分け不要）

## 自己回復パターン（カメラ firmware）

| 症状 | 検知 | 対処 |
|---|---|---|
| main loop hang | task watchdog 30s | panic reset → 起動時に `reset_reason=TASK_WDT` |
| camera DMA stall | health check 5s 周期で `esp_camera_fb_get` NULL × 6 連続 | `ESP.restart()` |
| Wire1 slave 沈黙 | `on_i2c_request` 最終呼び出しから 10s 経過 | `Wire1.end()` → `Wire1.begin()` で再 init |
| reinit 直後の Wire1 wedge | `camera_reinit` 完了と同時に予防的に再確立 | `Wire1.end/begin` を `camera_reinit` 末尾で実行 |

`reset_reason` を boot 時に Serial 出力するようにしてあるので、自己回復が起きたかどうか後追いできる。

## I2C 共有バス（StickC master + camera slave）

- マスタ側 `Wire.requestFrom` が失敗するとバスが physical に stuck（SDA/SCL が low に張り付く）することがある
- **`Wire.end()` + `Wire.begin()` だけでは復旧しない**。SCL を最大 16 回手動パルス + 手動 STOP で stuck slave を解放してから `Wire.begin()` する必要
- `arduino_src/roverc_server/roverc_server.ino` の `recover_i2c_bus()` で実装済
- プローブ失敗判定は **ヒステリシス**を入れる（3 連続失敗で初めて `present=false`）。一瞬の blip で UI が空になるのを防ぐ
- **永続不在のスレーブは bus recovery カウンタから除外**。そうしないと不在 right の連続失敗で毎周回 recovery が走り left の正常 probe を阻害する

## "Re-flash で直る、USB 抜き差しで直らない" の正体

- Timer Camera X は BM8563 RTC バックアップ + 内部キャパで USB 抜き直後も ESP32 が完全に死なない
- OV3660 センサ側の I2C ステートマシンや SCCB 状態が残留して、ホットリスタートでは再 init に失敗するケースがある
- `arduino-cli upload` の DTR/RTS シーケンスは EN を長くホールドするので結果的にちゃんとセンサ含めてリセットされる
- 教訓: 自己回復ロジック（`ESP.restart()` 含む）を仕込んでおけば物理操作なしで復帰可能。今回はそうしてある

## 効果なし or 副次的でしかなかった対策（記録）

| 対策 | 結果 | 備考 |
|---|---|---|
| `esp_wifi_set_ps(WIFI_PS_NONE)` | 今回の症状には効果なし | バースト周期が DTIM と一致して見えたが真因ではなかった。一般的には station 側遅延削減には有効 |
| OpenWRT `wmm_ac_be_txop_limit=32` / `tx_burst=0.5` | 今回の症状には効果なし | 設定自体は副作用が良いので残してある（latency-sensitive 用途で原則有効） |
| `client.flush()` を毎フレーム | 効果なし | TCP_NODELAY が既に効いていれば追加効果なし |
| モータ端子キャパシタ | (未検証) ラグ対策としては妥当 | brownout は実測ゼロだったので電源側ではなく、RF EMI 経由 WiFi 劣化が支配的と推測。実装するなら motor 端子直近に 100nF（要分解） |

## ラグ系トラブルの切り分けフロー

1. **brownout の有無**: ESP.restart 痕跡を `reset_reason` で確認。今回はゼロ → 電源サグは brownout 閾値（~2.43V）まで届いていない
2. **HTTP 単発の生存**: `curl --max-time 3 http://<ip>/jpg` が返るか。返れば camera HTTP 層は健全
3. **WebServer 占有の有無**: persistent な `/stream` クライアントが居る間は他リクエストが詰まる。teleop を止めてから単独 curl で計測
4. **網側のバースト性**: 生 socket recv で `Δ` を見る。urllib 経由と比較して差があれば parser 側問題
5. **HUD fps_1s の意味**: render が観測した frame_count 変化の頻度。parser が高 fps でも render に bursty に届けば fps_1s は低く見える → parser 側の per-frame timestamp で実測 fps を見るのが確実

## 残した装備（再発時に効く）

- **カメラ firmware**: watchdog + health check + Wire1 self-heal + framesize-change deinit/init + reset_reason ログ + boot 時の `WIFI_PS_NONE`
- **StickC firmware**: I2C probe hysteresis + 物理クロックパルス bus recovery
- **クライアント**: rolling 1s fps + 30s window freeze 数 + max gap + 累積 err 表示の HUD（数値で再発を即検知）
- **ネットワーク (OpenWRT)**: TXOP 32μs / tx_burst 0.5 を radio0 に設定済（副作用としてのみ有効）

## 参考リンク

- [espressif/arduino-esp32 #5834 — XCLK 5/10/20MHz worsens WiFi throughput](https://github.com/espressif/arduino-esp32/issues/5834)
- [esphome/issues #4191 — esp32_camera not working with 10/20MHZ, only 8MHZ](https://github.com/esphome/issues/issues/4191)
- [espressif/esp32-camera #220 — OV3660 quality < 5 broken](https://github.com/espressif/esp32-camera/issues/220)
- [espressif/esp32-camera #232 — OV3660 fps performance](https://github.com/espressif/esp32-camera/issues/232)
- [hx-esp32-cam-fpv — low-latency MJPEG over UDP+FEC reference](https://github.com/RomanLut/hx-esp32-cam-fpv)
- [ESP-FAQ: Camera Application](https://docs.espressif.com/projects/esp-faq/en/latest/application-solution/camera-application.html)
- [OpenWrt forum — Reducing multiplexing latencies in wifi](https://forum.openwrt.org/t/reducing-multiplexing-latencies-still-further-in-wifi/133605)
