#!/usr/bin/env bash

set -euo pipefail

if ! command -v ydotool >/dev/null 2>&1; then
    echo "ydotool が見つかりません。インストールしてください。" >&2
    exit 1
fi

if ! pgrep -x ydotoold >/dev/null 2>&1; then
    echo "警告: ydotoold が起動していません。別ターミナル等で \"sudo ydotoold\" を開始してください。" >&2
fi

ydotool mousemove --absolute 0 0
