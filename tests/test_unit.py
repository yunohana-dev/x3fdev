"""X3F を必要としない単体テスト。"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
import tifffile

from x3fdev import camera
from x3fdev.decode import read_external_tiff
from x3fdev.decode.dng import DngRaw, GainMap, apply_gain_maps, normalize, parse_opcode_list
from x3fdev.root import ProjectRootNotFoundError, find_project_root


def test_find_project_root_from_subdir(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x3fdev"\n')
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


def test_find_project_root_ignores_other_projects(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n')
    with pytest.raises(ProjectRootNotFoundError):
        find_project_root(tmp_path)


def test_detect_known_models() -> None:
    for key, exif in [
        ("dp0_quattro", "SIGMA dp0 Quattro"),
        ("dp2", "SIGMA DP2"),
        ("sd1_merrill", "SIGMA SD1 Merrill"),
    ]:
        det = camera.detect({"FileType": "X3F", "Model": exif})
        assert det.model.key == key
        assert det.detected


def test_detect_unknown_model_is_error() -> None:
    with pytest.raises(camera.UnknownCameraError):
        camera.detect({"FileType": "X3F", "Model": "SIGMA sd Quattro H"})


def test_detect_override() -> None:
    det = camera.detect({"FileType": "X3F", "Model": "SIGMA sd Quattro H"}, override="dp2")
    assert det.model.key == "dp2"
    assert not det.detected
    with pytest.raises(camera.UnknownCameraError):
        camera.detect({"FileType": "X3F", "Model": "SIGMA DP2"}, override="nope")


def test_detect_rejects_non_x3f() -> None:
    with pytest.raises(ValueError):
        camera.detect({"FileType": "JPEG", "Model": "SIGMA DP2"})


def _opcode_list(gm: GainMap) -> bytes:
    nv, nh, mp = gm.gains.shape
    params = (
        struct.pack(
            ">10I4dI",
            gm.top,
            gm.left,
            gm.bottom,
            gm.right,
            gm.plane,
            gm.planes,
            gm.row_pitch,
            gm.col_pitch,
            nv,
            nh,
            gm.spacing_v,
            gm.spacing_h,
            gm.origin_v,
            gm.origin_h,
            mp,
        )
        + gm.gains.astype(">f4").tobytes()
    )
    return struct.pack(">I", 1) + struct.pack(">IIII", 9, 0x01030000, 0, len(params)) + params


def test_parse_opcode_list_roundtrip() -> None:
    gains = np.arange(12, dtype=np.float32).reshape(2, 3, 2)
    gm = GainMap(0, 0, 10, 20, 1, 2, 1, 1, 1.0, 0.5, 0.0, 0.0, gains)
    (parsed,) = parse_opcode_list(_opcode_list(gm))
    assert parsed.plane == 1 and parsed.planes == 2
    assert parsed.spacing_h == 0.5
    np.testing.assert_array_equal(parsed.gains, gains)


def test_apply_gain_map_bilinear() -> None:
    h, w = 4, 5
    img = np.ones((h, w, 3), dtype=np.float32)
    # 2x2 の格子。左上 1、右上 2、左下 3、右下 4（相対座標で画像全体を覆う）
    gains = np.array([[[1.0], [2.0]], [[3.0], [4.0]]], dtype=np.float32)
    gm = GainMap(0, 0, h, w, 0, 1, 1, 1, 1.0, 1.0, 0.0, 0.0, gains)
    out = apply_gain_maps(img, (gm,))
    # 画素 (r, c) の相対位置は (r/h, c/w)
    for r in range(h):
        for c in range(w):
            v, u = r / h, c / w
            expect = (1 * (1 - u) + 2 * u) * (1 - v) + (3 * (1 - u) + 4 * u) * v
            assert out[r, c, 0] == pytest.approx(expect, rel=1e-6)
    np.testing.assert_array_equal(out[:, :, 1:], 1.0)


def test_normalize_crops_and_scales() -> None:
    raw = np.full((6, 8, 3), 100, dtype=np.uint16)
    raw[1:5, 2:7] = [1100, 600, 100]
    dng = DngRaw(
        raw=raw,
        black_level=(100.0, 100.0, 100.0),
        white_level=(2100.0, 1100.0, 600.0),
        active_area=(1, 2, 5, 7),
        gain_maps=(),
        as_shot_neutral=(1.0, 1.0, 1.0),
        color_matrix1=[[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
        forward_matrix1=[[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
        camera_calibration1=[[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]],
        baseline_exposure=0.0,
    )
    out = normalize(dng)
    assert out.shape == (4, 5, 3)
    np.testing.assert_allclose(out[0, 0], [0.5, 0.5, 0.0])


def test_read_external_tiff_srgb(tmp_path: Path) -> None:
    a = np.zeros((2, 2, 3), dtype=np.uint16)
    a[0, 0] = 65535
    a[0, 1] = round(0.5 * 65535)
    p = tmp_path / "x.tif"
    tifffile.imwrite(p, a, photometric="rgb")
    img = read_external_tiff(p, transfer="srgb")
    assert img.meta.color_space == "srgb_linear"
    assert img.meta.white_balanced
    np.testing.assert_allclose(img.data[0, 0], 1.0)
    np.testing.assert_allclose(img.data[0, 1], 0.21404, atol=1e-4)
    np.testing.assert_allclose(img.data[1, 1], 0.0)
