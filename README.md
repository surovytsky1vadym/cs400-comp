# CS400 Compiler — Practice 3

A hand-written lexer + compiler for a small statically-typed language, targeting LLVM IR via `llvmlite`.

## Requirements

```
pip install llvmlite
```

`lli` (LLVM interpreter) must be on your PATH for running `.ll` files.

## Usage

```bash
# Compile a source file to LLVM IR
python3 compiler.py input.txt output.ll

# Run the IR directly (fastest for testing)
lli output.ll

# Or compile to a native binary
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
```

On any lexical, syntactic, or semantic error the compiler prints one line to stderr:

```
compilation error: line 2:8: unexpected byte '$'
```

and exits with a non-zero code without writing the output file.

## Debug: dump token list

```bash
LEX_DUMP=1 python3 compiler.py input.txt /dev/null
```

## Language

One statement per line. All variables are 32-bit integers.

| Statement | Meaning |
|-----------|---------|
| `i32 x{5}` | Declare const variable `x`, initialised to `5` |
| `i32 mut y{10}` | Declare mutable variable `y` |
| `y := expr` | Assign to a `mut` variable |
| `exit val` | Print result and exit |

Expressions in `{...}` and on the right of `:=`: a constant, a variable, or one binary operation (`+`, `-`, `*`) on two values.

## Running the tests

```bash
chmod +x tests/run_tests.sh
bash tests/run_tests.sh
```
