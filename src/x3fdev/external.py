"""外部コマンド（x3f_extract、exiftool）の呼び出し。

終了コードと stderr を必ず確認し、失敗を握りつぶさない。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ExternalCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def run(args: Sequence[str | Path], *, cwd: Path | None = None) -> CommandResult:
    argv = [str(a) for a in args]
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise ExternalCommandError(f"コマンドが見つかりません: {argv[0]}") from e
    if p.returncode != 0:
        raise ExternalCommandError(
            f"コマンドが失敗しました（終了コード {p.returncode}）: {' '.join(argv)}\n"
            f"{p.stderr.strip()}"
        )
    return CommandResult(stdout=p.stdout, stderr=p.stderr)


@dataclass(frozen=True)
class X3fExtract:
    """tmp/bin に配置した x3f_extract。"""

    path: Path
    commit: str

    @classmethod
    def locate(cls, root: Path) -> X3fExtract:
        path = root / "tmp" / "bin" / "x3f_extract"
        commit_file = root / "tmp" / "bin" / "x3f_extract.commit"
        if not path.is_file() or not commit_file.is_file():
            raise ExternalCommandError(
                f"x3f_extract がありません（{path}）。scripts/build_x3f.sh でビルドしてください"
            )
        return cls(path=path, commit=commit_file.read_text().strip())


def exiftool_path() -> str:
    path = shutil.which("exiftool")
    if path is None:
        raise ExternalCommandError("exiftool が見つかりません（brew install exiftool）")
    return path
