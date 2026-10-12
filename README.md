# CS400 Compiler — Practice 4

Hand-written lexer, recursive-descent parser, a separate semantic pass (`SemanticChecker`)
and code generation via `llvmlite.ir`.

```bash
python3 compiler.py input.txt output.ll   # compile
python3 compiler.py --ast input.txt       # print the tree
lli output.ll                             # run
python3 check.py                          # run tests/ok and tests/err
```

Types: `i32`, `i64`, `bool` (`true`/`false`). Operators `+ - *` (integers, result is the wider type),
`==` `!=` (two integers or two bools, result `bool`). The only implicit conversion is `i32 → i64` (`sext`).
Errors: `compilation error: line L:C: ...` on stderr, non-zero exit, no output file.

## Warm-up (types on paper)
```
i32 a{10}        i32
i64 b{a}         i32 -> widened to i64
bool c{a == b}   bool
i32 d{a + b}     i64  <- line 4 rejected: a + b is i64, and an i64 never narrows into i32
bool e{c == 1}   (would also be rejected: bool compared with i32)
```
