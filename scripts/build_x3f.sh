#!/usr/bin/env bash
# x3f_extract をビルドし、tmp/bin/x3f_extract に配置する。
#
# submodule（third_party/x3f）を汚さないため、git archive で tmp/build/x3f に
# 展開してからビルドする。依存の OpenCV もそこに clone・ビルドされる。
#
# 使い方: プロジェクトルートで scripts/build_x3f.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$root/third_party/x3f"
build="$root/tmp/build/x3f"
bindir="$root/tmp/bin"

if [ ! -e "$src/.git" ]; then
    echo "third_party/x3f がありません。git submodule update --init を実行してください" >&2
    exit 1
fi

commit="$(git -C "$src" rev-parse HEAD)"
short="$(git -C "$src" rev-parse --short=12 HEAD)"

mkdir -p "$build" "$bindir"
# deps/（OpenCV のビルド結果）は残し、ソースだけを差し替える
git -C "$src" archive --format=tar HEAD | tar -x -C "$build"

cd "$build"
# OpenCV 同梱の TBB はダウンロード元が失われていてビルドできない。
# TBB は並列化のためだけに使われ出力には影響しないので、無効にする（展開したコピー側を書き換える）
sed -i 's/-D WITH_TBB=ON -D BUILD_TBB=ON/-D WITH_TBB=OFF -D BUILD_TBB=OFF/' install_opencv.sh
# gcc 6 以降では、古い OpenCV のプリコンパイル済みヘッダーが -isystem /usr/include と衝突して
# math.h が見つからなくなるため無効にする
sed -i 's/-D BUILD_TIFF=ON/-D BUILD_TIFF=ON -D ENABLE_PRECOMPILED_HEADERS=OFF/' install_opencv.sh
grep -q 'ENABLE_PRECOMPILED_HEADERS=OFF' install_opencv.sh && grep -q 'WITH_TBB=OFF' install_opencv.sh || { echo "install_opencv.sh の書き換えに失敗しました" >&2; exit 1; }
# gcc 7 以降の -dumpversion は "12" のようにメジャー版だけを返し、古い OpenCV が版を解釈できない。
# checkout の直後に -dumpfullversion へ置き換える
sed -i 's|^git checkout $OCV_HASH \|\| exit 1$|&\ngrep -rl -- -dumpversion cmake \| xargs -r sed -i "s/-dumpversion/-dumpfullversion/"|' install_opencv.sh
grep -q 'dumpfullversion' install_opencv.sh || { echo "install_opencv.sh の書き換えに失敗しました" >&2; exit 1; }

# 古い OpenCV は CMake 4 で廃止されたポリシー（CMP0022/CMP0026 の OLD）を使うため、CMake 3.27 を使う。
# PyPI の cmake パッケージを uvx で実行するシムを PATH の先頭に置く
shim="$root/tmp/build/shim"
mkdir -p "$shim"
cat > "$shim/cmake" <<'EOF'
#!/bin/sh
exec uvx --from cmake==3.27.9 cmake "$@"
EOF
chmod +x "$shim/cmake"
export PATH="$shim:$PATH"
cmake --version | head -1
# OCV_AUX: libtbb.a をリンクしない
make -j"$(nproc)" VER="$short" OCV_AUX=

target="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
install -m 755 "bin/$target/x3f_extract" "$bindir/x3f_extract"
echo "$commit" > "$bindir/x3f_extract.commit"
echo "installed: $bindir/x3f_extract ($commit)"
