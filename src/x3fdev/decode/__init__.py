"""デコード: X3F（または外部で作成した TIFF）を linear RGB の画像に変換する。

どちらの経路も DecodedImage（float32 の配列 + メタデータ）を返す。
x3f_extract のオプションと出力の性質は docs/adr/0001-decoder.md を参照。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import tifffile

from x3fdev.camera import CameraModel
from x3fdev.decode.dng import FloatImage, apply_gain_maps, normalize, read_dng
from x3fdev.external import X3fExtract, run

# x3f_extract に渡すオプション。変えたらパイプラインの版を上げる（design.md 12 章）
X3F_EXTRACT_OPTIONS: tuple[str, ...] = ("-dng",)

ColorSpace = Literal["camera", "srgb_linear"]


@dataclass(frozen=True)
class DecoderInfo:
    name: str
    commit: str | None
    options: tuple[str, ...]


@dataclass(frozen=True)
class DecodeMeta:
    """デコード結果の性質。パイプラインの先頭の処理はこれに従う。"""

    decoder: DecoderInfo
    color_space: ColorSpace  # camera: カメラの色空間（WB 未適用）
    white_balanced: bool
    # 以下は color_space == "camera" のときのみ（DNG のタグの値）
    as_shot_neutral: tuple[float, float, float] | None = None
    color_matrix1: list[list[float]] | None = None
    forward_matrix1: list[list[float]] | None = None
    camera_calibration1: list[list[float]] | None = None
    baseline_exposure: float = 0.0
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass(frozen=True)
class DecodedImage:
    data: FloatImage  # (H, W, 3) float32、linear。白レベルが 1.0
    meta: DecodeMeta


def decode_x3f(
    path: Path, model: CameraModel, x3f_extract: X3fExtract, work_root: Path
) -> DecodedImage:
    """x3f_extract で DNG を出力し、正規化と空間ゲインの適用までを行う。

    work_root（プロジェクトルートの tmp/ 配下）に作業ディレクトリを作り、終了後に消す。
    """
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="decode-") as wd:
        out_dir = Path(wd)
        res = run(
            [x3f_extract.path, *X3F_EXTRACT_OPTIONS, "-o", out_dir, path.resolve()],
        )
        dngs = list(out_dir.glob("*.dng"))
        if len(dngs) != 1:
            raise RuntimeError(
                f"x3f_extract が DNG を出力しませんでした: {path}\n{res.stdout}{res.stderr}"
            )
        if "errors: 0" not in res.stdout + res.stderr:
            raise RuntimeError(f"x3f_extract がエラーを報告しました: {path}\n{res.stderr}")
        dng = read_dng(dngs[0])

    data = apply_gain_maps(normalize(dng), dng.gain_maps)
    meta = DecodeMeta(
        decoder=DecoderInfo("x3f_extract", x3f_extract.commit, X3F_EXTRACT_OPTIONS),
        color_space="camera",
        white_balanced=False,
        as_shot_neutral=dng.as_shot_neutral,
        color_matrix1=dng.color_matrix1,
        forward_matrix1=dng.forward_matrix1,
        camera_calibration1=dng.camera_calibration1,
        baseline_exposure=dng.baseline_exposure,
        model=model.key,
        extra={"gain_maps": len(dng.gain_maps), "active_area": list(dng.active_area)},
    )
    return DecodedImage(data=data, meta=meta)


def srgb_to_linear(v: FloatImage) -> FloatImage:
    out: FloatImage = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(
        np.float32
    )
    return out


def read_external_tiff(
    path: Path, transfer: Literal["srgb", "linear"], model: CameraModel | None = None
) -> DecodedImage:
    """外部で作成した 16bit TIFF（SPP の出力など）を読む。

    SPP の TIFF は sRGB のガンマがかかっているので transfer="srgb" で linear に戻す。
    ホワイトバランスと色変換は適用済みとして扱う。
    """
    a = tifffile.imread(path)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError(f"RGB の TIFF ではありません: {path} shape={a.shape}")
    a = a[:, :, :3]
    if a.dtype != np.uint16:
        raise ValueError(f"16bit の TIFF ではありません: {path} dtype={a.dtype}")
    v = a.astype(np.float32) / 65535.0
    data = srgb_to_linear(v) if transfer == "srgb" else v
    meta = DecodeMeta(
        decoder=DecoderInfo("external_tiff", None, (f"transfer={transfer}",)),
        color_space="srgb_linear",
        white_balanced=True,
        model=model.key if model else None,
    )
    return DecodedImage(data=data, meta=meta)


def write_linear_tiff(img: DecodedImage, out: Path) -> None:
    """検証・デバッグ用に float32 の TIFF を書く。

    メタデータは ImageDescription に JSON で入れる。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        out,
        img.data,
        photometric="rgb",
        compression="zlib",
        description=img.meta.to_json(),
        metadata=None,
    )
