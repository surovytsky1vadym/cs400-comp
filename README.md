# CS400 Compiler — Practice 9

Hand-written lexer, recursive-descent parser, a separate semantic pass (`SemanticChecker`)
and code generation via `llvmlite.ir`.

```bash
python3 src/compiler.py input.txt output.ll   # compile
python3 src/compiler.py --ast input.txt       # print the tree
python3 src/compiler.py --ast-checked input.txt   # print the tree after the semantic pass
python3 src/compiler.py --remove-unused input.txt output.ll            # compile the optimized tree
python3 src/compiler.py --remove-unused --ast-checked input.txt        # print the tree after the pass
lli output.ll                             # run
python3 check.py                          # run tests/ok, tests/err and tests/opt
python3 check.py --remove-unused          # the same, with tests/ok and tests/err compiled with the flag
```
Flags may stand anywhere on the command line.

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

## Optimizations (Practice 9)
`--remove-unused` runs one more pass on the tree, after the checker has accepted the program and before code
generation (`remove_unused` in `src/compiler.py`). Without the flag nothing changes.

* `UseCounter` (a visitor) counts, per declaration (`id(DeclNode)`, reached through the `decl` link the checker put
  on every `Var`, so two `x`s are two entries), the reads: in initialisers, values of `:=`, conditions, arguments,
  `print`, `exit`, field chains, member-call objects, capture lists and callable objects. The target of `:=` is not a
  read. It also counts the calls of every global function, wherever they stand (statements, functions, member
  functions, lambda bodies).
* `Pruner` rebuilds every statement list (top level, `if`/`while` blocks, function, member function and lambda bodies)
  without the declarations at zero reads and without the `:=` to them; global functions at zero calls are dropped from
  the program with their bodies (and with the structs of the lambdas inside them). An `if` whose block became empty
  stays. The loop repeats until a round removes nothing. Parameters, fields, lambdas and member functions are kept.
* A value that may be observed keeps its declaration (and an `:=` with such a value keeps its variable). By default
  this is the additional task: `read()` or a call of a function with an effect, i.e. a body with `print`, `read()` or
  `while`, a mutating member function, a function that calls itself directly or through others, or one that calls
  such a function (a fixpoint over the call graph; a callable object's call is its `call`). `--keep-calls` switches
  to the Task 1 rule: any call keeps its value.
* `OPT_TRACE=1` prints, per iteration, every declaration and global function with its count to stderr.
* Tests: `tests/opt/NAME.txt` with `NAME.astc` (`--remove-unused --ast-checked`) and `NAME.expected`, which
  `check.py` compares without and with the flag.

**Limits of counting.** A variable that only feeds itself (`k := k + 1` with nobody else reading `k`) survives: the right
side reads it (`tests/opt/self_feeding.txt`). Two functions that only call each other never reach zero calls and stay
(`tests/opt/removed_fn_lambda.txt`) — the same reason reference counting cannot free a cycle.

**Who frees a removed subtree.** The compiler is written in Python (CPython). An object is freed by reference counting
the moment nothing points at it; objects in a reference cycle are freed only by the cyclic garbage collector
(`gc`), which runs from time to time or on `gc.collect()`. The nodes have no parent pointers, so a removed declaration
whose only owner was its statement list is freed as soon as the list is rebuilt. But some nodes point back up: every
`ExitNode.fn` points at its function, and a lambda's struct and its `call` point at each other (`owner`). A removed
function is therefore a cycle (`FnNode → body → ExitNode → FnNode`): its count never reaches zero, and only the cycle
collector frees it. Measured with `weakref.ref` (script below):
```
outer x before: True refcount 1
outer x after the pass, no gc.collect(): None
fn unused after the pass: <compiler.FnNode object at 0x10ae48ce0>
fn unused after gc.collect(): None
```
```python
import gc, sys, weakref
sys.path.insert(0, "src")
from compiler import lex, Parser, SemanticChecker, remove_unused
def checked(path):
    tree = Parser(lex(open(path, "rb").read())).parse_program()
    tree.accept(SemanticChecker())
    return tree
tree = checked("tests/opt/warmup_shadow.txt")
outer_x = weakref.ref(tree.stmts[1])                 # i32 x{1}, read by nobody
print("outer x before:", outer_x() is not None, "refcount", sys.getrefcount(tree.stmts[1]) - 1)
remove_unused(tree)
print("outer x after the pass, no gc.collect():", outer_x())
tree = checked("tests/opt/fn_chain.txt")
unused = weakref.ref(tree.fns[2])                    # fn unused, whose exit points back at it
remove_unused(tree)
print("fn unused after the pass:", unused())
gc.collect()
print("fn unused after gc.collect():", unused())
```
This is the compiler's memory: the compiled programs have no heap, so nothing is freed in the binary.

Warm-up: program 1 removes `y` (iteration 1), then `x` (iteration 2), iteration 3 removes nothing; what remains is
`i32 z{15}` / `exit z`. Program 2 removes only the outer `x` (iteration 1: the `x` in `r := x` is the inner one),
iteration 2 removes nothing; the program still exits with 10.
