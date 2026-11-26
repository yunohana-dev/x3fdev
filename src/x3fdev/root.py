"""プロジェクトルートの解決。

ツールの状態（キャッシュ、プロファイル、評価ログ）はすべてプロジェクトルートの中に置く。
プロジェクトルートが特定できなければ、何も書き込まずにエラーにする。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_NAME = "x3fdev"


class ProjectRootNotFoundError(RuntimeError):
    pass


def find_project_root(start: Path | None = None) -> Path:
    """start（既定はカレントディレクトリ）から親へ向かってプロジェクトルートを探す。"""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        pyproject = d / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError:
            continue
        if data.get("project", {}).get("name") == PROJECT_NAME:
            return d
    raise ProjectRootNotFoundError(
        f"プロジェクトルートが見つかりません（{here} から上に、"
        f"[project] name = {PROJECT_NAME!r} の pyproject.toml がありません）。"
        "プロジェクトルートの中で実行してください"
    )


def tmp_dir(root: Path) -> Path:
    """キャッシュ・ビルド成果物・作業ファイルの置き場（git 管理外）。"""
    return root / "tmp"
