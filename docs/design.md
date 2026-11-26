# 設計

用語は `README.md` の定義に従う。

## 1. 全体構成

```
X3F ──[デコード]──> linear RGB ──[パイプライン]──> sRGB JPEG
                                     ▲
       カメラプロファイル（機種ごと・固定） ┤
       画像ごとのパラメータ（自動推定）     ┤
       スタイル（チューニングで学習・全機種共通）┘
```

| 層 | 内容 | 決め方 | 保存先 |
|---|---|---|---|
| カメラプロファイル | 色補正行列（必要なら小さな LUT） | キャリブレーション | `profiles/camera/<model>.json` |
| 画像ごとのパラメータ | 露出補正、ホワイトバランス、黒レベル | 現像時に自動推定 | サイドカーファイル |
| スタイル | トーン、彩度、色相など（8〜10 次元） | チューニング | `profiles/style/<name>.json` |

撮影ごとに変わる値（画像ごとのパラメータ）と好み（スタイル）を分けることで、スタイルが撮影条件に引きずられないようにする。

## 2. 技術スタック

- Python 3.12+、uv
- numpy、tifffile、Pillow（JPEG 書き出し）、opencv-python-headless（位置合わせ）、colour-science（色空間・ΔE）
- PyTorch（CPU）、BoTorch（PairwiseGP）
- FastAPI + uvicorn、素の HTML/JS（ビルド工程なし）
- CLI: typer
- 外部コマンド: x3f_extract（Kalpanika/x3f。git submodule で commit 固定）、exiftool

## 3. プロジェクトルートの解決

- カレントディレクトリから親へ向かって `pyproject.toml` を探し、`[project] name == "x3fdev"` のディレクトリをプロジェクトルートとする
- 見つからなければ、何も書き込まずにエラー終了する
- 実行は `uv run x3fdev ...`。`uv tool install` は使わない

## 4. ディレクトリ構成

```
x3fdev/
  README.md
  CLAUDE.md
  pyproject.toml
  docs/
    requirements.md
    design.md
    adr/                        # 判断の記録
  src/x3fdev/
    cli.py
    root.py                     # プロジェクトルートの解決
    camera.py                   # 機種判定、機種名 → カメラプロファイル
    decode/                     # x3f_extract 呼び出し、外部 TIFF 入力
    cache/                      # sha256 キーのキャッシュ
    pipeline/                   # 純粋関数の処理群、param_space 定義
    autoparam/                  # 画像ごとのパラメータの推定
    calibrate/                  # カメラプロファイルのフィット
    tune/                       # PairwiseGP、候補生成、評価ログ、参照画像による初期化
    web/                        # チューニング UI（FastAPI + static）
    develop/                    # バッチ現像、JPEG 書き出し
    sidecar/                    # サイドカーファイルの読み書き、スキーマ
    geotag/                     # exiftool ラッパー
  tests/
  profiles/                     # git 管理。版の管理は git に任せる
    camera/<model>.json
    style/<name>.json
  tuning/                       # git 管理。評価ログ
    <session_id>/
      session.json
      comparisons.jsonl
  third_party/x3f/              # git submodule
  tmp/                          # .gitignore
    bin/x3f_extract
    cache/<sha256>/
      meta.json
      linear.tif                # フル解像度
      preview.npy               # 長辺 1600px、float32
    candidates/                 # チューニング中の候補画像
```

機種のキーは小文字のスネークケースにする: `dp0_quattro`、`dp2`、`sd1_merrill`。

## 5. デコード

- x3f_extract で linear な 16bit TIFF を得る。具体的なオプション（色空間、ホワイトバランスの適用有無、ノイズ低減の無効化、Quattro の出力解像度）は **M0 で検証して決め、ADR に記録する**
- デコーダの出力が「ホワイトバランス適用済みか」「どの色空間か」によって、パイプラインの先頭の処理が変わる。M0 で確定させる
- キャッシュのキーは X3F の sha256 とデコーダの commit、デコードオプションの組み合わせ
- 外部 TIFF 入力: `decode` 層のもう一つの実装として、16bit linear TIFF を同じ形式（float32 の配列 + メタデータ）で返す

機種とセンサー世代:

| 機種 | 世代 | レンズ |
|---|---|---|
| DP2（無印） | TRUE II 世代（14MP） | 固定 |
| SD1 Merrill | Merrill | 交換式 |
| dp0 Quattro | Quattro（層ごとに解像度が異なる 1:1:4 構成） | 固定 |

## 6. パイプライン

処理順（M0 の結果で調整する）:

1. 黒レベルの補正
2. ホワイトバランス（画像ごとのパラメータ）
3. カメラプロファイルの色補正行列 → 作業色空間（linear Rec.2020 を第一候補。M1 で確定）
4. 露出補正（画像ごとのパラメータ）
5. スタイル: トーンカーブ → 彩度 → 色相シフト → シャドウ・ハイライトの色味
6. ガマットのクリップ（知覚的な圧縮）、sRGB への変換、ガンマ
7. 出力用のシャープネス（固定・弱め。スタイルには含めない）
8. JPEG 書き出し（品質 95 既定、色差のサブサンプリングは 4:4:4、sRGB の ICC プロファイルを埋め込む）
   - Pillow はサブサンプリングを指定しないと 4:2:0 で保存するため、`subsampling=0`（4:4:4）を必ず明示する。Foveon は画素ごとに色の情報を持つので、4:2:0 にすると色の解像度を出力段階で捨てることになる
   - 品質の既定値は暫定。M1 で確定する（17 章）

### スタイルのパラメータ空間（`style-v1` の候補。M1 で確定）

| 名前 | 内容 |
|---|---|
| `contrast` | トーンカーブの S 字の強さ |
| `pivot` | コントラストの中心となる明るさ |
| `highlight_rolloff` | ハイライトの肩の柔らかさ |
| `shadow_lift` | シャドウのつま先の持ち上げ |
| `saturation` | 全体の彩度 |
| `hue_warm` | 赤〜橙〜黄の色相シフト |
| `hue_green` | 緑の色相シフト |
| `hue_blue` | 青〜空色の色相シフト |
| `shadow_tint` | シャドウの色味（暖色〜寒色の 1 軸） |
| `highlight_tint` | ハイライトの色味（暖色〜寒色の 1 軸） |

- 各パラメータは物理的な意味と範囲をコードで定義する（`pipeline/param_space.py`）
- **最大 10 次元**。増やすとペア比較の回数では収束しない

### 画像ごとのパラメータの推定（`autoparam`）

- ホワイトバランス: 撮影時の設定（X3F のメタデータ）を既定とする
- 露出補正: 輝度ヒストグラムからの推定（中間調の目標値とハイライトのクリップ率の制約）
- 黒レベル: デコーダの値を既定とする
- 自動推定の改良は M3 以降。サイドカーファイルの `overrides` で手動の上書きができるようにしておく

## 7. キャリブレーション（`calibrate`）

### 基準画像の作り方（ユーザーが SPP で行う）

| 項目 | 設定 |
|---|---|
| カラーモード | スタンダード |
| ホワイトバランス | 撮影時設定 |
| 露出、コントラスト、シャドウ、ハイライト、彩度、X3F Fill Light | 0 |
| シャープネス、ノイズリダクション | 最小か 0 |
| 出力 | 16bit TIFF、sRGB |

- 1 機種あたり 5〜10 枚。昼の屋外・日陰・室内・高 ISO などシーンを散らす
- X3F と TIFF をファイル名の stem で組にする（`SDIM0001.X3F` と `SDIM0001.tif`）

### 処理

1. X3F をデコードし、カメラプロファイルを適用する直前の段階まで処理する
2. 基準の TIFF を linear に戻す
3. 縮小した上で位置合わせを行う（SPP が歪曲補正などで幾何を変えている可能性があるため。ECC または位相相関）
4. 白飛び・黒つぶれの画素とエッジ付近を除外し、3×3 行列をロバストな最小二乗でフィットする
5. ΔE2000 の平均と 95 パーセンタイルを表示する。行列で不足なら、小さな 3D LUT（例: 9³）を追加する
6. 位置合わせが不安定な場合は、画素単位をやめて領域統計（ブロック平均）でフィットする

## 8. チューニング（`tune`）

### 流れ

1. `tune run` で、入力から評価用の画像を `--sample` 枚選ぶ（撮影日時と明るさでばらけるように抽出）。デコードと縮小をキャッシュに用意する
2. Web UI を起動する（`127.0.0.1:<port>`。Windows のブラウザから開ける）
3. 毎回、評価用の画像を 1 枚選び（順に回す）、候補を 2 つ作る
   - 片方: 現在の事後平均が最良の点
   - もう片方: 獲得関数（EUBO などの嗜好学習向けのもの）で選んだ点
4. 2 枚を並べて表示する。**左右はランダムに入れ替える**（位置による偏りを避けるため）
5. ユーザーの回答を `comparisons.jsonl` に 1 行追記してから、モデルを更新する
6. 10 回に 1 回程度、過去の組み合わせを再提示して一貫性を測る

### UI の操作

| キー | 動作 |
|---|---|
| ← / → | 左 / 右を選ぶ |
| ↓ | 同等（学習には使わず、記録だけする） |
| S | この組をスキップ |
| Z | 100% 表示での比較の切り替え |
| Q | セッションを終了 |

### 学習（`tune fit`）

- PairwiseGP（`PairwiseLaplaceMarginalLogLikelihood`）を、評価ログ全体から学習する
- `param_space` が現在と異なるセッションは除外する
- 事後平均が最大となる点を `profiles/style/<name>.json` に書き込む
- 一貫性の率と事後の不確かさを `metrics` として記録する

### 参照画像による初期化（`tune init`）

- 参照画像から、輝度分布（トーンカーブの形）、彩度分布、色相ごとの偏り、シャドウ・ハイライトの色味を測る
- それらに近づくスタイルのパラメータを推定し、初期値として書き込む
- 被写体による偏り（夕景が多い、など）が混ざるので、初期値にのみ使い、学習の目的関数には使わない

## 9. 現像（`develop`）

1. 入力を列挙する（`-r` で再帰）
2. 機種を判定し、カメラプロファイルを選ぶ
3. `--from-sidecar` が指定され、かつサイドカーファイルがあれば、その `resolved` を使う。それ以外は、プロファイルと自動推定から現像パラメータを決める
4. パイプラインを適用し、JPEG を書き出す。EXIF（撮影日時、機種、露出情報）は元の X3F から引き継ぐ
5. `--save-sidecar` が指定されていれば、サイドカーファイルを書く
6. 既存の出力は、`--overwrite` がなければスキップする
7. `--jobs` でプロセスを並列にする

## 10. サイドカーファイル

- 置き場所は X3F の隣に固定する。ファイル名は `<元ファイル名>.json`（例: `SDIM0001.X3F.json`）
- **再現に使うのは `resolved` だけ**。`provenance` は追跡用の記録

```json
{
  "x3fdev_sidecar": 1,
  "source": {"file": "SDIM0001.X3F", "sha256": "..."},
  "camera": {"model": "dp0_quattro", "detected": true},
  "resolved": {
    "camera_profile": {"matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "lut": null},
    "style": {"param_space": "style-v1", "values": {"contrast": 0.38}},
    "per_image": {"exposure_ev": 0.3, "wb": {"temp": 5200, "tint": 2}, "black": 0.002},
    "output": {"color_space": "sRGB", "jpeg_quality": 95, "subsampling": "4:4:4", "sharpen": "default"}
  },
  "overrides": {},
  "provenance": {
    "tool_version": "0.1.0",
    "pipeline": "pipeline-v1",
    "decoder": {"name": "x3f_extract", "commit": "...", "options": ["..."]},
    "profiles_commit": "abc1234",
    "style_name": "default",
    "created_at": "2026-11-27T10:00:00+09:00"
  }
}
```

- `--from-sidecar` で、`provenance.pipeline` が現在のパイプラインと異なる場合は、警告を出して止める。古い版で再現する場合は、該当する git tag を checkout して実行する

## 11. 評価ログとプロファイルの形式

`tuning/<session_id>/session.json`:
```json
{"id": "2026-11-27_001", "style_name": "default", "param_space": "style-v1", "pipeline": "pipeline-v1",
 "images": [{"sha256": "...", "file": "/mnt/d/photos/SDIM0001.X3F", "model": "dp0_quattro"}],
 "started_at": "..."}
```

`tuning/<session_id>/comparisons.jsonl`（1 行が 1 回の比較）:
```json
{"ts": "...", "image": "sha256:...", "left": {"contrast": 0.42}, "right": {"contrast": 0.30}, "choice": "left", "rt_ms": 2300, "consistency_check": false}
```

`profiles/style/<name>.json`:
```json
{"param_space": "style-v1", "pipeline": "pipeline-v1",
 "values": {"contrast": 0.38},
 "derived_from": ["2026-11-27_001"],
 "metrics": {"n_comparisons": 84, "consistency_rate": 0.86, "posterior_std_mean": 0.07},
 "updated_at": "..."}
```

`profiles/camera/<model>.json`:
```json
{"model": "dp0_quattro", "pipeline": "pipeline-v1",
 "decoder": {"name": "x3f_extract", "commit": "...", "options": ["..."]},
 "matrix": [[...], [...], [...]], "lut": null,
 "fit": {"refs": [{"x3f_sha256": "...", "tiff_sha256": "..."}], "delta_e2000_mean": 1.8, "delta_e2000_p95": 4.2},
 "updated_at": "..."}
```

## 12. バージョン管理の規則

| 識別子 | 上げる条件 |
|---|---|
| `pipeline`（`pipeline-vN`） | 同じ現像パラメータで出力画像が変わる変更（処理順、計算式、色空間、デコードオプションの変更） |
| `param_space`（`style-vN`） | スタイルのパラメータの追加・削除・意味や範囲の変更 |
| `x3fdev_sidecar` | サイドカーファイルのスキーマの非互換な変更 |
| `tool_version` | リリースごと（pyproject の version） |

- `pipeline` を上げたら git tag（`pipeline-vN`）を打つ
- `param_space` が変わったら、旧版の評価ログは学習から除外される。必要ならチューニングをやり直す
- `pipeline` が変わったら、カメラプロファイルは再キャリブレーションが必要（`calibrate` を再実行する）

## 13. CLI

```
x3fdev info FILE [--json]
x3fdev decode FILE --output OUT.tif [--camera MODEL]

x3fdev calibrate --refs DIR [--camera MODEL] [--dry-run]

x3fdev tune init   --style NAME --refs DIR
x3fdev tune run    --input PATH... [--style NAME] [--sample N] [--port 8765] [--resume SESSION_ID] [-r]
x3fdev tune fit    --style NAME [--sessions SESSION_ID...]
x3fdev tune status [--style NAME]

x3fdev develop PATH... --output DIR
    [-r] [--style NAME] [--camera MODEL]
    [--save-sidecar] [--from-sidecar] [--overwrite]
    [--quality 95] [--jobs N]

x3fdev geotag JPEG... --gpx GPX...
    [--tz +09:00] [--clock-offset -35s] [--max-gap 5m] [--dry-run]

x3fdev cache du
x3fdev cache clear [--all | --older-than 30d]
```

- `--style` の既定は `default`
- `PATH` にはファイルとディレクトリを指定できる。ディレクトリは `-r` がなければ直下のみ
- ログは stderr に出す。`info` と `tune status` は `--json` で機械可読な出力にできる

## 14. ジオタグ（`geotag`）

- exiftool の `-geotag` を使う。自前で補間を実装しない
- 書き込み先は JPEG のみ（X3F には書かない）
- `--tz`: EXIF の撮影日時にタイムゾーンがない場合の解釈
- `--clock-offset`: カメラの時計のずれ（カメラの時刻 − 正しい時刻）
- `--max-gap`: この時間内に GPX の点がなければ付与しない

## 15. マイルストーン

| # | 内容 | 合否基準 |
|---|---|---|
| M0 | x3f_extract のビルド。3 機種のデコード検証。`info` と `decode` の実装 | (1) 3 機種の X3F から linear TIFF が得られる。(2) dp0 Quattro の出力を SPP の基準画像と並べた比較画像を作り、**ユーザーが目視で許容と判断する**。不合格なら、外部 TIFF 入力を主経路に切り替える判断をユーザーに仰ぐ。(3) デコードオプションと出力の性質（色空間、WB 適用の有無）を ADR に記録する |
| M1 | キャッシュ、パイプライン、`autoparam`、`develop` の単発実行、`param_space` の確定 | 固定のパラメータで 3 機種とも破綻のない JPEG が出る。同じ入力から同じ出力が得られる（決定性） |
| M2 | `calibrate` | dp0 Quattro で ΔE2000 の平均と 95 パーセンタイルが出る。補正前より改善し、目視で基準画像に近い |
| M3 | `tune run` / `fit` / `status`、Web UI | 候補 2 枚の表示が 1 秒以内。50〜100 回の比較で事後の不確かさが下がり、一貫性の率が表示される。中断と再開ができる |
| M4 | `develop` のバッチ処理とサイドカーファイル | 1 フォルダを一括処理できる。`--from-sidecar` で、プロファイルを消した状態からでも同じ JPEG が再現できる |
| M5 | `geotag` | GPX と時計のずれの指定で JPEG に位置が書き込まれる |
| M6 | `tune init`（参照画像による初期化） | 参照画像から初期値が作られ、`tune run` の開始点に使われる |

M0 から順に進め、**各マイルストーンの合否をユーザーに確認してから次へ進む**。

## 16. リスク

| リスク | 影響 | 対策 |
|---|---|---|
| x3f_extract の dp0 Quattro のデコード品質が不十分 | 全体の前提が崩れる | M0 で最初に検証する。外部 TIFF 入力の経路を用意しておく |
| x3f_extract の開発は止まっており、メタデータの解釈が不完全 | 一部の機種・設定で色や露出がずれる | commit を固定する。キャリブレーションで補正する |
| SPP の基準画像と幾何が合わない | キャリブレーションの精度が落ちる | 縮小と位置合わせ。不安定なら領域統計でフィットする |
| ユーザーの評価のぶれが大きい | スタイルが収束しない | 一貫性の検査を行う。ぶれの大きい次元は固定する |
| 好みがシーンによって変わる | 単一のスタイルでは満足できない | 初期は単一スタイル。必要になれば、シーン別のスタイルを `--style` で使い分ける |

## 17. 未確定事項

- x3f_extract の具体的なオプションと出力の性質（M0）
- 作業色空間（M1）
- `style-v1` のパラメータの確定と範囲（M1）
- JPEG の品質の既定値（M1）: 3 機種の X3F から品質 90／95／100（いずれも 4:4:4）で書き出し、ファイルサイズと差分（SSIM と等倍での目視）を比較してユーザーが決める
- 獲得関数の選定（M3）
