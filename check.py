#!/usr/bin/env python3
"""
check.py — test runner.

tests/ok/NAME.txt   must compile; `lli` output must equal NAME.expected;
                    if NAME.ast exists, `--ast` output must equal it too;
                    if NAME.ast_checked exists, so must the `--ast-checked` output;
                    if NAME.in exists, it is passed to the program as the standard input.
tests/err/NAME.txt  must be rejected: non-zero exit, stderr equal to NAME.expected,
                    and no output file written.
tests/opt/NAME.txt  `lli` output must equal NAME.expected both without and with --remove-unused;
                    `--remove-unused --ast-checked` must equal NAME.astc.
tests/io/NAME.txt   built as a binary (compiler, llc -filetype=obj -relocation-model=pic, clang -fPIE);
                    for every session NAME.S.in the binary's output must be exactly NAME.S.expected,
                    and its exit status 0.
tests/ffi/NAME.txt  the same, with NAME.c given to clang next to the object file.

Usage: python3 check.py [-v] [--remove-unused]
LLI, LLC and CLANG override the tools (default: lli, llc, clang on the PATH).
With --remove-unused (or REMOVE_UNUSED=1) every tests/ok, tests/err, tests/io and tests/ffi program
is compiled with the flag: the output and the errors must stay the same. NAME.ast_checked is not
compared then, since it is the tree before the pass.
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
COMPILER = os.path.join(ROOT, "src", "compiler.py")
TESTS = os.path.join(ROOT, "tests")
LLI = os.environ.get("LLI", "lli")
LLC = os.environ.get("LLC", "llc")
CLANG = os.environ.get("CLANG", "clang")
PY = sys.executable
OPT = "--remove-unused" in sys.argv or bool(os.environ.get("REMOVE_UNUSED"))
FLAGS = ["--remove-unused"] if OPT else []


def read(path):
    with open(path) as f:
        return f.read().strip()


def run(cmd, stdin_path=None):
    stdin = open(stdin_path) if stdin_path else subprocess.DEVNULL
    p = subprocess.run(cmd, capture_output=True, text=True, stdin=stdin)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def compile_and_run(src, base, tmp, flags):
    out = os.path.join(tmp, "out.ll")
    code, _, err = run([PY, COMPILER, *flags, src, out])
    if code != 0:
        return False, f"compilation failed: {err}"
    stdin = base + ".in" if os.path.exists(base + ".in") else None
    _, actual, err = run([LLI, out], stdin)
    expected = read(base + ".expected")
    if actual != expected:
        return False, f"expected {expected!r}, got {(actual or err)!r}"
    return True, actual


def check_ok(src, base, tmp):
    if os.path.exists(base + ".ast"):
        _, ast, _ = run([PY, COMPILER, "--ast", src])
        if ast != read(base + ".ast"):
            return False, "--ast output differs from .ast"
    if os.path.exists(base + ".ast_checked") and not OPT:
        _, ast, _ = run([PY, COMPILER, "--ast-checked", src])
        if ast != read(base + ".ast_checked"):
            return False, "--ast-checked output differs from .ast_checked"
    return compile_and_run(src, base, tmp, FLAGS)


def check_err(src, base, tmp):
    out = os.path.join(tmp, "out.ll")
    if os.path.exists(out):
        os.remove(out)
    code, _, err = run([PY, COMPILER, *FLAGS, src, out])
    expected = read(base + ".expected")
    if code == 0:
        return False, "compiled, but an error was expected"
    if os.path.exists(out):
        return False, "output file written despite the error"
    if err != expected:
        return False, f"expected {expected!r}, got {err!r}"
    return True, err


def check_opt(src, base, tmp):
    for flags in ([], ["--remove-unused"]):
        passed, detail = compile_and_run(src, base, tmp, flags)
        if not passed:
            return False, f"{' '.join(flags) or 'without the flag'}: {detail}"
    _, ast, err = run([PY, COMPILER, "--remove-unused", "--ast-checked", src])
    if ast != read(base + ".astc"):
        return False, f"--remove-unused --ast-checked output differs from .astc {err}".rstrip()
    return True, detail


def build_binary(src, base, tmp, extra=()):
    """The three commands of full_compiler.sh: .txt -> .ll -> .o -> binary."""
    ll, obj, exe = (os.path.join(tmp, "prog" + ext) for ext in (".ll", ".o", ""))
    for cmd in ([PY, COMPILER, *FLAGS, src, ll],
                [LLC, "-filetype=obj", "-relocation-model=pic", ll, "-o", obj],
                [CLANG, "-fPIE", obj, *extra, "-o", exe]):
        code, _, err = run(cmd)
        if code != 0:
            return None, f"{os.path.basename(cmd[0])} failed: {err}"
    return exe, None


def check_sessions(src, base, tmp, extra=()):
    exe, problem = build_binary(src, base, tmp, extra)
    if problem:
        return False, problem
    folder, name = os.path.split(base)
    sessions = sorted(f[len(name) + 1:-3] for f in os.listdir(folder)
                      if f.startswith(name + ".") and f.endswith(".in"))
    if not sessions:
        return False, "no session NAME.S.in"
    for s in sessions:
        with open(f"{base}.{s}.in") as stdin:
            p = subprocess.run([exe], stdin=stdin, capture_output=True, text=True)
        with open(f"{base}.{s}.expected") as f:
            expected = f.read()
        if p.stdout != expected:                        # exactly: no strip, a binary is a real program
            return False, f"session {s}: expected {expected!r}, got {p.stdout!r}"
        if p.returncode != 0:
            return False, f"session {s}: exit status {p.returncode}, expected 0"
    return True, f"sessions: {', '.join(sessions)}"


def check_io(src, base, tmp):
    return check_sessions(src, base, tmp)


def check_ffi(src, base, tmp):
    return check_sessions(src, base, tmp, [base + ".c"])


def main():
    verbose = "-v" in sys.argv
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for folder, fn in (("ok", check_ok), ("err", check_err), ("opt", check_opt),
                           ("io", check_io), ("ffi", check_ffi)):
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
    print(f"{len(rows) - failed} passed, {failed} failed, {len(rows)} total"
          + (" (tests/ok, tests/err, tests/io and tests/ffi with --remove-unused)" if OPT else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
