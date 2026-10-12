#!/usr/bin/env python3
"""
check.py — test runner.

tests/ok/NAME.txt   must compile; `lli` output must equal NAME.expected;
                    if NAME.ast exists, `--ast` output must equal it too.
tests/err/NAME.txt  must be rejected: non-zero exit, stderr equal to NAME.expected,
                    and no output file written.

Usage: python3 check.py [-v]     (LLI=/path/to/lli to override the interpreter)
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
COMPILER = os.path.join(ROOT, "compiler.py")
TESTS = os.path.join(ROOT, "tests")
LLI = os.environ.get("LLI", "lli")
PY = sys.executable


def read(path):
    with open(path) as f:
        return f.read().strip()


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def check_ok(src, base, tmp):
    out = os.path.join(tmp, "out.ll")
    code, _, err = run([PY, COMPILER, src, out])
    if code != 0:
        return False, f"compilation failed: {err}"
    if os.path.exists(base + ".ast"):
        _, ast, _ = run([PY, COMPILER, "--ast", src])
        if ast != read(base + ".ast"):
            return False, "--ast output differs from .ast"
    _, actual, err = run([LLI, out])
    expected = read(base + ".expected")
    if actual != expected:
        return False, f"expected {expected!r}, got {(actual or err)!r}"
    return True, actual


def check_err(src, base, tmp):
    out = os.path.join(tmp, "out.ll")
    if os.path.exists(out):
        os.remove(out)
    code, _, err = run([PY, COMPILER, src, out])
    expected = read(base + ".expected")
    if code == 0:
        return False, "compiled, but an error was expected"
    if os.path.exists(out):
        return False, "output file written despite the error"
    if err != expected:
        return False, f"expected {expected!r}, got {err!r}"
    return True, err


def main():
    verbose = "-v" in sys.argv
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for folder, fn in (("ok", check_ok), ("err", check_err)):
            d = os.path.join(TESTS, folder)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".txt"):
                    continue
                base = os.path.join(d, name[:-4])
                passed, detail = fn(base + ".txt", base, tmp)
                rows.append((folder, name[:-4], passed, detail))

    width = max(len(r[1]) for r in rows)
    print(f"{'':4} {'suite':5} {'test':{width}}  result")
    print("-" * (width + 20))
    for folder, name, passed, detail in rows:
        mark = "PASS" if passed else "FAIL"
        line = f"{mark:4} {folder:5} {name:{width}}"
        if verbose or not passed:
            line += f"  {detail}"
        print(line)
    failed = sum(1 for r in rows if not r[2])
    print("-" * (width + 20))
    print(f"{len(rows) - failed} passed, {failed} failed, {len(rows)} total")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
