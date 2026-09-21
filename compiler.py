"""
compiler.py  —  Practice 2
Languages and Compilers Design · CS400

Usage:
    python3 compiler.py input.txt output.ll
    lli output.ll

The compiler reads a source file, lexes it into typed tokens using a
hand-written byte-level state machine, validates syntax and semantics,
and emits LLVM IR via llvmlite.ir.
"""

import sys
from llvmlite import ir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_alpha(b):
    """True for ASCII letters and underscore."""
    return (65 <= b <= 90) or (97 <= b <= 122) or b == 95   # A-Z, a-z, _

def is_digit(b):
    """True for ASCII decimal digits."""
    return 48 <= b <= 57   # 0-9

def is_alnum(b):
    return is_alpha(b) or is_digit(b)


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

KEYWORDS = {
    b"i32":  ("keyword", "typename"),
    b"mut":  ("keyword", "specifier"),
    b"exit": ("keyword", "statement"),
}


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

class Token:
    """A single lexed token."""
    __slots__ = ("kind", "subkind", "text", "line", "col")

    def __init__(self, kind, text, line, col, subkind=None):
        self.kind    = kind     # "keyword" | "ident" | "number" | "lbrace" |
                                # "rbrace" | "operator" | "endline"
        self.subkind = subkind  # for keywords: "typename" | "specifier" | "statement"
                                # for operators: ":=" | "+" | "-" | "*"
        self.text    = text     # str
        self.line    = line     # 1-based
        self.col     = col      # 1-based

    def __repr__(self):
        if self.subkind:
            return f"({self.text!r}, {self.kind}, {self.subkind})"
        return f"({self.text!r}, {self.kind})"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class CompileError(Exception):
    """Raised on any lexical, syntactic or semantic error."""
    def __init__(self, message):
        super().__init__(message)


def die(message):
    """Print compilation error to stderr and exit non-zero."""
    print(f"compilation error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# TASK 1 — Lexer
# ---------------------------------------------------------------------------

def lex(data: bytes):
    """
    Hand-written byte-by-byte state machine.
    Returns a list of lines, each a list of Token objects.
    Newline tokens are NOT included in the inner lists; instead each
    inner list represents one source line.

    States: START, IDENT, NUMBER, COLON
    """
    lines  = []   # list of completed lines
    tokens = []   # tokens on the current line

    state = "START"
    start = 0       # byte index where current token started
    start_col = 1   # column of that byte

    line = 1
    col  = 1        # column of the *current* byte (1-based)
    i    = 0

    # We run one extra iteration (i == len(data)) to flush any open token.
    while i <= len(data):
        b = data[i] if i < len(data) else None   # None = end-of-input sentinel

        # ------------------------------------------------------------------ START
        if state == "START":
            if b is None:
                break

            elif b in (32, 9):          # space or tab — skip
                pass

            elif b == 10:               # newline — end of line
                lines.append(tokens)
                tokens = []
                line += 1
                col = 0                 # will become 1 after i += 1 below

            elif is_alpha(b):           # start of identifier / keyword
                state     = "IDENT"
                start     = i
                start_col = col

            elif is_digit(b):           # start of number
                state     = "NUMBER"
                start     = i
                start_col = col

            elif b == ord("{"):
                tokens.append(Token("lbrace", "{", line, col))

            elif b == ord("}"):
                tokens.append(Token("rbrace", "}", line, col))

            elif b == ord("+"):
                tokens.append(Token("operator", "+", line, col, "+"))

            elif b == ord("-"):
                tokens.append(Token("operator", "-", line, col, "-"))

            elif b == ord("*"):
                tokens.append(Token("operator", "*", line, col, "*"))

            elif b == ord(":"):
                state     = "COLON"
                start_col = col

            elif b == ord("="):
                # lone '=' is a lexical error
                raise CompileError(
                    f"line {line}:{col}: unexpected byte '='"
                )

            else:
                ch = chr(b) if b < 128 else f"\\x{b:02x}"
                raise CompileError(
                    f"line {line}:{col}: unexpected byte '{ch}'"
                )

        # ------------------------------------------------------------------ IDENT
        elif state == "IDENT":
            if b is not None and is_alnum(b):
                pass   # keep collecting

            else:
                word = data[start:i]
                if word in KEYWORDS:
                    kind, subkind = KEYWORDS[word]
                    tokens.append(Token(kind, word.decode(), line, start_col, subkind))
                else:
                    tokens.append(Token("ident", word.decode(), line, start_col))
                state = "START"
                continue   # re-read byte b in START (do NOT advance i)

        # ------------------------------------------------------------------ NUMBER
        elif state == "NUMBER":
            if b is not None and is_digit(b):
                pass   # keep collecting

            elif b is not None and is_alpha(b):
                # letter immediately after digit: lexical error
                ch = chr(b)
                raise CompileError(
                    f"line {line}:{col}: unexpected byte '{ch}' inside number"
                )

            else:
                word = data[start:i]
                tokens.append(Token("number", word.decode(), line, start_col))
                state = "START"
                continue   # re-read byte b in START

        # ------------------------------------------------------------------ COLON
        elif state == "COLON":
            if b == ord("="):
                tokens.append(Token("operator", ":=", line, start_col, ":="))
                state = "START"

            else:
                # ':' not followed by '=' is a lexical error
                raise CompileError(
                    f"line {line}:{start_col}: unexpected byte ':'"
                )

        # advance
        i   += 1
        col += 1

    # Any tokens left on the last line (file did not end with newline)
    if tokens:
        lines.append(tokens)

    return lines


# ---------------------------------------------------------------------------
# Worked-example pretty-print helper  (used by --lex-dump)
# ---------------------------------------------------------------------------

def dump_tokens(lines):
    for row in lines:
        print("  ".join(repr(t) for t in row))


# ---------------------------------------------------------------------------
# TASK 2 — Syntax / Semantics + Code Generation
# ---------------------------------------------------------------------------

def parse_and_build(lines):
    """
    Walk the token lines, validate syntax and semantics, build LLVM IR.
    Returns the llvmlite module as a string.

    Grammar (one statement per line):
        decl   ::= 'i32' ['mut'] IDENT '{' expr '}'
        assign ::= IDENT ':=' expr
        exit   ::= 'exit' (IDENT | NUMBER)
        expr   ::= val | val OP val
        val    ::= IDENT | NUMBER
        OP     ::= '+' | '-' | '*'
    """

    # ---- llvmlite setup ----
    i32  = ir.IntType(32)
    i8   = ir.IntType(8)
    i64  = ir.IntType(64)
    void = ir.VoidType()

    module = ir.Module(name="program")
    module.triple = "x86_64-unknown-linux-gnu"

    # Declare printf
    printf_ty  = ir.FunctionType(i32, [ir.PointerType(i8)], var_arg=True)
    printf_fn  = ir.Function(module, printf_ty, name="printf")

    # Define main
    main_ty    = ir.FunctionType(i32, [])
    main_fn    = ir.Function(module, main_ty, name="main")
    block      = main_fn.append_basic_block(name="entry")
    builder    = ir.IRBuilder(block)

    # Format string for printf
    fmt_str    = b"Program exit with result %d\n\0"
    fmt_const  = ir.Constant(ir.ArrayType(i8, len(fmt_str)),
                             bytearray(fmt_str))
    fmt_global = ir.GlobalVariable(module, fmt_const.type, name=".fmt")
    fmt_global.global_constant = True
    fmt_global.initializer     = fmt_const

    def gep_fmt():
        zero = ir.Constant(i64, 0)
        return builder.gep(fmt_global, [zero, zero], inbounds=True)

    # Symbol table: name -> {"alloca": ir.AllocaInstr, "mut": bool}
    symbols = {}

    def resolve_val(tok):
        """Return an IR Value for a NUMBER or IDENT token."""
        if tok.kind == "number":
            return ir.Constant(i32, int(tok.text))
        elif tok.kind == "ident":
            if tok.text not in symbols:
                raise CompileError(
                    f"line {tok.line}:{tok.col}: variable '{tok.text}' is used before its declaration"
                )
            return builder.load(symbols[tok.text]["alloca"], name=tok.text)
        else:
            raise CompileError(
                f"line {tok.line}:{tok.col}: expected a value, got '{tok.text}'"
            )

    def build_expr(toks, start_idx):
        """
        Parse expr starting at toks[start_idx].
        Returns (ir.Value, next_index).
        expr ::= val | val OP val
        """
        if start_idx >= len(toks):
            raise CompileError("expected expression")
        lhs = resolve_val(toks[start_idx])
        idx = start_idx + 1
        if idx < len(toks) and toks[idx].kind == "operator" and toks[idx].text in ("+", "-", "*"):
            op_tok = toks[idx]
            idx += 1
            if idx >= len(toks):
                raise CompileError(
                    f"line {op_tok.line}:{op_tok.col}: expected value after '{op_tok.text}'"
                )
            rhs = resolve_val(toks[idx])
            idx += 1
            if op_tok.text == "+":
                val = builder.add(lhs, rhs, name="add")
            elif op_tok.text == "-":
                val = builder.sub(lhs, rhs, name="sub")
            else:
                val = builder.mul(lhs, rhs, name="mul")
            return val, idx
        return lhs, idx

    exit_seen = False

    for lineno_0, toks in enumerate(lines):
        if not toks:
            continue   # blank line

        # Determine statement kind from first token
        first = toks[0]

        # ---- Declaration: i32 [mut] IDENT { expr } ----
        if first.kind == "keyword" and first.subkind == "typename":
            idx = 1
            is_mut = False

            # optional 'mut'
            if idx < len(toks) and toks[idx].kind == "keyword" and toks[idx].subkind == "specifier":
                is_mut = True
                idx += 1

            # variable name
            if idx >= len(toks) or toks[idx].kind != "ident":
                t = toks[idx] if idx < len(toks) else first
                raise CompileError(
                    f"line {t.line}:{t.col}: expected variable name after type"
                )
            name_tok = toks[idx]; idx += 1

            if name_tok.text in symbols:
                raise CompileError(
                    f"line {name_tok.line}:{name_tok.col}: variable '{name_tok.text}' is declared twice"
                )

            # mandatory '{'
            if idx >= len(toks) or toks[idx].kind != "lbrace":
                t = toks[idx] if idx < len(toks) else name_tok
                raise CompileError(
                    f"line {name_tok.line}:{name_tok.col}: variable '{name_tok.text}' needs an initialiser in {{}}"
                )
            lbrace_tok = toks[idx]; idx += 1

            # Check { is closed on the same line
            rbrace_idx = None
            for j in range(idx, len(toks)):
                if toks[j].kind == "rbrace":
                    rbrace_idx = j
                    break
            if rbrace_idx is None:
                raise CompileError(
                    f"line {lbrace_tok.line}:{lbrace_tok.col}: '{{' is not closed before the end of the line"
                )

            # expression inside braces
            val, expr_end = build_expr(toks, idx)
            if expr_end != rbrace_idx:
                t = toks[expr_end] if expr_end < len(toks) else lbrace_tok
                raise CompileError(
                    f"line {t.line}:{t.col}: unexpected token '{t.text}' inside initialiser"
                )
            idx = rbrace_idx + 1   # past '}'

            # extra tokens?
            if idx < len(toks):
                t = toks[idx]
                raise CompileError(
                    f"line {t.line}:{t.col}: unexpected token '{t.text}' after declaration"
                )

            # Emit IR
            alloca = builder.alloca(i32, name=name_tok.text)
            builder.store(val, alloca)
            symbols[name_tok.text] = {"alloca": alloca, "mut": is_mut}

        # ---- Assignment: IDENT := expr ----
        elif first.kind == "ident":
            if len(toks) < 2 or toks[1].kind != "operator" or toks[1].text != ":=":
                raise CompileError(
                    f"line {first.line}:{first.col}: expected ':=' after '{first.text}'"
                )
            if first.text not in symbols:
                raise CompileError(
                    f"line {first.line}:{first.col}: variable '{first.text}' is used before its declaration"
                )
            if not symbols[first.text]["mut"]:
                raise CompileError(
                    f"line {first.line}:{first.col}: cannot assign to '{first.text}': it is not mut"
                )
            val, idx = build_expr(toks, 2)
            if idx < len(toks):
                t = toks[idx]
                raise CompileError(
                    f"line {t.line}:{t.col}: unexpected token '{t.text}' after assignment"
                )
            builder.store(val, symbols[first.text]["alloca"])

        # ---- Exit: exit (IDENT | NUMBER) ----
        elif first.kind == "keyword" and first.subkind == "statement":
            if exit_seen:
                raise CompileError(
                    f"line {first.line}:{first.col}: duplicate 'exit' statement"
                )
            if len(toks) < 2:
                raise CompileError(
                    f"line {first.line}:{first.col}: 'exit' requires a value"
                )
            val_tok = toks[1]
            val = resolve_val(val_tok)
            if len(toks) > 2:
                t = toks[2]
                raise CompileError(
                    f"line {t.line}:{t.col}: unexpected token '{t.text}' after exit"
                )
            # printf("Program exit with result %d\n", val)
            builder.call(printf_fn, [gep_fmt(), val])
            builder.ret(ir.Constant(i32, 0))
            exit_seen = True

        else:
            raise CompileError(
                f"line {first.line}:{first.col}: unrecognised statement starting with '{first.text}'"
            )

    if not exit_seen:
        raise CompileError("program has no 'exit' statement")

    return str(module)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 compiler.py input.txt output.ll", file=sys.stderr)
        sys.exit(1)

    src_path = sys.argv[1]
    out_path = sys.argv[2]

    # Read source
    try:
        with open(src_path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"compilation error: cannot read '{src_path}': {e}", file=sys.stderr)
        sys.exit(1)

    # Lex
    try:
        lines = lex(data)
    except CompileError as e:
        die(str(e))

    # Debug dump when called with --lex-dump env var
    import os
    if os.environ.get("LEX_DUMP"):
        dump_tokens(lines)
        sys.exit(0)

    # Parse + build IR
    try:
        ir_text = parse_and_build(lines)
    except CompileError as e:
        die(str(e))

    # Write output
    try:
        with open(out_path, "w") as f:
            f.write(ir_text)
    except OSError as e:
        print(f"compilation error: cannot write '{out_path}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
