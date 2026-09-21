"""
compiler.py  —  Practice 3
Languages and Compilers Design · CS400

Usage:
    python3 compiler.py input.txt output.ll
    python3 compiler.py --ast input.txt
    lli output.ll

The compiler reads a source file, lexes it, parses it into an AST via recursive descent,
and then generates LLVM IR by walking the AST.
"""

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
    b"i32":  ("keyword", "typename"),
    b"mut":  ("keyword", "specifier"),
    b"exit": ("keyword", "statement"),
}

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
            elif b in (32, 9): pass
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
            elif b == ord("="): raise CompileError(f"line {line}:{col}: unexpected byte '='")
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
    def dump(self, indent=0):
        raise NotImplementedError()
    def codegen(self, cg):
        raise NotImplementedError()

class ProgramNode(Node):
    def __init__(self, stmts, exit_node):
        self.stmts = stmts
        self.exit_node = exit_node
    def dump(self, indent=0):
        print(" " * indent + "Program")
        for stmt in self.stmts:
            stmt.dump(indent + 2)
        if self.exit_node:
            self.exit_node.dump(indent + 2)
    def codegen(self, cg):
        for stmt in self.stmts:
            stmt.codegen(cg)
        self.exit_node.codegen(cg)

class StmtNode(Node): pass

class DeclNode(StmtNode):
    def __init__(self, line, col, name, mutable, init):
        self.line, self.col, self.name, self.mutable, self.init = line, col, name, mutable, init
    def dump(self, indent=0):
        print(" " * indent + f"Decl {self.name} {'mut' if self.mutable else 'const'}")
        self.init.dump(indent + 2)
    def codegen(self, cg):
        cg.visit_decl(self)

class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        self.line, self.col, self.name, self.value = line, col, name, value
    def dump(self, indent=0):
        print(" " * indent + f"Assign {self.name}")
        self.value.dump(indent + 2)
    def codegen(self, cg):
        cg.visit_assign(self)

class ExitNode(Node):
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
    def dump(self, indent=0):
        print(" " * indent + "Exit")
        self.value.dump(indent + 2)
    def codegen(self, cg):
        cg.visit_exit(self)

class ExprNode(Node): pass

class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        self.line, self.col, self.op, self.left, self.right = line, col, op, left, right
    def dump(self, indent=0):
        print(" " * indent + f"BinOp {self.op}")
        self.left.dump(indent + 2)
        self.right.dump(indent + 2)
    def codegen(self, cg):
        return cg.visit_binop(self)

class VarNode(ExprNode):
    def __init__(self, line, col, name):
        self.line, self.col, self.name = line, col, name
    def dump(self, indent=0):
        print(" " * indent + f"Var {self.name}")
    def codegen(self, cg):
        return cg.visit_var(self)

class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
    def dump(self, indent=0):
        print(" " * indent + f"Const {self.value}")
    def codegen(self, cg):
        return cg.visit_const(self)

# ---------------------------------------------------------------------------
# TASK 2 & 4 — Parser
# ---------------------------------------------------------------------------
class Parser:
    def __init__(self, lines):
        self.lines = lines
        self.toks = []
        self.pos = 0
        self.last_line = 1
        self.last_col = 1

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
        else:
            raise CompileError(f"line {self.last_line}:{self.last_col}: {msg}")

    def parse_program(self):
        stmts = []
        exit_node = None
        for toks in self.lines:
            if not toks:
                continue
            self.toks = toks
            self.pos = 0
            if self.toks:
                self.last_line = self.toks[0].line
            
            tok = self.peek()
            if tok.kind == "keyword" and tok.text == "exit":
                if exit_node is not None:
                    self.raise_err("duplicate 'exit' statement")
                self.eat()
                
                tok2 = self.peek()
                if tok2 is None:
                    self.raise_err("expected a constant or a variable, found end of line")
                elif tok2.kind not in ("number", "ident"):
                    self.raise_err(f"expected a constant or a variable, got '{tok2.text}'")
                    
                val = self.parse_factor()
                if self.peek() is not None:
                    self.raise_err(f"unexpected '{self.peek().text}' after exit")
                exit_node = ExitNode(tok.line, tok.col, val)
            else:
                if exit_node is not None:
                    self.raise_err("unexpected statement after exit")
                stmts.append(self.parse_statement())
                if self.peek() is not None:
                    self.raise_err(f"unexpected '{self.peek().text}' after the statement")

        if exit_node is None:
            raise CompileError("program without exit")
            
        return ProgramNode(stmts, exit_node)

    def parse_statement(self):
        tok = self.peek()
        if tok is None:
            self.raise_err("expected statement")
        if tok.kind == "keyword" and tok.text == "i32":
            return self.parse_decl()
        elif tok.kind == "ident":
            return self.parse_assign()
        else:
            self.raise_err(f"cannot start a statement with '{tok.text}'")

    def parse_decl(self):
        self.eat() # i32
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
        if tok is None or tok.kind != "rbrace":
            raise CompileError(f"line {lbrace_tok.line}:{lbrace_tok.col}: '{{' is not closed before the end of the line")
        self.eat()
        
        return DeclNode(name_tok.line, name_tok.col, name_tok.text, mutable, expr)

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

    def parse_expr(self):
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

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            self.raise_err("expected a constant or a variable, found end of line")
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        elif tok.kind == "ident":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        else:
            self.raise_err(f"expected a constant or a variable, got '{tok.text}'")

# ---------------------------------------------------------------------------
# TASK 3 — Code Generation
# ---------------------------------------------------------------------------
class CodeGen:
    def __init__(self):
        self.i32 = ir.IntType(32)
        self.i8 = ir.IntType(8)
        self.i64 = ir.IntType(64)
        
        self.module = ir.Module(name="program")
        self.module.triple = llvm.get_default_triple()
        
        self.printf_ty = ir.FunctionType(self.i32, [ir.PointerType(self.i8)], var_arg=True)
        self.printf_fn = ir.Function(self.module, self.printf_ty, name="printf")
        
        main_ty = ir.FunctionType(self.i32, [])
        self.main_fn = ir.Function(self.module, main_ty, name="main")
        block = self.main_fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)
        
        fmt_str = b"Program exit with result %d\n\0"
        fmt_const = ir.Constant(ir.ArrayType(self.i8, len(fmt_str)), bytearray(fmt_str))
        self.fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=".fmt")
        self.fmt_global.global_constant = True
        self.fmt_global.initializer = fmt_const
        
        self.symbols = {}

    def gep_fmt(self):
        zero = ir.Constant(self.i64, 0)
        return self.builder.gep(self.fmt_global, [zero, zero], inbounds=True)

    def visit_decl(self, node):
        if node.name in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is declared twice")
        val = node.init.codegen(self)
        alloca = self.builder.alloca(self.i32, name=node.name)
        self.builder.store(val, alloca)
        self.symbols[node.name] = {"alloca": alloca, "mut": node.mutable}

    def visit_assign(self, node):
        if node.name not in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is used before its declaration")
        if not self.symbols[node.name]["mut"]:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        val = node.value.codegen(self)
        self.builder.store(val, self.symbols[node.name]["alloca"])

    def visit_exit(self, node):
        val = node.value.codegen(self)
        self.builder.call(self.printf_fn, [self.gep_fmt(), val])
        self.builder.ret(ir.Constant(self.i32, 0))

    def visit_binop(self, node):
        left_val = node.left.codegen(self)
        right_val = node.right.codegen(self)
        if node.op == "+":
            return self.builder.add(left_val, right_val, name="add")
        elif node.op == "-":
            return self.builder.sub(left_val, right_val, name="sub")
        elif node.op == "*":
            return self.builder.mul(left_val, right_val, name="mul")
        else:
            raise CompileError(f"unknown operator {node.op}")

    def visit_var(self, node):
        if node.name not in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is used before its declaration")
        return self.builder.load(self.symbols[node.name]["alloca"], name=node.name)

    def visit_const(self, node):
        return ir.Constant(self.i32, node.value)

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

    import os
    try:
        lines = lex(data)
        if os.environ.get("LEX_DUMP"):
            dump_tokens(lines)
            sys.exit(0)
            
        parser = Parser(lines)
        ast = parser.parse_program()
        
        if dump_ast:
            ast.dump()
            sys.exit(0)
            
        cg = CodeGen()
        ast.codegen(cg)
        ir_text = str(cg.module)
        
    except CompileError as e:
        die(str(e))

    try:
        if out_path:
            with open(out_path, "w") as f:
                f.write(ir_text)
    except OSError as e:
        print(f"compilation error: cannot write '{out_path}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
