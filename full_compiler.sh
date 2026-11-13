#!/bin/sh
# full_compiler.sh PROGRAM.txt BINARY [FILE.c ...] [--remove-unused]
# Builds a binary from the .ll the compiler writes: compiler -> llc -> clang.
# PYTHON, LLC and CLANG override the tools; extra .c files are linked in (extern fn).
set -e
PYTHON=${PYTHON:-python3}
LLC=${LLC:-llc}
CLANG=${CLANG:-clang}
ROOT=$(cd "$(dirname "$0")" && pwd)

flags=""
files=""
for a in "$@"; do
    case "$a" in
        --*) flags="$flags $a" ;;
        *)   files="$files $a" ;;
    esac
done
set -- $files
if [ $# -lt 2 ]; then
    echo "Usage: $0 PROGRAM.txt BINARY [FILE.c ...] [--remove-unused]" >&2
    exit 1
fi
src=$1
bin=$2
shift 2

"$PYTHON" "$ROOT/src/compiler.py" $flags "$src" "$bin.ll"
"$LLC" -filetype=obj -relocation-model=pic "$bin.ll" -o "$bin.o"
"$CLANG" -fPIE "$bin.o" "$@" -o "$bin"
