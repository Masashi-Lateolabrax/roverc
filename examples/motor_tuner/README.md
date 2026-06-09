# examples/motor_tuner

モータモデルのパラメータ → 応答カーブを描く**オフライン**可視化ツール。
実機（StickC / RoverC）には一切繋がない。`config.json` の `motor` セクションに
数値を書く前に、そのパラメータがどんな応答になるかを机上で確認するためのもの。

## 何を描くか

正規化指令 `s` に firmware が掛けるゲインを、KICK → STEADY → BRAKE の 1 サイクルに
渡って描画する：

```
KICK    : gain = f_k(t)    [0, T_k]   立ち上がり 0 -> k_steady
STEADY  : gain = k_steady              平坦
BRAKE   : gain = f_b(t)    [0, T_b]   立ち下がり k_steady -> 0
```

`f_k` / `f_b` の monomial は `coefs.chunk_bytes`（実機に送るのと同じワイヤ）から
取り出すので、カーブは on-device の挙動と一致する。第2軸に
`gain × max_motor` を [-127, 127] にクランプした int8 出力も重ね、飽和が見える。

応答カーブは**パラメータの値だけで決まる**（どのセル = wheel×dir かは無関係）。
よってセル選択 UI は無い。出したい形が決まったら **print cell JSON** ボタンで
そのセル相当の JSON を stdout に出し、`config.json` の好きな `<wheel>_<dir>` に
人手で貼る。

## 実行

```sh
uv run examples/motor_tuner/motor_tuner.py            # m_order = 2
uv run examples/motor_tuner/motor_tuner.py --m-order 1
```

スライダはグラフの相の並びに対応して3列：

- **KICK**（左）：`kick_dur_ms`、`alpha[*]`（`m_order-1` 本）、`beta[*]`（`m_order` 本）
- **STEADY / global**（中央）：`k_steady`、`max_motor`
- **BRAKE**（右）：`brake_dur_ms`、`alpha[*]`、`beta[*]`

`q_k/r_k/...` の生係数ではなく**境界固定パラメータ alpha / beta** を出している。
`Σα=1` を強制するため `f_k(T_k)=k_steady`、`f_b(0)=k_steady` が常に成立し、相を
またいで**高さが段差なく連続**する（どのスライダ値でも）。`beta` は境界に影響しない
自由パラメータ。identity は `alpha[0]=1, beta[0]=1, 他=0`。

赤線が `gain = 127/max_motor`（int8 飽和しきい値）。`Esc`／窓閉じで終了。
**print cell JSON** はスライダの値（`k_steady` / `kick_dur_ms` / `brake_dur_ms` /
`alpha_kick` / `beta_kick` / `alpha_brake` / `beta_brake`）を config.json の
セルと同じ形で出力するので、そのまま貼れる。

## 依存

teleop と同じ **pygame**（リポジトリ依存に含まれる）。Slider/Button は
`examples/teleop/widgets.py` を再利用、`coefs` は `src/crover_mod` を `sys.path`
に追加して import する。追加の GUI ツールキットは不要。
