"""
compiler.py  —  Practice 5
Languages and Compilers Design · CS400

Usage:
    python3 src/compiler.py input.txt output.ll
    python3 src/compiler.py --ast input.txt
    lli output.ll

Pipeline: lexer -> parser (AST) -> SemanticChecker (types, symbols) -> CodeGen (llvmlite.ir).
Every check lives in the SemanticChecker; the code generator only reads node.type / node.decl.
"""

import os
import sys
from llvmlite import ir
import llvmlite.binding as llvm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_alpha(b):
    return (65 <= b <= 90) or (97 <= b <= 122) or b == 95

def is_digit(b):
    return 48 <= b <= 57

def is_alnum(b):
    return is_alpha(b) or is_digit(b)

KEYWORDS = {
    b"i32":   ("keyword", "typename"),
    b"i64":   ("keyword", "typename"),
    b"bool":  ("keyword", "typename"),
    b"mut":   ("keyword", "specifier"),
    b"exit":  ("keyword", "statement"),
    b"true":  ("keyword", "literal"),
    b"false": ("keyword", "literal"),
    b"if":    ("keyword", "control"),
    b"else":  ("keyword", "control"),
    b"while": ("keyword", "control"),
}

INT_TYPES = ("i32", "i64")
I32_MAX = 2**31 - 1
I64_MAX = 2**63 - 1

class Token:
    __slots__ = ("kind", "subkind", "text", "line", "col")
    def __init__(self, kind, text, line, col, subkind=None):
        self.kind = kind
        self.subkind = subkind
        self.text = text
        self.line = line
        self.col = col
    def __repr__(self):
        if self.subkind:
            return f"({self.text!r}, {self.kind}, {self.subkind})"
        return f"({self.text!r}, {self.kind})"

class CompileError(Exception):
    pass

def die(message):
    print(f"compilation error: {message}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# TASK 1 — Lexer
# ---------------------------------------------------------------------------
def lex(data: bytes):
    lines  = []
    tokens = []
    state = "START"
    start = 0
    start_col = 1
    line = 1
    col = 1
    i = 0
    while i <= len(data):
        b = data[i] if i < len(data) else None
        if state == "START":
            if b is None: break
            elif b in (32, 9, 13): pass
            elif b == 10:
                lines.append(tokens)
                tokens = []
                line += 1
                col = 0
            elif is_alpha(b): state, start, start_col = "IDENT", i, col
            elif is_digit(b): state, start, start_col = "NUMBER", i, col
            elif b == ord("{"): tokens.append(Token("lbrace", "{", line, col))
            elif b == ord("}"): tokens.append(Token("rbrace", "}", line, col))
            elif b == ord("+"): tokens.append(Token("op", "+", line, col, "+"))
            elif b == ord("-"): tokens.append(Token("op", "-", line, col, "-"))
            elif b == ord("*"): tokens.append(Token("op", "*", line, col, "*"))
            elif b == ord(":"): state, start_col = "COLON", col
            elif b == ord("="): state, start_col = "EQ", col
            elif b == ord("!"): state, start_col = "BANG", col
            else:
                ch = chr(b) if b < 128 else f"\\x{b:02x}"
                raise CompileError(f"line {line}:{col}: unexpected byte '{ch}'")
        elif state == "IDENT":
            if b is not None and is_alnum(b): pass
            else:
                word = data[start:i]
                if word in KEYWORDS:
                    kind, subkind = KEYWORDS[word]
                    tokens.append(Token(kind, word.decode(), line, start_col, subkind))
                else:
                    tokens.append(Token("ident", word.decode(), line, start_col))
                state = "START"
                continue
        elif state == "NUMBER":
            if b is not None and is_digit(b): pass
            elif b is not None and is_alpha(b):
                raise CompileError(f"line {line}:{col}: unexpected byte '{chr(b)}' inside number")
            else:
                word = data[start:i]
                tokens.append(Token("number", word.decode(), line, start_col))
                state = "START"
                continue
        elif state == "COLON":
            if b == ord("="):
                tokens.append(Token("op", ":=", line, start_col, ":="))
                state = "START"
            else:
                raise CompileError(f"line {line}:{start_col}: unexpected byte ':'")
        elif state == "EQ":
            if b == ord("="):
                tokens.append(Token("op", "==", line, start_col, "=="))
                state = "START"
            else:
                raise CompileError(f"line {line}:{start_col}: expected '==' (a single '=' is not an operator)")
        elif state == "BANG":
            if b == ord("="):
                tokens.append(Token("op", "!=", line, start_col, "!="))
                state = "START"
            else:                                   # '!' alone: emit it, read this byte again
                tokens.append(Token("op", "!", line, start_col, "!"))
                state = "START"
                continue
        i += 1
        col += 1
    if tokens: lines.append(tokens)
    return lines

def dump_tokens(lines):
    for row in lines:
        print("  ".join(repr(t) for t in row))

# ---------------------------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------------------------
class Node:
    kind = None
    def dump(self, indent=0):
        raise NotImplementedError()
    def accept(self, visitor):
        return getattr(visitor, "visit_" + self.kind)(self)

class ProgramNode(Node):
    kind = "program"
    def __init__(self, stmts, exit_node):
        self.stmts = stmts
        self.exit_node = exit_node
    def dump(self, indent=0):
        print(" " * indent + "Program")
        for stmt in self.stmts:
            stmt.dump(indent + 2)
        if self.exit_node:
            self.exit_node.dump(indent + 2)

class DeclNode(Node):
    kind = "decl"
    def __init__(self, line, col, type_name, name, mutable, init):
        self.line, self.col = line, col
        self.type_name, self.name, self.mutable, self.init = type_name, name, mutable, init
        self.slot = None              # set by CodeGen: the alloca of this variable
    def dump(self, indent=0):
        print(" " * indent + f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}")
        self.init.dump(indent + 2)

class AssignNode(Node):
    kind = "assign"
    def __init__(self, line, col, name, value):
        self.line, self.col, self.name, self.value = line, col, name, value
        self.decl = None              # set by SemanticChecker
    def dump(self, indent=0):
        print(" " * indent + f"Assign {self.name}")
        self.value.dump(indent + 2)

class ExitNode(Node):
    kind = "exit"
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
    def dump(self, indent=0):
        print(" " * indent + "Exit")
        self.value.dump(indent + 2)

class BinOpNode(Node):
    kind = "binop"
    def __init__(self, line, col, op, left, right):
        self.line, self.col, self.op, self.left, self.right = line, col, op, left, right
        self.type = None              # result type, set by SemanticChecker
        self.operand_type = None      # the common width both operands are widened to
    def dump(self, indent=0):
        print(" " * indent + f"BinOp {self.op}")
        self.left.dump(indent + 2)
        self.right.dump(indent + 2)

class VarNode(Node):
    kind = "var"
    def __init__(self, line, col, name):
        self.line, self.col, self.name = line, col, name
        self.type = None
        self.decl = None
    def dump(self, indent=0):
        print(" " * indent + f"Var {self.name}")

class ConstNode(Node):
    kind = "const"
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
        self.type = None
    def dump(self, indent=0):
        print(" " * indent + f"Const {self.value}")

class NotNode(Node):
    kind = "not"
    def __init__(self, line, col, operand):
        self.line, self.col, self.operand = line, col, operand
        self.type = None
    def dump(self, indent=0):
        print(" " * indent + "Not")
        self.operand.dump(indent + 2)

class BlockNode(Node):
    kind = "block"
    def __init__(self, line, col, stmts, exit_node):
        self.line, self.col, self.stmts, self.exit_node = line, col, stmts, exit_node
    def dump(self, indent=0):
        print(" " * indent + "Block")
        for stmt in self.stmts:
            stmt.dump(indent + 2)
        if self.exit_node:
            self.exit_node.dump(indent + 2)

class IfNode(Node):
    kind = "if"
    def __init__(self, line, col, cond, then_block, else_block):
        self.line, self.col = line, col
        self.cond, self.then_block, self.else_block = cond, then_block, else_block
    def dump(self, indent=0):
        print(" " * indent + "If")
        self.cond.dump(indent + 2)
        self.then_block.dump(indent + 2)
        if self.else_block:
            self.else_block.dump(indent + 2)

class WhileNode(Node):
    kind = "while"
    def __init__(self, line, col, cond, body):
        self.line, self.col, self.cond, self.body = line, col, cond, body
    def dump(self, indent=0):
        print(" " * indent + "While")
        self.cond.dump(indent + 2)
        self.body.dump(indent + 2)

class BoolNode(Node):
    kind = "bool"
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
        self.type = "bool"
    def dump(self, indent=0):
        print(" " * indent + f"Bool {'true' if self.value else 'false'}")

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class Parser:
    """Recursive descent with two cursors: a line cursor (peek_line / next_line)
    and a token cursor inside the current line (peek / eat)."""

    def __init__(self, lines):
        self.lines = [toks for toks in lines if toks]   # blank lines carry no tokens
        self.li = -1
        self.toks = []
        self.pos = 0
        self.last_line = 1
        self.last_col = 1

    # --- line cursor ---
    def peek_line(self):
        return self.lines[self.li + 1] if self.li + 1 < len(self.lines) else None

    def next_line(self):
        self.li += 1
        self.toks = self.lines[self.li]
        self.pos = 0
        self.last_line, self.last_col = self.toks[0].line, self.toks[0].col

    # --- token cursor ---
    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        self.last_line = tok.line
        self.last_col = tok.col + len(tok.text)
        return tok

    def raise_err(self, msg):
        tok = self.peek()
        if tok is not None:
            raise CompileError(f"line {tok.line}:{tok.col}: {msg}")
        raise CompileError(f"line {self.last_line}:{self.last_col}: {msg}")

    def end_of_line(self):
        if self.peek() is not None:
            self.raise_err(f"unexpected '{self.peek().text}' after the statement")

    # program ::= { statement } exit
    def parse_program(self):
        stmts = []
        while True:
            toks = self.peek_line()
            if toks is None:
                raise CompileError("program without exit")
            if toks[0].text == "exit":
                self.next_line()
                exit_node = self.parse_exit()
                break
            stmts.append(self.parse_statement_line())
        if self.peek_line() is not None:
            self.next_line()
            self.raise_err("unexpected statement after exit")
        return ProgramNode(stmts, exit_node)

    # exit ::= "exit" factor
    def parse_exit(self):
        tok = self.eat()
        val = self.parse_factor()
        if self.peek() is not None:
            self.raise_err(f"unexpected '{self.peek().text}' after exit")
        return ExitNode(tok.line, tok.col, val)

    # statement ::= decl | assign | if | while      (consumes one line, or several for if/while)
    def parse_statement_line(self):
        self.next_line()
        tok = self.peek()
        if tok.text == "if":
            return self.parse_if()
        if tok.text == "while":
            return self.parse_while()
        if tok.text == "else":
            self.raise_err("'else' without an 'if'")
        if tok.kind in ("lbrace", "rbrace"):
            self.raise_err(f"unexpected '{tok.text}'")
        node = self.parse_statement()
        self.end_of_line()
        return node

    def parse_statement(self):
        tok = self.peek()
        if tok.kind == "keyword" and tok.subkind == "typename":
            return self.parse_decl()
        elif tok.kind == "ident":
            return self.parse_assign()
        else:
            self.raise_err(f"cannot start a statement with '{tok.text}'")

    # if ::= "if" expr NL block [ "else" NL block ]
    def parse_if(self):
        if_tok = self.eat()
        cond = self.parse_expr()
        self.end_of_line()
        then_block = self.parse_block(if_tok)
        else_block = None
        toks = self.peek_line()
        if toks is not None and toks[0].text == "else":
            self.next_line()
            else_tok = self.eat()
            self.end_of_line()
            else_block = self.parse_block(else_tok)
        return IfNode(if_tok.line, if_tok.col, cond, then_block, else_block)

    # while ::= "while" expr NL block
    def parse_while(self):
        while_tok = self.eat()
        cond = self.parse_expr()
        self.end_of_line()
        return WhileNode(while_tok.line, while_tok.col, cond, self.parse_block(while_tok))

    # block ::= "{" NL { statement } [ exit NL ] "}" NL     (not empty)
    def parse_block(self, owner):
        toks = self.peek_line()
        if toks is None:
            raise CompileError(f"line {self.last_line}:{self.last_col}: expected '{{' on its own line "
                               f"after '{owner.text}', found end of file")
        self.next_line()
        if toks[0].kind != "lbrace":
            self.raise_err(f"expected '{{' on its own line after '{owner.text}', got '{toks[0].text}'")
        lbrace = self.eat()
        self.end_of_line()
        stmts, exit_node = [], None
        while True:
            toks = self.peek_line()
            if toks is None:
                raise CompileError(f"line {lbrace.line}:{lbrace.col}: '{{' is never closed")
            if toks[0].kind == "rbrace":
                self.next_line()
                self.eat()
                self.end_of_line()
                break
            if exit_node is not None:
                self.next_line()
                self.raise_err("statement after 'exit' in the same block")
            if toks[0].text == "exit":
                self.next_line()
                exit_node = self.parse_exit()
            else:
                stmts.append(self.parse_statement_line())
        if not stmts and exit_node is None:
            raise CompileError(f"line {lbrace.line}:{lbrace.col}: empty block")
        return BlockNode(lbrace.line, lbrace.col, stmts, exit_node)

    def parse_decl(self):
        type_tok = self.eat()
        mutable = False
        tok = self.peek()
        if tok is not None and tok.text == "mut":
            self.eat()
            mutable = True

        tok = self.peek()
        if tok is None or tok.kind != "ident":
            self.raise_err("expected a variable name")
        name_tok = self.eat()

        tok = self.peek()
        if tok is None or tok.kind != "lbrace":
            raise CompileError(f"line {name_tok.line}:{name_tok.col}: variable '{name_tok.text}' needs an initialiser in {{}}")
        lbrace_tok = self.eat()

        expr = self.parse_expr()

        tok = self.peek()
        if tok is None:
            raise CompileError(f"line {lbrace_tok.line}:{lbrace_tok.col}: '{{' is not closed before the end of the line")
        if tok.kind != "rbrace":
            self.raise_err(f"expected '}}', got '{tok.text}'")
        self.eat()

        return DeclNode(name_tok.line, name_tok.col, type_tok.text, name_tok.text, mutable, expr)

    def parse_assign(self):
        name_tok = self.eat()
        tok = self.peek()
        if tok is None or tok.text != ":=":
            if tok is not None:
                raise CompileError(f"line {tok.line}:{tok.col}: expected ':=' after '{name_tok.text}', got '{tok.text}'")
            else:
                self.raise_err(f"expected ':=' after '{name_tok.text}', found end of line")
        self.eat()
        expr = self.parse_expr()
        return AssignNode(name_tok.line, name_tok.col, name_tok.text, expr)

    # expr ::= arith [ ("==" | "!=") arith ]
    def parse_expr(self):
        node = self.parse_arith()
        tok = self.peek()
        if tok is not None and tok.kind == "op" and tok.text in ("==", "!="):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_arith())
            tok = self.peek()
            if tok is not None and tok.kind == "op" and tok.text in ("==", "!="):
                self.raise_err(f"only one comparison is allowed in an expression, got a second '{tok.text}'")
        return node

    # arith ::= term { ("+" | "-") term }
    def parse_arith(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text in ("+", "-"):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_factor()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text == "*":
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_factor())
        return node

    # factor ::= number | "true" | "false" | ident | "!" factor
    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            self.raise_err("expected a constant or a variable, found end of line")
        if tok.kind == "op" and tok.text == "!":
            self.eat()
            return NotNode(tok.line, tok.col, self.parse_factor())
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        elif tok.subkind == "literal":
            self.eat()
            return BoolNode(tok.line, tok.col, tok.text == "true")
        elif tok.kind == "ident":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        else:
            self.raise_err(f"expected a constant or a variable, got '{tok.text}'")

# ---------------------------------------------------------------------------
# TASK 2 — Semantic pass
# ---------------------------------------------------------------------------
class SemanticChecker:
    """Walks the tree before any IR exists. Sets node.type on every expression
    and node.decl on every Var / Assign; raises CompileError with line:col."""

    def __init__(self):
        self.scopes = [{}]            # stack of frames: name -> DeclNode; [0] is the program

    def visit_program(self, node):
        for stmt in node.stmts:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:              # only the innermost frame: shadowing is allowed
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is already declared in this block")
        node.init.accept(self)                          # before the name exists
        self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.scopes[-1][node.name] = node

    def visit_block(self, node):
        self.scopes.append({})
        for stmt in node.stmts:
            stmt.accept(self)
        if node.exit_node:
            node.exit_node.accept(self)
        self.scopes.pop()

    def visit_if(self, node):
        self.check_condition(node, "if")
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        self.check_condition(node, "while")
        node.body.accept(self)

    def check_condition(self, node, what):
        t = node.cond.accept(self)
        if t != "bool":
            raise CompileError(f"line {node.line}:{node.col}: the condition of '{what}' must be bool, got {t}")

    def visit_not(self, node):
        t = node.operand.accept(self)
        if t != "bool":
            raise CompileError(f"line {node.line}:{node.col}: cannot apply '!' to {t}")
        node.type = "bool"
        return node.type

    def visit_assign(self, node):
        decl = self.lookup(node, node.name)
        if not decl.mutable:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        node.decl = decl
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_binop(self, node):
        lt, rt = node.left.accept(self), node.right.accept(self)
        if node.op in ("+", "-", "*"):
            for t in (lt, rt):
                if t not in INT_TYPES:
                    raise CompileError(f"line {node.line}:{node.col}: cannot apply '{node.op}' to {t}")
            node.type = node.operand_type = wider(lt, rt)
        else:
            if (lt in INT_TYPES) != (rt in INT_TYPES):
                raise CompileError(f"line {node.line}:{node.col}: cannot compare {lt} with {rt}")
            node.operand_type = wider(lt, rt) if lt in INT_TYPES else "bool"
            node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node, node.name)
        node.type = node.decl.type_name
        return node.type

    def visit_const(self, node):
        if node.value <= I32_MAX:
            node.type = "i32"
        elif node.value <= I64_MAX:
            node.type = "i64"
        else:
            raise CompileError(f"line {node.line}:{node.col}: constant {node.value} does not fit in i64")
        return node.type

    def visit_bool(self, node):
        return node.type

    def lookup(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        raise CompileError(f"line {node.line}:{node.col}: variable '{name}' is used before its declaration")

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if isinstance(expr, ConstNode) and want in INT_TYPES:
            raise CompileError(f"line {expr.line}:{expr.col}: constant {expr.value} does not fit in {want}")
        raise CompileError(f"line {at.line}:{at.col}: cannot {what} of type {want} "
                           f"with a value of type {have}")

def wider(a, b):
    return "i64" if "i64" in (a, b) else "i32"

# ---------------------------------------------------------------------------
# TASK 3 — Code Generation (reads node.type / node.decl, checks nothing)
# ---------------------------------------------------------------------------
I1, I8, I32, I64 = ir.IntType(1), ir.IntType(8), ir.IntType(32), ir.IntType(64)
LLVM_TYPES = {"i32": I32, "i64": I64, "bool": I1}

class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="program")
        self.module.triple = llvm.get_default_triple()

        printf_ty = ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True)
        self.printf_fn = ir.Function(self.module, printf_ty, name="printf")

        self.main_fn = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.entry_block = self.main_fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(self.entry_block)

        self.fmt_int  = self.global_string(".fmt_int",  b"Program exit with result %lld\n\0")
        self.fmt_bool = self.global_string(".fmt_bool", b"Program exit with result %s\n\0")
        self.str_true  = self.global_string(".str_true",  b"true\0")
        self.str_false = self.global_string(".str_false", b"false\0")

    def global_string(self, name, data):
        const = ir.Constant(ir.ArrayType(I8, len(data)), bytearray(data))
        g = ir.GlobalVariable(self.module, const.type, name=name)
        g.global_constant = True
        g.linkage = "private"
        g.initializer = const
        return g

    def cstr(self, g):
        zero = ir.Constant(I64, 0)
        return self.builder.gep(g, [zero, zero], inbounds=True)

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    def visit_program(self, node):
        for stmt in node.stmts:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_decl(self, node):
        val = self.coerce(node.init.accept(self), node.init.type, node.type_name)
        node.slot = self.alloca(LLVM_TYPES[node.type_name], node.name)
        self.builder.store(val, node.slot)

    def alloca(self, ty, name):
        """Every slot goes to the start of the entry block, wherever the builder is now."""
        here = self.builder.block
        self.builder.position_at_start(self.entry_block)
        slot = self.builder.alloca(ty, name=name)
        self.builder.position_at_end(here)
        return slot

    def visit_block(self, node):
        for stmt in node.stmts:
            stmt.accept(self)
        if node.exit_node:
            node.exit_node.accept(self)

    def visit_if(self, node):
        cond = node.cond.accept(self)
        then_bb = self.main_fn.append_basic_block("then")
        else_bb = self.main_fn.append_basic_block("else") if node.else_block else None
        merge_bb = self.main_fn.append_basic_block("merge")
        self.builder.cbranch(cond, then_bb, else_bb or merge_bb)

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        if not self.builder.block.is_terminated:      # an arm that exited already has its ret
            self.builder.branch(merge_bb)

        if else_bb:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def visit_while(self, node):
        cond_bb = self.main_fn.append_basic_block("cond")
        body_bb = self.main_fn.append_basic_block("body")
        end_bb = self.main_fn.append_basic_block("end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        self.builder.cbranch(node.cond.accept(self), body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)              # the back edge

        self.builder.position_at_end(end_bb)

    def visit_not(self, node):
        return self.builder.not_(node.operand.accept(self), name="not")

    def visit_assign(self, node):
        val = self.coerce(node.value.accept(self), node.value.type, node.decl.type_name)
        self.builder.store(val, node.decl.slot)

    def visit_exit(self, node):
        val = node.value.accept(self)
        if node.value.type == "bool":
            text = self.builder.select(val, self.cstr(self.str_true), self.cstr(self.str_false), name="text")
            self.builder.call(self.printf_fn, [self.cstr(self.fmt_bool), text])
        else:
            val = self.coerce(val, node.value.type, "i64")
            self.builder.call(self.printf_fn, [self.cstr(self.fmt_int), val])
        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        lhs = self.coerce(node.left.accept(self), node.left.type, node.operand_type)
        rhs = self.coerce(node.right.accept(self), node.right.type, node.operand_type)
        if node.op == "+":
            return self.builder.add(lhs, rhs, name="add")
        elif node.op == "-":
            return self.builder.sub(lhs, rhs, name="sub")
        elif node.op == "*":
            return self.builder.mul(lhs, rhs, name="mul")
        elif node.op == "==":
            return self.builder.icmp_signed("==", lhs, rhs, name="eq")
        else:
            return self.builder.icmp_signed("!=", lhs, rhs, name="ne")

    def visit_var(self, node):
        return self.builder.load(node.decl.slot, name=node.name)

    def visit_const(self, node):
        return ir.Constant(LLVM_TYPES[node.type], node.value)

    def visit_bool(self, node):
        return ir.Constant(I1, 1 if node.value else 0)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 compiler.py [--ast] input.txt [output.ll]", file=sys.stderr)
        sys.exit(1)

    dump_ast = False
    args = sys.argv[1:]
    if args[0] == "--ast":
        dump_ast = True
        args = args[1:]
        if len(args) != 1:
            print("Usage: python3 compiler.py --ast input.txt", file=sys.stderr)
            sys.exit(1)
        src_path = args[0]
        out_path = None
    else:
        if len(args) != 2:
            print("Usage: python3 compiler.py input.txt output.ll", file=sys.stderr)
            sys.exit(1)
        src_path = args[0]
        out_path = args[1]

    try:
        with open(src_path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"compilation error: cannot read '{src_path}': {e}", file=sys.stderr)
        sys.exit(1)

    try:
        lines = lex(data)
        if os.environ.get("LEX_DUMP"):
            dump_tokens(lines)
            sys.exit(0)

        ast = Parser(lines).parse_program()

        if dump_ast:
            ast.dump()
            sys.exit(0)

        ast.accept(SemanticChecker())     # every check happens here, before any IR
        cg = CodeGen()
        ast.accept(cg)
        ir_text = str(cg.module)
    except CompileError as e:
        die(str(e))

    try:
        with open(out_path, "w") as f:
            f.write(ir_text)
    except OSError as e:
        print(f"compilation error: cannot write '{out_path}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
