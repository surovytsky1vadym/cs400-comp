# CS400 Compiler — Practice 8

Hand-written lexer, recursive-descent parser, a separate semantic pass (`SemanticChecker`)
and code generation via `llvmlite.ir`.

```bash
python3 src/compiler.py input.txt output.ll   # compile
python3 src/compiler.py --ast input.txt       # print the tree
python3 src/compiler.py --ast-checked input.txt   # print the tree after the semantic pass
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

## Practice 5: if / else / while, scopes, basic blocks
A block is a new frame in the semantic pass (shadowing with any type is allowed) and new basic
blocks in the IR (`then`/`else`/`merge`, `cond`/`body`/`end`). Every `alloca` is placed in the entry block.
`opt -passes=mem2reg -S output.ll` turns the slots into registers (see `tests/ok/if-else_phi.txt`).

Warm-up: the inner `exit x` refers to `i64 x{20}` and prints 20; the middle `exit x` (bool x) and the
last `exit x` (i32 x) are never reached. The pass pushes and pops 2 frames; the IR needs 5 basic blocks
(entry, then, merge for the outer if, then, merge for the inner one).

## Practice 6: structs and functions
`struct` declarations become named LLVM types (`%"Point" = type {i32, i32}`); fields are reached with
`gep` by the index the semantic pass found. Functions are collected first (signatures), then checked
and generated; each becomes `fn_<name>` in the module, with parameters copied into entry-block slots.
`exit` inside a function is `ret`. Nested `{}` initialisers are built with `insertvalue`.

## Practice 7: member functions, read() and print
Fields come first, then member functions. Approach: new nodes all the way (`MemberFnNode`,
`MemberCallNode`). Inside a member function a name is looked up among parameters and locals, then
the fields of the struct (`VarNode.member_index`); `f(...)` looks among the sibling member functions
first, then the global ones. In the IR `Point_shift` takes `%Point* this` first; a field is a `gep` on
`this`; a call passes the object's `alloca` (or a `gep` into it), and a call result gets a temporary `alloca`.
Mutation rule: a member function is mutating if it assigns a field of its object or calls a mutating member
function on its object or its fields; decided by a fixpoint after all bodies are checked, then every
mutating call must go through `mut` links only (a call result is a temporary: anything goes).
`read()` calls `scanf("%d")` into an entry-block `alloca`; `print` shares `printf` with `exit` and prints
structs by walking their field table (`Circle{center: Point{x: 3, y: 4}, radius: 2}`).
Additional task: `.` after a call (`p.moved(1).shift(2)`, `origin().moved(7).x`) — fields of a result are `extractvalue`.
A test that reads input keeps it in `NAME.in`; `check.py` passes it as stdin.

## Practice 8: callable objects and lambdas
Both are rewrites in the semantic pass. A call `name(args)` where `name` is a visible variable becomes
the member call `name.call(args)`: the `CallNode` turns into a `CallableCallNode`, a `MemberCallNode` that only
prints differently, so the argument checks, the mutation rule and code generation are the ones of member
calls. `obj.name(args)` on a field becomes `obj.name.call(args)`. A name that is both a visible variable and a
function is an error at the call.
A lambda becomes struct `lambda.<name>` with one field per capture, its member function `call`, and a variable
initialised at the declaration with the captured values, so a capture is a copy. Lambdas of the same name in
different blocks get `lambda.<name>.1` and so on. The body is checked like a member function of that struct,
except that it does not see sibling member functions and reports outer names as not captured. The struct's name
cannot be written in a program, so a lambda cannot be a parameter, a result, a printed value or assigned.
`--ast` stays the parse tree; `--ast-checked` prints the tree after the pass.
Additional task: `[mut n]` makes the field `mut`; the closure variable is `mut` when any capture is.
A test may also keep the `--ast-checked` output in `NAME.ast_checked`.
