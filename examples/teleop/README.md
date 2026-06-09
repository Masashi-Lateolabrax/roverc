# examples/teleop

RoverC を WASD で操縦する pygame テレオペ UI。プラットフォームの共有
クライアントライブラリ（`src/python_client/` の `roverc` / `camera` /
`coefs` / `telemetry`）の使用例。

## 構成

| File | Role |
|---|---|
| `teleop.py` | pygame マルチウィンドウ UI、UDP モーション送信、前方単眼カメラ表示、trim / kick スライダ、バッテリ表示 |
| `widgets.py` | この UI 専用の pygame Slider / Button / ChoiceRow |

`teleop.py` は冒頭で `src/python_client` を `sys.path` に追加し、共有ライブラリを
import する（パッケージ未インストールでも動く既存の `scripts/` と同じ方式）。
`widgets` は本ディレクトリから直接 import される。

## 実行

```sh
# StickC LCD に出る IP を直接渡す
uv run examples/teleop/teleop.py --host 192.168.1.123

# 校正済の多項式係数を起動時にプッシュする
uv run examples/teleop/teleop.py --host 192.168.1.123 --coefs coefs/identity.json
```

3 ウィンドウ（input / settings / cameras）が開く。キー割り当て・操作は
トップレベル `../../README.md` の「操縦」節を参照。
