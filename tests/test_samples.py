"""実際の X3F を使うテスト。

X3FDEV_TEST_SAMPLES にサンプルのディレクトリを指定する。未設定なら skip する。
構成: <X3FDEV_TEST_SAMPLES>/<機種のキー>/decode/*.X3F
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from x3fdev import camera
from x3fdev.decode import decode_x3f
from x3fdev.external import X3fExtract
from x3fdev.root import find_project_root

SAMPLES = os.environ.get("X3FDEV_TEST_SAMPLES")
pytestmark = pytest.mark.skipif(not SAMPLES, reason="X3FDEV_TEST_SAMPLES が未設定")


def _first_sample(model_key: str) -> Path:
    assert SAMPLES
    files = sorted((Path(SAMPLES) / model_key / "decode").glob("*.X3F"))
    if not files:
        pytest.skip(f"{model_key} のサンプルがありません")
    return files[0]


@pytest.mark.parametrize("model_key", list(camera.MODELS))
def test_detect_from_metadata(model_key: str) -> None:
    meta = camera.read_metadata(_first_sample(model_key))
    det = camera.detect(meta)
    assert det.model.key == model_key


@pytest.mark.parametrize("model_key", list(camera.MODELS))
def test_decode(model_key: str) -> None:
    root = find_project_root()
    try:
        x = X3fExtract.locate(root)
    except Exception as e:
        pytest.skip(str(e))
    path = _first_sample(model_key)
    meta = camera.read_metadata(path)
    img = decode_x3f(path, camera.model_from_key(model_key), x, root / "tmp" / "work")
    assert img.data.dtype == np.float32
    assert img.data.shape == (meta["ImageHeight"], meta["ImageWidth"], 3)
    assert img.meta.color_space == "camera"
    assert not img.meta.white_balanced
    # 正規化されている（大半の画素が 0〜1 付近）
    med = float(np.median(img.data))
    assert 0.0 < med < 1.0
    # 空間ゲインは Quattro 以外で適用される（x3f_extract の既定）
    assert (img.meta.extra["gain_maps"] > 0) == (model_key != "dp0_quattro")
