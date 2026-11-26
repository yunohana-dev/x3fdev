"""機種の判定。

機種は X3F のメタデータ（exiftool で読む Model）から判定する。
未知の機種は既定値に頼らずエラーにする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from x3fdev.external import exiftool_path, run


@dataclass(frozen=True)
class CameraModel:
    key: str  # 機種のキー（小文字のスネークケース）
    exif_model: str  # EXIF の Model の値
    generation: str  # センサー世代


MODELS: dict[str, CameraModel] = {
    m.key: m
    for m in (
        CameraModel("dp0_quattro", "SIGMA dp0 Quattro", "quattro"),
        CameraModel("dp2", "SIGMA DP2", "true2"),
        CameraModel("sd1_merrill", "SIGMA SD1 Merrill", "merrill"),
    )
}

_BY_EXIF_MODEL = {m.exif_model: m for m in MODELS.values()}


class UnknownCameraError(ValueError):
    pass


def model_from_key(key: str) -> CameraModel:
    try:
        return MODELS[key]
    except KeyError:
        known = ", ".join(MODELS)
        raise UnknownCameraError(f"未知の機種のキーです: {key!r}（既知: {known}）") from None


def model_from_exif(exif_model: str) -> CameraModel:
    try:
        return _BY_EXIF_MODEL[exif_model.strip()]
    except KeyError:
        raise UnknownCameraError(
            f"未知の機種です: {exif_model!r}。--camera で機種を指定できます"
            f"（既知: {', '.join(MODELS)}）"
        ) from None


# info で表示するメタデータのタグ
_TAGS = (
    "FileType",
    "Make",
    "Model",
    "SerialNumber",
    "DateTimeOriginal",
    "ISO",
    "ExposureTime",
    "FNumber",
    "FocalLength",
    "ExposureCompensation",
    "WhiteBalance",
    "ImageWidth",
    "ImageHeight",
)


def read_metadata(path: Path) -> dict[str, Any]:
    """exiftool で X3F のメタデータを読む。"""
    res = run([exiftool_path(), "-j", "-n", *(f"-{t}" for t in _TAGS), str(path)])
    items = json.loads(res.stdout)
    if len(items) != 1:
        raise ValueError(f"exiftool の出力が想定外です: {path}")
    meta: dict[str, Any] = items[0]
    meta.pop("SourceFile", None)
    return meta


@dataclass(frozen=True)
class Detection:
    model: CameraModel
    detected: bool  # False なら --camera で上書きされた
    exif_model: str | None


def detect(meta: dict[str, Any], override: str | None = None) -> Detection:
    """メタデータから機種を判定する。override（--camera）があればそれを使う。"""
    if meta.get("FileType") != "X3F":
        raise ValueError(f"X3F ではありません（FileType={meta.get('FileType')!r}）")
    exif_model = meta.get("Model")
    exif_model = str(exif_model) if exif_model is not None else None
    if override is not None:
        return Detection(model_from_key(override), detected=False, exif_model=exif_model)
    if exif_model is None:
        raise UnknownCameraError(
            "メタデータに機種（Model）がありません。--camera で指定してください"
        )
    return Detection(model_from_exif(exif_model), detected=True, exif_model=exif_model)
