#!/usr/bin/env bash
# tests/run_tests.sh — run all tests and report pass/fail
set -euo pipefail

COMPILER="python3 $(dirname "$0")/../compiler.py"
TESTS_DIR="$(dirname "$0")"
TMPOUT=$(mktemp /tmp/compiler_test_XXXXXX.ll)
TMPSTDERR=$(mktemp /tmp/compiler_test_XXXXXX.err)

pass=0
fail=0

run_test() {
    local src="$1"
    local expected_file="$2"
    local name
    name=$(basename "$src" .txt)

    # Determine if this is a valid or invalid test
    if [[ "$name" == valid_* ]]; then
        # Should compile and run successfully
        if python3 "$COMPILER" "$src" "$TMPOUT" 2>"$TMPSTDERR"; then
            actual=$(lli "$TMPOUT" 2>&1)
            expected=$(cat "$expected_file")
            if [[ "$actual" == "$expected" ]]; then
                echo "  PASS  $name"
                ((pass++))
            else
                echo "  FAIL  $name"
                echo "        expected: $expected"
                echo "        actual:   $actual"
                ((fail++))
            fi
        else
            echo "  FAIL  $name  (compilation failed unexpectedly)"
            cat "$TMPSTDERR"
            ((fail++))
        fi
    else
        # Should fail with an error message
        if python3 "$COMPILER" "$src" "$TMPOUT" 2>"$TMPSTDERR"; then
            echo "  FAIL  $name  (expected compile error, but succeeded)"
            ((fail++))
        else
            actual=$(cat "$TMPSTDERR")
            expected=$(cat "$expected_file")
            if [[ "$actual" == "$expected" ]]; then
                echo "  PASS  $name"
                ((pass++))
            else
                echo "  FAIL  $name  (wrong error message)"
                echo "        expected: $expected"
                echo "        actual:   $actual"
                ((fail++))
            fi
        fi
    fi
}

echo "=== Running tests ==="
for src in "$TESTS_DIR"/*.txt; do
    exp="${src%.txt}.expected"
    if [[ -f "$exp" ]]; then
        run_test "$src" "$exp"
    fi
done

rm -f "$TMPOUT" "$TMPSTDERR"

echo ""
echo "Results: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
