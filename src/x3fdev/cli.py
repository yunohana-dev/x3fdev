"""x3fdev の CLI。ログは stderr に出す。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from x3fdev import camera
from x3fdev.external import ExternalCommandError, X3fExtract
from x3fdev.root import ProjectRootNotFoundError, find_project_root, tmp_dir

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Sigma X3F の自動現像ツール")
log = logging.getLogger("x3fdev")


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def _fail(msg: str) -> typer.Exit:
    typer.echo(f"エラー: {msg}", err=True)
    return typer.Exit(code=1)


def _root() -> Path:
    try:
        return find_project_root()
    except ProjectRootNotFoundError as e:
        raise _fail(str(e)) from e


def _check_file(path: Path) -> None:
    if not path.is_file():
        raise _fail(f"ファイルがありません: {path}")


@app.command()
def info(
    file: Path,
    as_json: Annotated[bool, typer.Option("--json", help="機械可読な出力")] = False,
) -> None:
    """X3F の情報を表示する（機種判定、デコード可否）。"""
    root = _root()
    _check_file(file)
    try:
        meta = camera.read_metadata(file)
    except ExternalCommandError as e:
        raise _fail(str(e)) from e

    result: dict[str, Any] = {"file": str(file), "metadata": meta}
    reasons: list[str] = []
    try:
        det = camera.detect(meta)
        result["model"] = det.model.key
        result["generation"] = det.model.generation
    except (camera.UnknownCameraError, ValueError) as e:
        result["model"] = None
        reasons.append(str(e))
    try:
        x = X3fExtract.locate(root)
        result["decoder"] = {"name": "x3f_extract", "commit": x.commit}
    except ExternalCommandError as e:
        result["decoder"] = None
        reasons.append(str(e))
    result["decodable"] = not reasons
    result["reasons"] = reasons

    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    rows = [
        ("ファイル", str(file)),
        ("機種", f"{result['model'] or '不明'}（{meta.get('Model')}）"),
        ("撮影日時", meta.get("DateTimeOriginal")),
        ("ISO", meta.get("ISO")),
        ("シャッター", meta.get("ExposureTime")),
        ("F 値", meta.get("FNumber")),
        ("焦点距離", meta.get("FocalLength")),
        ("画像サイズ", f"{meta.get('ImageWidth')}x{meta.get('ImageHeight')}"),
        ("デコード", "可" if result["decodable"] else "不可"),
    ]
    for label, value in rows:
        if value is not None:
            typer.echo(f"{label}: {value}")
    for r in reasons:
        typer.echo(f"  - {r}")


@app.command()
def decode(
    file: Path,
    output: Annotated[Path, typer.Option("--output", "-o", help="出力する TIFF のパス")],
    camera_key: Annotated[
        str | None, typer.Option("--camera", help="機種を上書きする（例: dp0_quattro）")
    ] = None,
) -> None:
    """X3F を linear TIFF（float32、カメラの色空間、WB 未適用）に変換する。検証・デバッグ用。"""
    from x3fdev.decode import decode_x3f, write_linear_tiff

    root = _root()
    _check_file(file)
    try:
        det = camera.detect(camera.read_metadata(file), override=camera_key)
        x = X3fExtract.locate(root)
    except (camera.UnknownCameraError, ValueError, ExternalCommandError) as e:
        raise _fail(str(e)) from e
    if not det.detected:
        log.warning(
            "機種を --camera で上書きしました: %s（メタデータ: %s）", det.model.key, det.exif_model
        )

    log.info("デコード中: %s（%s）", file, det.model.key)
    try:
        img = decode_x3f(file, det.model, x, tmp_dir(root) / "work")
    except (ExternalCommandError, RuntimeError, ValueError) as e:
        raise _fail(str(e)) from e
    write_linear_tiff(img, output)
    h, w = img.data.shape[:2]
    log.info("書き出しました: %s（%dx%d）", output, w, h)


if __name__ == "__main__":
    app()
