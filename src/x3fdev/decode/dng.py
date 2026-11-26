"""x3f_extract が出力する DNG（LinearRaw）の読み込み。

副作用はファイルの読み込みだけ。GainMap の適用などの変換は純粋関数にする。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import tifffile

FloatImage = npt.NDArray[np.float32]

_GAINMAP_ID = 9


@dataclass(frozen=True)
class GainMap:
    """DNG の GainMap opcode（OpcodeList2）。座標は ActiveArea でクロップした画像に対するもの。"""

    top: int
    left: int
    bottom: int
    right: int
    plane: int
    planes: int
    row_pitch: int
    col_pitch: int
    spacing_v: float  # 相対座標（画像の高さ = 1）
    spacing_h: float
    origin_v: float
    origin_h: float
    gains: npt.NDArray[np.float32]  # (points_v, points_h, map_planes)


@dataclass(frozen=True)
class DngRaw:
    raw: npt.NDArray[np.uint16]  # (H, W, 3)、クロップ前
    black_level: tuple[float, float, float]
    white_level: tuple[float, float, float]
    active_area: tuple[int, int, int, int]  # top, left, bottom, right
    gain_maps: tuple[GainMap, ...]
    as_shot_neutral: tuple[float, float, float]
    color_matrix1: list[list[float]]
    forward_matrix1: list[list[float]]
    camera_calibration1: list[list[float]]
    baseline_exposure: float


def _floats(value: Any, count: int) -> list[float]:
    """tifffile の (S)RATIONAL 値（分子と分母の平坦な並び）を float にする。"""
    v = list(value) if isinstance(value, tuple | list) else [value]
    if len(v) == 2 * count and all(isinstance(x, int) for x in v):
        return [v[2 * i] / v[2 * i + 1] for i in range(count)]
    if len(v) == count:
        return [float(x) for x in v]
    raise ValueError(f"想定外のタグの値です: {value!r}")


def _matrix(value: Any) -> list[list[float]]:
    f = _floats(value, 9)
    return [f[0:3], f[3:6], f[6:9]]


def parse_opcode_list(data: bytes) -> tuple[GainMap, ...]:
    """OpcodeList（ビッグエンディアン）から GainMap を取り出す。他の opcode はエラーにする。"""
    (count,) = struct.unpack_from(">I", data, 0)
    off = 4
    maps = []
    for _ in range(count):
        op_id, _ver, _flags, size = struct.unpack_from(">IIII", data, off)
        off += 16
        if op_id != _GAINMAP_ID:
            raise ValueError(f"未対応の DNG opcode です: id={op_id}")
        p = off
        top, left, bottom, right, plane, planes, row_pitch, col_pitch, nv, nh = struct.unpack_from(
            ">10I", data, p
        )
        p += 40
        spacing_v, spacing_h, origin_v, origin_h = struct.unpack_from(">4d", data, p)
        p += 32
        (map_planes,) = struct.unpack_from(">I", data, p)
        p += 4
        n = nv * nh * map_planes
        gains = np.frombuffer(data, dtype=">f4", count=n, offset=p).astype(np.float32)
        maps.append(
            GainMap(
                top=top,
                left=left,
                bottom=bottom,
                right=right,
                plane=plane,
                planes=planes,
                row_pitch=row_pitch,
                col_pitch=col_pitch,
                spacing_v=spacing_v,
                spacing_h=spacing_h,
                origin_v=origin_v,
                origin_h=origin_h,
                gains=gains.reshape(nv, nh, map_planes),
            )
        )
        off += size
    return tuple(maps)


def read_dng(path: Path) -> DngRaw:
    with tifffile.TiffFile(path) as tif:
        ifd0 = tif.pages[0]
        assert isinstance(ifd0, tifffile.TiffPage)
        subs = ifd0.pages
        if subs is None or len(subs) != 1:
            raise ValueError(f"DNG の SubIFD が想定外です: {path}")
        sub = subs[0]
        assert isinstance(sub, tifffile.TiffPage)
        if sub.photometric != 34892:  # LinearRaw
            raise ValueError(f"LinearRaw の DNG ではありません: {path}")
        raw = sub.asarray()
        st = sub.tags
        t0 = ifd0.tags

        opcode = st.get("OpcodeList2")
        gain_maps = parse_opcode_list(bytes(opcode.value)) if opcode is not None else ()
        aa = st["ActiveArea"].value
        bl = _floats(st["BlackLevel"].value, 3)
        wl = _floats(st["WhiteLevel"].value, 3)
        neutral = _floats(t0["AsShotNeutral"].value, 3)
        be = t0.get("BaselineExposure")
        return DngRaw(
            raw=raw,
            black_level=(bl[0], bl[1], bl[2]),
            white_level=(wl[0], wl[1], wl[2]),
            active_area=(int(aa[0]), int(aa[1]), int(aa[2]), int(aa[3])),
            gain_maps=gain_maps,
            as_shot_neutral=(neutral[0], neutral[1], neutral[2]),
            color_matrix1=_matrix(t0["ColorMatrix1"].value),
            forward_matrix1=_matrix(t0["ForwardMatrix1"].value),
            camera_calibration1=_matrix(t0["CameraCalibration1"].value),
            baseline_exposure=_floats(be.value, 1)[0] if be is not None else 0.0,
        )


def normalize(dng: DngRaw) -> FloatImage:
    """ActiveArea でクロップし、黒レベルを引いて白レベルを 1.0 にそろえる（チャンネルごと）。"""
    t, left, b, r = dng.active_area
    img = dng.raw[t:b, left:r].astype(np.float32)
    black = np.asarray(dng.black_level, dtype=np.float32)
    white = np.asarray(dng.white_level, dtype=np.float32)
    out: FloatImage = (img - black) / (white - black)
    return out


def apply_gain_maps(img: FloatImage, maps: tuple[GainMap, ...]) -> FloatImage:
    """GainMap を適用する（双線形補間）。

    画素 (r, c) の相対位置は r/H、c/W とする。x3f_extract 自身の sRGB 出力
    （x3f_calc_spatial_gain）と同じ対応で、x3f_extract はこれに合わせて
    MapOrigin と MapSpacing を書いている。
    """
    if not maps:
        return img
    out = img.copy()
    h, w = img.shape[:2]
    for m in maps:
        nv, nh, mp = m.gains.shape
        rows = np.arange(m.top, min(m.bottom, h), m.row_pitch)
        cols = np.arange(m.left, min(m.right, w), m.col_pitch)
        fv = np.clip((rows / h - m.origin_v) / m.spacing_v, 0, nv - 1)
        fh = np.clip((cols / w - m.origin_h) / m.spacing_h, 0, nh - 1)
        v0 = np.minimum(np.floor(fv).astype(np.int64), nv - 2) if nv > 1 else np.zeros_like(rows)
        h0 = np.minimum(np.floor(fh).astype(np.int64), nh - 2) if nh > 1 else np.zeros_like(cols)
        av = (fv - v0)[:, None].astype(np.float32)
        ah = (fh - h0)[None, :].astype(np.float32)
        v1 = np.minimum(v0 + 1, nv - 1)
        h1 = np.minimum(h0 + 1, nh - 1)
        for k in range(m.planes):
            g = m.gains[:, :, min(k, mp - 1)]
            g00 = g[np.ix_(v0, h0)]
            g01 = g[np.ix_(v0, h1)]
            g10 = g[np.ix_(v1, h0)]
            g11 = g[np.ix_(v1, h1)]
            gain = (g00 * (1 - ah) + g01 * ah) * (1 - av) + (g10 * (1 - ah) + g11 * ah) * av
            ch = m.plane + k
            out[m.top : m.bottom : m.row_pitch, m.left : m.right : m.col_pitch, ch] *= gain
    return out
