# ADR 0001: デコーダ（x3f_extract）のオプションと出力の性質

- 状態: 採用
- 日付: 2026-11-27
- 関連: design.md 5 章「デコード」、15 章 M0

## 背景

デコードの出力が「ホワイトバランス適用済みか」「どの色空間か」によって、パイプラインの先頭の処理が変わる。M0 で x3f_extract（Kalpanika/x3f）の出力形式を検証し、確定させる。

## 決定

### デコーダ

- Kalpanika/x3f の本家、commit `e8b48cac0de92c1f0e694ac87a9e0fc31d65cbe3`（2017-06-23）を `third_party/x3f` に submodule で固定する
- `scripts/build_x3f.sh` で `tmp/build/x3f` に展開してビルドし、`tmp/bin/x3f_extract` に置く。依存の OpenCV（2016 年ごろの版）を現行の環境で通すため、展開したコピーに次の回避策を当てる。いずれもビルドを通すためのもので、出力の画素値には影響しない
  - TBB の無効化（同梱の TBB はダウンロード元が失われている。並列化にだけ使われる）
  - CMake 3.27 の使用（`uvx --from cmake==3.27.9`。CMake 4 は廃止したポリシーを拒否する）
  - `-dumpversion` から `-dumpfullversion` への置換（gcc 7 以降で版の検出に失敗する）
  - プリコンパイル済みヘッダーの無効化（gcc 6 以降で `math.h` が見つからなくなる）

### オプション

`x3f_extract -dng`（他のオプションは既定のまま）。コード上の定義は `x3fdev.decode.X3F_EXTRACT_OPTIONS`。

既定で有効になる処理:

| 処理 | 既定 | 備考 |
|---|---|---|
| 黒レベルの推定と補正 | 有効 | 遮光領域から推定。DNG の BlackLevel に残る |
| 欠陥画素の補間 | 有効 | |
| ノイズ低減 | 有効 | 解像感にはほぼ影響しない（下記の検証を参照） |
| Quattro の展開 | 有効 | 下層を上層の解像度に展開する。dp0 Quattro は 5424×3616（SPP と同じサイズ・画角） |
| 空間ゲイン（色シェーディングの補正） | Quattro 以外で有効 | DNG の OpcodeList2（GainMap）に書かれ、画素には未適用 |

### 出力の性質

デコード層（`x3fdev.decode.decode_x3f`）は DNG を読み、次の処理を行って `DecodedImage` を返す。

1. ActiveArea でクロップする
2. チャンネルごとに `(v - BlackLevel) / (WhiteLevel - BlackLevel)` で正規化する（白レベルが 1.0）
3. GainMap を適用する。画素 (r, c) の相対位置は (r/H, c/W) とする（x3f_extract 自身の sRGB 出力と同じ対応）

結果は次の性質を持つ。

- **linear**、float32
- **カメラの色空間**（センサーのネイティブ RGB）
- **ホワイトバランスは未適用**
- メタデータとして、DNG の AsShotNeutral、ColorMatrix1、ForwardMatrix1、CameraCalibration1、BaselineExposure を持つ

カメラの色空間から XYZ への変換は、DNG の規約（ForwardMatrix による D50 への変換）に従うと、x3f_extract が CAMF から計算する変換と一致する。

```
neutral' = inv(CameraCalibration1) · AsShotNeutral
XYZ_D50  = ForwardMatrix1 · diag(1 / neutral') · inv(CameraCalibration1) · camera
         × 2^BaselineExposure
```

### パイプラインへの影響

パイプラインの先頭は、design.md 6 章のとおり、黒レベルの補正 → ホワイトバランス → 色補正行列の順でよい。ただし、黒レベルの補正と白レベルによる正規化は、デコード層で済ませる。

## 検証

### DNG の解釈

3 機種で、上の式でデコード結果を sRGB にし、`x3f_extract -tiff -color sRGB` の出力と比較した。白飛びと黒つぶれを除いた画素で、linear 値の差は量子化誤差の範囲だった。黒と白のレベル、空間ゲイン、色行列の解釈が正しいことを確認した。

| 機種 | サンプル | チャンネルごとの比（中央値） | 差の平均 | 差の 99 パーセンタイル |
|---|---|---|---|---|
| dp0 Quattro | 20230406094048 | 1.0000 / 1.0001 / 0.9998 | 1e-5 | 1.2e-4 |
| DP2 | SDIM0004 | 1.0000 / 1.0001 / 0.9998 | 1e-5 | 3e-5 |
| SD1 Merrill | 20161105121538 | 0.9999 / 1.0000 / 0.9998 | 2e-5 | 1e-4 |

### SPP との目視比較（dp0 Quattro）

`x3f_extract -tiff -color sRGB` の出力と、SPP の標準設定の TIFF を、全体と等倍で比較した（3 枚）。

- 偽色や破綻はなく、ノイズも同等。幾何は SPP と一致する
- SPP よりコントラストと彩度が低い。トーンカーブがないためで、M1 以降で扱う
- 解像感は SPP の方が高く見える。`-no-denoise` でもほぼ変わらない（輝度のラプラシアンの標準偏差: SPP 0.170、既定 0.0592、ノイズ低減なし 0.0597）。トーンの違いを含む比較なので、M1 の終わりに条件をそろえて再評価する（design.md 17 章）

ユーザーの判断: 破綻はなく、許容する。解像感は気になるので、M1 の終わりに再評価して入力経路を判断する。SD1 Merrill の左下の暗部に見える色ムラは、特に気にならない。

## 検討した他の案

- **`-tiff -color none`:** 前処理済みの linear が得られるが、黒と白のレベルがファイルに残らず、空間ゲインも適用されない
- **`-tiff -color sRGB`:** x3f_extract が色変換とガンマまで適用する。ホワイトバランスと色変換を自前で制御できず、sRGB の外の色とハイライトがクリップされる
- **外部 TIFF（SPP の出力）を主な入力にする:** SPP での書き出し作業が写真ごとに要る。ホワイトバランスと sRGB へのクリップが済んでいる。デコード層のもう一つの実装（`read_external_tiff`）として用意し、M1 の終わりの再評価の結果で主経路にするかを判断する
