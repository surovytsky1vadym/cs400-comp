"""
compiler.py  —  Practice 8
Languages and Compilers Design · CS400

Usage:
    python3 src/compiler.py input.txt output.ll
    python3 src/compiler.py --ast input.txt
    python3 src/compiler.py --ast-checked input.txt
    lli output.ll

Pipeline: lexer -> parser (AST) -> SemanticChecker (types, symbols) -> CodeGen (llvmlite.ir).
Every check lives in the SemanticChecker; the code generator only reads what the checker stored
on the nodes (node.type, node.decl, node.fn, node.index).
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
    b"i32":    ("keyword", "typename"),
    b"i64":    ("keyword", "typename"),
    b"bool":   ("keyword", "typename"),
    b"mut":    ("keyword", "specifier"),
    b"exit":   ("keyword", "statement"),
    b"true":   ("keyword", "literal"),
    b"false":  ("keyword", "literal"),
    b"if":     ("keyword", "control"),
    b"else":   ("keyword", "control"),
    b"while":  ("keyword", "control"),
    b"struct": ("keyword", "declaration"),
    b"fn":     ("keyword", "declaration"),
}

BUILTIN_TYPES = ("i32", "i64", "bool")
RESERVED = ("read", "print")                     # built-ins: never a user name
INT_TYPES = ("i32", "i64")
I32_MAX = 2**31 - 1
I64_MAX = 2**63 - 1

SINGLE_BYTE_TOKENS = {
    ord("{"): "lbrace", ord("}"): "rbrace",
    ord("("): "lparen", ord(")"): "rparen",
    ord(","): "comma",  ord("."): "dot",
    ord("["): "lbracket", ord("]"): "rbracket",
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
# Lexer
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
            elif b in SINGLE_BYTE_TOKENS: tokens.append(Token(SINGLE_BYTE_TOKENS[b], chr(b), line, col))
            elif b == ord("+"): tokens.append(Token("op", "+", line, col, "+"))
            elif b == ord("*"): tokens.append(Token("op", "*", line, col, "*"))
            elif b == ord("-"): state, start_col = "MINUS", col
            elif b == ord(":"): state, start_col = "COLON", col
            elif b == ord("="): state, start_col = "EQ", col
            elif b == ord("!"): state, start_col = "BANG", col
            elif b == ord('"'): state, start, start_col = "STRING", i, col
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
        elif state == "STRING":                     # no escapes: any byte but '"' and newline
            if b is None or b == 10:
                raise CompileError(f"line {line}:{start_col}: string literal is not closed on its line")
            if b == ord('"'):
                text = data[start:i + 1].decode("latin-1")    # one char per byte, quotes included
                tokens.append(Token("string", text, line, start_col))
                state = "START"
        elif state == "MINUS":
            if b == ord(">"):
                tokens.append(Token("arrow", "->", line, start_col))
                state = "START"
            else:                                   # '-' alone: emit it, read this byte again
                tokens.append(Token("op", "-", line, start_col, "-"))
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
    def __init__(self, structs, fns, stmts, exit_node):
        self.structs, self.fns, self.stmts, self.exit_node = structs, fns, stmts, exit_node
    def dump(self, indent=0):
        print(" " * indent + "Program")
        for node in self.structs + self.fns + self.stmts:
            if not (isinstance(node, StructNode) and node.lambda_name):   # printed under its Lambda
                node.dump(indent + 2)
        self.exit_node.dump(indent + 2)

class StructNode(Node):
    kind = "struct"
    def __init__(self, line, col, name, fields, methods):
        self.line, self.col, self.name, self.fields, self.methods = line, col, name, fields, methods
        self.index = {}               # field name -> position, set by SemanticChecker
        self.method_table = {}        # member function name -> MemberFnNode, set by SemanticChecker
        self.lambda_name = None       # the struct of a lambda: the name of that lambda
    def dump(self, indent=0):
        print(" " * indent + f"Struct {self.name}")
        for f in self.fields + self.methods:
            f.dump(indent + 2)

class FieldDeclNode(Node):
    kind = "field_decl"
    def __init__(self, line, col, type_name, type_pos, name, mutable):
        self.line, self.col, self.type_name, self.type_pos = line, col, type_name, type_pos
        self.name, self.mutable = name, mutable
    def dump(self, indent=0):
        print(" " * indent + f"Member {self.name} {self.type_name} {'mut' if self.mutable else 'const'}")

class FnNode(Node):
    kind = "fn"
    def __init__(self, line, col, name, params, ret_type, ret_pos, body):
        self.line, self.col, self.name = line, col, name
        self.params, self.ret_type, self.ret_pos, self.body = params, ret_type, ret_pos, body
        self.ir_fn = None             # set by CodeGen
        self.shown = name             # the name in messages: a lambda's call is shown as the lambda
    label = "Fn"
    def dump(self, indent=0):
        print(" " * indent + f"{self.label} {self.name} -> {self.ret_type}")
        for p in self.params:
            print(" " * (indent + 2) + f"Param {p.name} {p.type_name}")
        self.body.dump(indent + 2)

class MemberFnNode(FnNode):
    """A function declared inside a struct; in the IR it is <Struct>_<name> with `this` first."""
    kind = "member_fn"
    label = "MemberFn"
    def __init__(self, *args):
        super().__init__(*args)
        self.owner = None             # the StructNode, set by SemanticChecker
        self.assigns_field = False    # assigns a field of its own object
        self.self_calls = []          # member functions it calls on its own object or its fields
        self.mutating = False         # decided after all bodies are checked

class LambdaNode(Node):
    """fn name := [captures](params) -> type. The semantic pass fills struct and decl;
    from then on only decl generates code at this place."""
    kind = "lambda"
    def __init__(self, line, col, name, captures, params, ret_type, ret_pos, body):
        self.line, self.col, self.name, self.captures = line, col, name, captures   # captures: ident tokens
        self.params, self.ret_type, self.ret_pos, self.body = params, ret_type, ret_pos, body
        self.struct = None            # the StructNode lambda.<name>, set by SemanticChecker
        self.decl = None              # the DeclNode of the closure variable, set by SemanticChecker
    def dump(self, indent=0):
        print(" " * indent + f"Lambda {self.name} -> {self.ret_type}")
        if self.struct is None:                         # --ast: the parse tree
            for c in self.captures:
                print(" " * (indent + 2) + f"Capture {c.text}")
            for p in self.params:
                print(" " * (indent + 2) + f"Param {p.name} {p.type_name}")
            self.body.dump(indent + 2)
            return
        self.struct.dump(indent + 2)
        d = self.decl
        print(" " * (indent + 2) + f"Decl {d.name} {d.type_name} {'mut' if d.mutable else 'const'}")
        for item in d.init.items:
            item.dump(indent + 4)

class DeclNode(Node):
    kind = "decl"
    def __init__(self, line, col, type_name, type_pos, name, mutable, init):
        self.line, self.col = line, col
        self.type_name, self.type_pos = type_name, type_pos
        self.name, self.mutable, self.init = name, mutable, init   # init: expression or InitListNode
        self.slot = None              # set by CodeGen: the alloca of this variable
    def dump(self, indent=0):
        print(" " * indent + f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}")
        if self.init is not None:
            self.init.dump(indent + 2)

class InitListNode(Node):
    kind = "init_list"
    def __init__(self, line, col, items):
        self.line, self.col, self.items = line, col, items
        self.type = None
        self.fields = None            # one FieldDeclNode per item, set by SemanticChecker
    def dump(self, indent=0):
        print(" " * indent + "Init")
        for item in self.items:
            item.dump(indent + 2)

class AssignNode(Node):
    kind = "assign"
    def __init__(self, line, col, target, value):
        self.line, self.col, self.target, self.value = line, col, target, value
    def dump(self, indent=0):
        print(" " * indent + f"Assign {self.target.path()}")
        self.value.dump(indent + 2)

class ExitNode(Node):
    kind = "exit"
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value
        self.fn = None                # the FnNode this exit returns from; None at the top level
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
        self.member_index = None      # inside a member function: a field of `this`
    def path(self):
        return self.name
    def dump(self, indent=0):
        print(" " * indent + f"Var {self.name}")

class FieldNode(Node):
    kind = "field"
    def __init__(self, line, col, obj, name, dot=None):
        self.line, self.col, self.obj, self.name = line, col, obj, name   # line:col of the field name
        self.dot = dot or (line, col) # line:col of the '.' before the name
        self.type = None
        self.index = None             # position of the field in its struct, set by SemanticChecker
        self.field = None             # the FieldDeclNode
    def path(self):
        return f"{self.obj.path()}.{self.name}"
    def dump(self, indent=0):
        print(" " * indent + f"Field {self.name}")
        self.obj.dump(indent + 2)

class CallNode(Node):
    kind = "call"
    def __init__(self, line, col, name, args):
        self.line, self.col, self.name, self.args = line, col, name, args
        self.type = None
        self.fn = None                # the FnNode, set by SemanticChecker
        self.on_this = False          # f(...) inside a member function that names a sibling
    def path(self):
        return f"{self.name}(...)"
    def dump(self, indent=0):
        print(" " * indent + f"Call {self.name}")
        for a in self.args:
            a.dump(indent + 2)

class MemberCallNode(Node):
    kind = "member_call"
    def __init__(self, line, col, obj, name, args, dot):
        self.line, self.col, self.obj, self.name, self.args = line, col, obj, name, args
        self.dot = dot                # line:col of the '.' before the name
        self.type = None
        self.fn = None                # the MemberFnNode, set by SemanticChecker
    def path(self):
        return f"{self.obj.path()}.{self.name}(...)"
    def dump(self, indent=0):
        print(" " * indent + f"MemberCall {self.name}")
        self.obj.dump(indent + 2)
        for a in self.args:
            a.dump(indent + 2)

class CallableCallNode(MemberCallNode):
    """name(args) after the semantic pass: name.call(args), checked and generated as a member call."""
    def dump(self, indent=0):
        print(" " * indent + f"CallableCall {self.obj.path()}")
        for a in self.args:
            a.dump(indent + 2)

class ReadNode(Node):
    kind = "read"
    def __init__(self, line, col):
        self.line, self.col = line, col
        self.type = "i32"
    def path(self):
        return "read()"
    def dump(self, indent=0):
        print(" " * indent + "Read")

class PrintNode(Node):
    kind = "print"
    def __init__(self, line, col, arg):
        self.line, self.col, self.arg = line, col, arg   # arg: expression or StringNode
    def dump(self, indent=0):
        print(" " * indent + "Print")
        self.arg.dump(indent + 2)

class StringNode(Node):
    kind = "string"
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value   # value: the bytes between the quotes
        self.type = "string"
    def dump(self, indent=0):
        print(" " * indent + f"String \"{self.value.decode('latin-1')}\"")

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
    def peek(self, ahead=0):
        i = self.pos + ahead
        return self.toks[i] if i < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        self.last_line = tok.line
        self.last_col = tok.col + len(tok.text)
        return tok

    def expect(self, kind, what):
        tok = self.peek()
        if tok is None:
            self.raise_err(f"expected {what}, found end of line")
        if tok.kind != kind:
            self.raise_err(f"expected {what}, got '{tok.text}'")
        return self.eat()

    def raise_err(self, msg):
        tok = self.peek()
        if tok is not None:
            raise CompileError(f"line {tok.line}:{tok.col}: {msg}")
        raise CompileError(f"line {self.last_line}:{self.last_col}: {msg}")

    @staticmethod
    def name_ok(tok, what):
        if tok.text in RESERVED:
            raise CompileError(f"line {tok.line}:{tok.col}: '{tok.text}' is a built-in and cannot be "
                               f"the name of a {what}")
        return tok

    def end_of_line(self):
        if self.peek() is not None:
            self.raise_err(f"unexpected '{self.peek().text}' after the statement")

    # program ::= { struct } { fn } { statement } exit
    def parse_program(self):
        structs, fns, stmts = [], [], []
        while True:
            toks = self.peek_line()
            if toks is None:
                raise CompileError("program without exit")
            first = toks[0].text
            if first == "struct":
                if fns or stmts:
                    self.next_line()
                    self.raise_err("a struct must be declared before all functions and statements")
                structs.append(self.parse_struct())
            elif self.is_lambda(toks):
                stmts.append(self.parse_statement_line())
            elif first == "fn":
                if stmts:
                    self.next_line()
                    self.raise_err("a function must be declared before all statements")
                fns.append(self.parse_fn())
            elif first == "exit":
                self.next_line()
                exit_node = self.parse_exit()
                break
            else:
                stmts.append(self.parse_statement_line())
        if self.peek_line() is not None:
            self.next_line()
            self.raise_err("unexpected statement after exit")
        return ProgramNode(structs, fns, stmts, exit_node)

    # struct ::= "struct" ident NL "{" NL field_decl { field_decl } { fn } "}" NL
    def parse_struct(self):
        self.next_line()
        kw = self.eat()
        name = self.name_ok(self.expect("ident", "a struct name"), "struct")
        self.end_of_line()
        lbrace = self.open_brace(kw)
        fields, methods = [], []
        while True:
            toks = self.peek_line()
            if toks is None:
                raise CompileError(f"line {lbrace.line}:{lbrace.col}: '{{' is never closed")
            if toks[0].text == "fn":
                if not fields:
                    self.next_line()
                    self.raise_err(f"a member function of '{name.text}' must come after its fields")
                methods.append(self.parse_fn(MemberFnNode))
                continue
            self.next_line()
            if toks[0].kind == "rbrace":
                self.eat()
                self.end_of_line()
                break
            if methods:
                self.raise_err(f"a field of '{name.text}' cannot follow a member function")
            type_tok = self.parse_type()
            mutable = self.peek() is not None and self.peek().text == "mut"
            if mutable:
                self.eat()
            fname = self.name_ok(self.expect("ident", "a field name"), "field")
            self.end_of_line()
            fields.append(FieldDeclNode(fname.line, fname.col, type_tok.text,
                                        (type_tok.line, type_tok.col), fname.text, mutable))
        if not fields:
            raise CompileError(f"line {lbrace.line}:{lbrace.col}: struct '{name.text}' has no fields")
        return StructNode(name.line, name.col, name.text, fields, methods)

    @staticmethod
    def is_lambda(toks):
        """A line that starts with fn name := [ is a lambda."""
        return len(toks) > 3 and toks[0].text == "fn" and toks[2].text == ":=" and toks[3].kind == "lbracket"

    # fn ::= "fn" ident ":=" "(" [ type ident { "," type ident } ] ")" "->" type NL block
    def parse_fn(self, cls=FnNode):
        self.next_line()
        kw = self.eat()
        what = "member function" if cls is MemberFnNode else "function"
        name = self.name_ok(self.expect("ident", f"a {what} name"), what)
        tok = self.peek()
        if tok is None or tok.text != ":=":
            self.raise_err(f"expected ':=' after the function name" + (f", got '{tok.text}'" if tok else ""))
        self.eat()
        if self.peek() is not None and self.peek().kind == "lbracket":
            self.raise_err(f"a {what} cannot have a capture list")
        params, ret, body = self.parse_signature_and_body(kw, name)
        return cls(name.line, name.col, name.text, params, ret.text, (ret.line, ret.col), body)

    # lambda ::= "fn" ident ":=" "[" [ ident { "," ident } ] "]"
    #            "(" [ type ident { "," type ident } ] ")" "->" type NL block
    def parse_lambda(self):
        kw = self.eat()
        name = self.name_ok(self.expect("ident", "a lambda name"), "lambda")
        self.eat()                                      # ':='
        self.eat()                                      # '['
        captures = []
        if self.peek() is not None and self.peek().kind != "rbracket":
            while True:
                captures.append(self.expect("ident", "a captured variable"))
                if self.peek() is not None and self.peek().kind == "comma":
                    self.eat()
                    continue
                break
        self.expect("rbracket", "']' to close the capture list")
        params, ret, body = self.parse_signature_and_body(kw, name)
        return LambdaNode(name.line, name.col, name.text, captures, params, ret.text, (ret.line, ret.col), body)

    def parse_signature_and_body(self, kw, name):
        self.expect("lparen", "'('")
        params = []
        if self.peek() is not None and self.peek().kind != "rparen":
            while True:
                type_tok = self.parse_type()
                pname = self.name_ok(self.expect("ident", "a parameter name"), "parameter")
                params.append(DeclNode(pname.line, pname.col, type_tok.text, (type_tok.line, type_tok.col),
                                       pname.text, False, None))
                if self.peek() is not None and self.peek().kind == "comma":
                    self.eat()
                    continue
                break
        self.expect("rparen", "')'")
        self.expect("arrow", "'->'")
        ret = self.parse_type()
        self.end_of_line()
        body = self.parse_block(kw)
        if body.exit_node is None:
            raise CompileError(f"line {name.line}:{name.col}: the body of '{name.text}' must end with exit")
        return params, ret, body

    def parse_type(self):
        tok = self.peek()
        if tok is None or not (tok.subkind == "typename" or tok.kind == "ident"):
            self.raise_err("expected a type" + (f", got '{tok.text}'" if tok else ", found end of line"))
        return self.eat()

    # exit ::= "exit" factor
    def parse_exit(self):
        tok = self.eat()
        val = self.parse_factor()
        if self.peek() is not None:
            self.raise_err(f"unexpected '{self.peek().text}' after exit")
        return ExitNode(tok.line, tok.col, val)

    # statement ::= decl | assign | if | while | print | lambda
    # (consumes one line, or several for if/while/lambda)
    def parse_statement_line(self):
        self.next_line()
        tok = self.peek()
        if tok.text == "if":
            return self.parse_if()
        if tok.text == "while":
            return self.parse_while()
        if tok.text == "else":
            self.raise_err("'else' without an 'if'")
        if self.is_lambda(self.toks):
            return self.parse_lambda()
        if tok.text in ("struct", "fn"):
            self.raise_err(f"'{tok.text}' is allowed only at the top level, before the statements")
        if tok.kind in ("lbrace", "rbrace"):
            self.raise_err(f"unexpected '{tok.text}'")
        if tok.kind == "string":
            self.raise_err("a string literal can only be the argument of print")
        node = self.parse_statement()
        self.end_of_line()
        return node

    def parse_statement(self):
        tok = self.peek()
        nxt = self.peek(1)
        if tok.subkind == "typename":
            return self.parse_decl()
        if tok.text == "print":
            return self.parse_print()
        if tok.kind == "ident" and nxt is not None and (nxt.kind == "ident" or nxt.text == "mut"):
            return self.parse_decl()                    # a struct-typed declaration: Point p{...}
        if tok.kind == "ident":
            return self.parse_assign()
        self.raise_err(f"cannot start a statement with '{tok.text}'")

    # decl ::= type [ "mut" ] ident "{" init { "," init } "}"
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
        name_tok = self.name_ok(self.eat(), "variable")

        tok = self.peek()
        if tok is None or tok.kind != "lbrace":
            raise CompileError(f"line {name_tok.line}:{name_tok.col}: variable '{name_tok.text}' needs an initialiser in {{}}")
        init = self.parse_init_list()
        if len(init.items) == 1 and not isinstance(init.items[0], InitListNode):
            init = init.items[0]                        # a single value: the plain expression
        return DeclNode(name_tok.line, name_tok.col, type_tok.text, (type_tok.line, type_tok.col),
                        name_tok.text, mutable, init)

    # init_list ::= "{" init { "," init } "}"        init ::= expr | init_list
    def parse_init_list(self):
        lbrace = self.eat()
        items = []
        while True:
            tok = self.peek()
            if tok is not None and tok.kind == "lbrace":
                items.append(self.parse_init_list())
            else:
                items.append(self.parse_expr())
            tok = self.peek()
            if tok is not None and tok.kind == "comma":
                self.eat()
                continue
            break
        tok = self.peek()
        if tok is None:
            raise CompileError(f"line {lbrace.line}:{lbrace.col}: '{{' is not closed before the end of the line")
        if tok.kind != "rbrace":
            self.raise_err(f"expected '}}', got '{tok.text}'")
        self.eat()
        return InitListNode(lbrace.line, lbrace.col, items)

    # assign ::= ident { "." ident } ":=" expr
    def parse_assign(self):
        name_tok = self.peek()
        target = self.parse_name()
        tok = self.peek()
        through_call = any(isinstance(n, (CallNode, MemberCallNode, ReadNode)) for n in self.links(target))
        if through_call:
            if tok is not None and tok.text == ":=":
                raise CompileError(f"line {tok.line}:{tok.col}: cannot assign to '{target.path()}': "
                                   f"a call result is a temporary")
            if isinstance(target, (CallNode, MemberCallNode, ReadNode)):
                raise CompileError(f"line {name_tok.line}:{name_tok.col}: a call alone is not a statement")
        if tok is None or tok.text != ":=":
            if tok is not None:
                raise CompileError(f"line {tok.line}:{tok.col}: expected ':=' after '{target.path()}', got '{tok.text}'")
            self.raise_err(f"expected ':=' after '{target.path()}', found end of line")
        self.eat()
        expr = self.parse_expr()
        root = self.root(target)
        return AssignNode(root.line, root.col, target, expr)

    @staticmethod
    def links(node):
        """The chain from its last link back to its root: c.center.d2() -> d2(), center, c."""
        out = [node]
        while isinstance(node, (FieldNode, MemberCallNode)):
            node = node.obj
            out.append(node)
        return out

    @staticmethod
    def root(node):
        return Parser.links(node)[-1]

    # the start of a chain: a variable, a call f(...) or read()
    def parse_name(self):
        tok = self.eat()
        if self.peek() is not None and self.peek().kind == "lparen":
            return self.parse_chain(self.parse_call(tok))
        if tok.text in RESERVED:
            raise CompileError(f"line {tok.line}:{tok.col}: '{tok.text}' is a built-in, not a variable")
        return self.parse_chain(VarNode(tok.line, tok.col, tok.text))

    # field       ::= ident "." ident { "." ident }
    # member_call ::= ident { "." ident } "." ident "(" [ expr { "," expr } ] ")"
    # A '.' may also follow a call: p.moved(1).x, origin().moved(7) (the additional task).
    def parse_chain(self, node):
        while self.peek() is not None and self.peek().kind == "dot":
            dot = self.eat()
            f = self.expect("ident", "a field or member function name after '.'")
            if self.peek() is not None and self.peek().kind == "lparen":
                self.name_ok(f, "member function")
                args = self.parse_args()
                node = MemberCallNode(f.line, f.col, node, f.text, args, (dot.line, dot.col))
            else:
                node = FieldNode(f.line, f.col, node, f.text, (dot.line, dot.col))
        return node

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

    # factor ::= number | "true" | "false" | ident | field | call | member_call | "!" factor
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
        elif tok.kind == "string":
            self.raise_err("a string literal can only be the argument of print")
        elif tok.text == "print":
            self.raise_err("print is a statement and has no value")
        elif tok.kind == "ident":
            return self.parse_name()
        else:
            self.raise_err(f"expected a constant or a variable, got '{tok.text}'")

    # call ::= ident "(" [ expr { "," expr } ] ")"        read ::= "read" "(" ")"
    def parse_call(self, name_tok):
        if name_tok.text == "read":
            self.eat()
            if self.peek() is not None and self.peek().kind != "rparen":
                self.raise_err("read() takes no arguments")
            self.expect("rparen", "')' to close the call")
            return ReadNode(name_tok.line, name_tok.col)
        return CallNode(name_tok.line, name_tok.col, name_tok.text, self.parse_args())

    def parse_args(self):
        self.eat()                                      # '('
        args = []
        if self.peek() is not None and self.peek().kind != "rparen":
            while True:
                args.append(self.parse_expr())
                if self.peek() is not None and self.peek().kind == "comma":
                    self.eat()
                    continue
                break
        self.expect("rparen", "')' to close the call")
        return args

    # print ::= "print" "(" ( string | expr ) ")"
    def parse_print(self):
        kw = self.eat()
        self.expect("lparen", "'(' after print")
        tok = self.peek()
        if tok is None or tok.kind == "rparen":
            raise CompileError(f"line {kw.line}:{kw.col}: print takes one argument, got none")
        if tok.kind == "string":
            self.eat()
            arg = StringNode(tok.line, tok.col, tok.text[1:-1].encode("latin-1"))
        else:
            arg = self.parse_expr()
        tok = self.peek()
        if tok is not None and tok.kind == "comma":
            self.raise_err("print takes one argument, got more")
        self.expect("rparen", "')' to close print")
        return PrintNode(kw.line, kw.col, arg)

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

    def open_brace(self, owner):
        toks = self.peek_line()
        if toks is None:
            raise CompileError(f"line {self.last_line}:{self.last_col}: expected '{{' on its own line "
                               f"after '{owner.text}', found end of file")
        self.next_line()
        if toks[0].kind != "lbrace":
            self.raise_err(f"expected '{{' on its own line after '{owner.text}', got '{toks[0].text}'")
        lbrace = self.eat()
        self.end_of_line()
        return lbrace

    # block ::= "{" NL { statement } [ exit NL ] "}" NL     (not empty)
    def parse_block(self, owner):
        lbrace = self.open_brace(owner)
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

# ---------------------------------------------------------------------------
# Semantic pass
# ---------------------------------------------------------------------------
class SemanticChecker:
    """Walks the tree before any IR exists. Sets node.type on every expression,
    node.decl on every Var, node.fn on every Call / Exit, node.index on every Field;
    raises CompileError with line:col."""

    def __init__(self):
        self.structs = {}             # name -> StructNode
        self.fns = {}                 # name -> FnNode
        self.scopes = [{}]            # stack of frames: name -> DeclNode
        self.current_fn = None
        self.current_struct = None    # the StructNode whose member function is being checked
        self.member_calls = []        # every MemberCallNode, for the mutation rule at the end
        self.enclosing = []           # around a lambda body: (scopes, struct) of each outer level
        self.program = None

    def visit_program(self, node):
        self.program = node
        for s in node.structs:
            s.accept(self)
        methods = [m for s in node.structs for m in s.methods]
        for f in node.fns:                              # all signatures first: calls may go forward
            if f.name in self.fns:
                raise CompileError(f"line {f.line}:{f.col}: function '{f.name}' is already declared")
            self.fns[f.name] = f
        for f in methods + node.fns:
            for p in f.params:
                self.check_type(p.type_name, p.type_pos)
            self.check_type(f.ret_type, f.ret_pos)
        for m in methods:
            self.current_struct = m.owner
            m.accept(self)
        self.current_struct = None
        for f in node.fns:
            f.accept(self)
        self.scopes = [{}]
        for stmt in node.stmts:
            stmt.accept(self)
        node.exit_node.accept(self)
        self.check_mutation([m for s in node.structs for m in s.methods])   # lambdas included

    def check_mutation(self, methods):
        """A member function is mutating if it assigns a field of its object or calls a mutating
        member function on its object or a field of it. Calls may form cycles: repeat until stable."""
        for m in methods:
            m.mutating = m.assigns_field
        changed = True
        while changed:
            changed = False
            for m in methods:
                if not m.mutating and any(callee.mutating for callee in m.self_calls):
                    m.mutating = changed = True
        for call in self.member_calls:                  # a mutating call needs every link mut
            if not call.fn.mutating:
                continue
            for link in Parser.links(call.obj):
                if isinstance(call, CallableCallNode) and isinstance(link, (FieldNode, VarNode)):
                    if not (link.field if isinstance(link, FieldNode) else link.decl).mutable:
                        what = "it" if link is call.obj else f"'{link.name}'"
                        raise CompileError(f"line {link.line}:{link.col}: cannot call '{call.obj.path()}': "
                                           f"its 'call' changes it, and {what} is not mut")
                elif isinstance(link, FieldNode):
                    if not link.field.mutable:
                        raise CompileError(f"line {link.line}:{link.col}: cannot call '{call.name}' on "
                                           f"'{call.obj.path()}': it changes the object, and field "
                                           f"'{link.name}' is not mut")
                elif isinstance(link, VarNode):
                    if not link.decl.mutable:
                        raise CompileError(f"line {link.line}:{link.col}: cannot call '{call.name}' on "
                                           f"'{call.obj.path()}': it changes the object, and "
                                           f"'{link.name}' is not mut")
                else:
                    break                               # a call result: a temporary copy, anything goes

    def visit_struct(self, node):
        if node.name in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: struct '{node.name}' is already declared")
        for i, f in enumerate(node.fields):
            if f.name in node.index:
                raise CompileError(f"line {f.line}:{f.col}: field '{f.name}' is already declared in '{node.name}'")
            if f.type_name == node.name:
                raise CompileError(f"line {f.type_pos[0]}:{f.type_pos[1]}: struct '{node.name}' cannot contain itself")
            self.check_type(f.type_name, f.type_pos)
            node.index[f.name] = i
        for m in node.methods:
            if m.name in node.index:
                raise CompileError(f"line {m.line}:{m.col}: '{m.name}' is already a field of '{node.name}'")
            if m.name in node.method_table:
                raise CompileError(f"line {m.line}:{m.col}: member function '{m.name}' is already "
                                   f"declared in '{node.name}'")
            m.owner = node
            node.method_table[m.name] = m
        self.structs[node.name] = node

    def visit_fn(self, node):
        self.current_fn = node
        frame = {}
        for p in node.params:
            if p.name in frame:
                raise CompileError(f"line {p.line}:{p.col}: parameter '{p.name}' is already declared")
            frame[p.name] = p
        self.scopes = [frame]                           # the body does not see top-level variables
        node.body.accept(self)
        self.current_fn = None

    visit_member_fn = visit_fn

    def check_type(self, name, pos):
        if name not in BUILTIN_TYPES and name not in self.structs:
            raise CompileError(f"line {pos[0]}:{pos[1]}: unknown type '{name}'")

    def visit_lambda(self, node):
        """The rewrite: struct lambda.<name> with one const field per capture, its member function
        call, and a variable of that struct initialised here with the captured values."""
        fields = []
        for c in node.captures:
            if any(f.name == c.text for f in fields):
                raise CompileError(f"line {c.line}:{c.col}: '{c.text}' is captured twice")
            var = self.find_var(c.text)
            if var is None:
                raise CompileError(f"line {c.line}:{c.col}: cannot capture '{c.text}': "
                                   f"no variable '{c.text}' is visible here")
            fields.append(FieldDeclNode(c.line, c.col, var.type_name, (c.line, c.col), c.text, False))
        for p in node.params:
            if any(f.name == p.name for f in fields):
                raise CompileError(f"line {p.line}:{p.col}: parameter '{p.name}' has the name of a capture "
                                   f"of lambda '{node.name}'")
        for p in node.params:
            self.check_type(p.type_name, p.type_pos)
        self.check_type(node.ret_type, node.ret_pos)

        call = MemberFnNode(node.line, node.col, "call", node.params, node.ret_type, node.ret_pos, node.body)
        call.shown = node.name
        struct = StructNode(node.line, node.col, self.lambda_struct_name(node.name), fields, [call])
        struct.lambda_name = node.name
        struct.index = {f.name: i for i, f in enumerate(fields)}
        struct.method_table = {"call": call}
        call.owner = struct
        self.structs[struct.name] = struct
        self.program.structs.append(struct)             # in the field order codegen needs: captures first
        node.struct = struct

        saved = self.scopes, self.current_fn, self.current_struct
        self.enclosing.append((self.scopes, self.current_struct))
        self.current_struct = struct
        call.accept(self)                               # same rule as any function body
        self.enclosing.pop()
        self.scopes, self.current_fn, self.current_struct = saved

        items = [VarNode(c.line, c.col, c.text) for c in node.captures]
        init = InitListNode(node.line, node.col, items)
        node.decl = DeclNode(node.line, node.col, struct.name, (node.line, node.col), node.name, False, init)
        node.decl.accept(self)                          # after the body: a lambda cannot call itself

    def lambda_struct_name(self, name):
        """lambda.<name>; lambdas in different blocks may share a name, their structs may not."""
        unique, n = f"lambda.{name}", 1
        while unique in self.structs:
            unique, n = f"lambda.{name}.{n}", n + 1
        return unique

    def is_lambda(self, t):
        return t in self.structs and self.structs[t].lambda_name is not None

    def reject_lambda(self, expr, use):
        if self.is_lambda(expr.type):
            raise CompileError(f"line {expr.line}:{expr.col}: '{expr.path()}' is a lambda and {use}")

    def visit_decl(self, node):
        self.check_type(node.type_name, node.type_pos)
        if node.name in self.scopes[-1]:              # only the innermost frame: shadowing is allowed
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is already declared in this block")
        init = node.init
        if not isinstance(init, InitListNode):
            init.accept(self)                           # before the name exists
            if node.type_name in self.structs and init.type != node.type_name:
                node.init = init = InitListNode(init.line, init.col, [init])   # one-field struct
        if isinstance(init, InitListNode):
            if node.type_name not in self.structs:
                raise CompileError(f"line {init.line}:{init.col}: '{node.name}' of type {node.type_name} "
                                   f"takes one value, got {len(init.items)}")
            self.check_init_list(init, node.type_name)
        else:
            self.check_assignable(init, node.type_name, node, f"initialise '{node.name}'")
        self.scopes[-1][node.name] = node

    def check_init_list(self, node, struct_name):
        struct = self.structs[struct_name]
        if len(node.items) != len(struct.fields):
            raise CompileError(f"line {node.line}:{node.col}: '{struct_name}' has {len(struct.fields)} "
                               f"fields, got {len(node.items)} values")
        node.type, node.fields = struct_name, struct.fields
        for item, field in zip(node.items, struct.fields):
            if isinstance(item, InitListNode):
                if field.type_name not in self.structs:
                    raise CompileError(f"line {item.line}:{item.col}: field '{field.name}' of type "
                                       f"{field.type_name} cannot be initialised with {{}}")
                self.check_init_list(item, field.type_name)
            else:
                item.accept(self)
                self.check_assignable(item, field.type_name, item, f"initialise field '{field.name}'")

    def visit_init_list(self, node):
        return node.type

    def visit_assign(self, node):
        target = node.target
        target.accept(self)
        self.reject_lambda(target, "cannot be assigned: its type has no name")
        root = Parser.root(target)
        if root.member_index is not None:              # a field of `this`: the function mutates
            self.current_fn.assigns_field = True
        if root.member_index is not None and not root.decl.mutable and self.current_struct.lambda_name:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{target.path()}': "
                               f"the capture '{root.name}' is const, write [mut {root.name}]")
        if isinstance(target, VarNode):
            if not target.decl.mutable:
                what = f"field '{target.name}'" if target.member_index is not None else "it"
                raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{target.name}': {what} is not mut")
        else:
            if not root.decl.mutable:
                raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{target.path()}': "
                                   f"'{root.name}' is not mut")
            link = target
            links = []
            while isinstance(link, FieldNode):
                links.append(link)
                link = link.obj
            for f in reversed(links):                   # every field on the way must be mut
                if not f.field.mutable:
                    raise CompileError(f"line {f.line}:{f.col}: cannot assign to '{target.path()}': "
                                       f"field '{f.name}' is not mut")
        if target.type in self.structs:
            bad = self.const_field(target.type)
            if bad:
                raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{target.path()}': "
                                   f"field '{bad}' is not mut")
        node.value.accept(self)
        self.reject_lambda(node.value, f"cannot be assigned to '{target.path()}': its type has no name")
        self.check_assignable(node.value, target.type, node, f"assign to '{target.path()}'")

    def const_field(self, struct_name):
        """The first field (at any depth) without mut, or None if the whole object may be replaced."""
        for f in self.structs[struct_name].fields:
            if not f.mutable:
                return f.name
            if f.type_name in self.structs:
                inner = self.const_field(f.type_name)
                if inner:
                    return f"{f.name}.{inner}"
        return None

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

    def visit_exit(self, node):
        t = node.value.accept(self)
        self.reject_lambda(node.value, "cannot be the value of exit: its type has no name")
        node.fn = self.current_fn
        if self.current_fn is None:
            if t not in BUILTIN_TYPES:
                raise CompileError(f"line {node.line}:{node.col}: exit of the program takes i32, i64 or bool, got {t}")
        else:
            want = self.current_fn.ret_type
            if not (t == want or (t == "i32" and want == "i64")):
                raise CompileError(f"line {node.line}:{node.col}: function '{self.current_fn.shown}' must exit "
                                   f"with {want}, got {t}")

    def visit_not(self, node):
        t = node.operand.accept(self)
        if t != "bool":
            raise CompileError(f"line {node.line}:{node.col}: cannot apply '!' to {t}")
        node.type = "bool"
        return node.type

    def visit_binop(self, node):
        lt, rt = node.left.accept(self), node.right.accept(self)
        if node.op in ("+", "-", "*"):
            for t in (lt, rt):
                if t not in INT_TYPES:
                    raise CompileError(f"line {node.line}:{node.col}: cannot apply '{node.op}' to {t}")
            node.type = node.operand_type = wider(lt, rt)
        else:
            if lt in INT_TYPES and rt in INT_TYPES:
                node.operand_type = wider(lt, rt)
            elif lt == rt == "bool":
                node.operand_type = "bool"
            else:
                raise CompileError(f"line {node.line}:{node.col}: cannot compare {lt} with {rt}")
            node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node, node.name)
        if isinstance(node.decl, FieldDeclNode):       # a field of the object of a member function
            node.member_index = self.current_struct.index[node.name]
        node.type = node.decl.type_name
        return node.type

    def visit_field(self, node):
        return self.resolve_field(node, node.obj.accept(self))

    def resolve_field(self, node, t):
        if t not in self.structs:
            line, col = node.dot if isinstance(node.obj, (CallNode, MemberCallNode, ReadNode)) else (node.line, node.col)
            raise CompileError(f"line {line}:{col}: '{node.obj.path()}' of type {t} has no fields")
        struct = self.structs[t]
        if struct.lambda_name:
            raise CompileError(f"line {node.line}:{node.col}: '{node.obj.path()}' is a lambda: "
                               f"its captures are not visible outside its body")
        if node.name not in struct.index:
            raise CompileError(f"line {node.line}:{node.col}: struct '{t}' has no field '{node.name}'")
        node.index = struct.index[node.name]
        node.field = struct.fields[node.index]
        node.type = node.field.type_name
        return node.type

    def visit_call(self, node):
        struct = self.current_struct
        var = self.find_var(node.name)
        sibling = struct is not None and not struct.lambda_name and node.name in struct.method_table
        if var is not None:                             # the call rule: a visible variable comes first
            if sibling or node.name in self.fns:
                raise CompileError(f"line {node.line}:{node.col}: '{node.name}' is both a variable and "
                                   f"a function: rename one of them")
            obj = VarNode(node.line, node.col, node.name)
            self.make_callable(node, obj, var.type_name)
            return self.visit_member_call(node)
        if sibling:                                     # a sibling hides a global
            node.fn, node.on_this = struct.method_table[node.name], True
            self.current_fn.self_calls.append(node.fn)
        elif node.name in self.fns:
            node.fn = self.fns[node.name]
        else:
            if struct is not None and struct.lambda_name:
                if node.name == struct.lambda_name:
                    raise CompileError(f"line {node.line}:{node.col}: lambda '{node.name}' cannot call "
                                       f"itself: its name is declared after its body")
                self.check_captured(node, node.name)
            raise CompileError(f"line {node.line}:{node.col}: function '{node.name}' is not declared")
        self.check_args(node, node.fn)
        node.type = node.fn.ret_type
        return node.type

    def make_callable(self, node, obj, t):
        """The rewrite: this call node becomes the member call obj.call(args), in place in the tree."""
        if t not in self.structs:
            raise CompileError(f"line {obj.line}:{obj.col}: '{obj.path()}' is not callable: it is {t}")
        if "call" not in self.structs[t].method_table:
            raise CompileError(f"line {obj.line}:{obj.col}: '{obj.path()}' is not callable: "
                               f"{t} has no member function 'call'")
        node.__class__ = CallableCallNode
        node.obj, node.name, node.dot = obj, "call", (node.line, node.col)

    def check_args(self, node, fn):
        target = node.obj.path() if isinstance(node, CallableCallNode) else fn.name
        if len(node.args) != len(fn.params):
            what = f"'{target}'" if isinstance(node, CallableCallNode) else f"function '{target}'"
            raise CompileError(f"line {node.line}:{node.col}: {what} takes {len(fn.params)} "
                               f"arguments, got {len(node.args)}")
        for arg, p in zip(node.args, fn.params):
            arg.accept(self)
            self.reject_lambda(arg, f"cannot be passed to '{target}': its type has no name, "
                                    f"so no parameter can have it")
            self.check_assignable(arg, p.type_name, arg, f"pass '{p.name}' to '{target}'")

    def visit_member_call(self, node):
        t = node.obj.accept(self)
        if t not in self.structs:
            raise CompileError(f"line {node.dot[0]}:{node.dot[1]}: cannot call '{node.name}' on "
                               f"'{node.obj.path()}' of type {t}: it is not a struct")
        struct = self.structs[t]
        if node.name in struct.index:                   # obj.name(args) on a field: obj.name.call(args)
            field = FieldNode(node.line, node.col, node.obj, node.name, node.dot)
            t = self.resolve_field(field, t)
            self.make_callable(node, field, t)
            struct = self.structs[t]
        if node.name not in struct.method_table:
            raise CompileError(f"line {node.line}:{node.col}: struct '{t}' has no member function '{node.name}'")
        node.fn = struct.method_table[node.name]
        self.check_args(node, node.fn)
        root = Parser.root(node.obj)
        if isinstance(root, VarNode) and root.member_index is not None:   # on a field of `this`
            self.current_fn.self_calls.append(node.fn)
        self.member_calls.append(node)
        node.type = node.fn.ret_type
        return node.type

    def visit_read(self, node):
        return node.type

    def visit_print(self, node):
        if not isinstance(node.arg, StringNode):
            node.arg.accept(self)
            self.reject_lambda(node.arg, "cannot be printed: its type has no name")

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

    def find_var(self, name, scopes=None, struct=None):
        if scopes is None:
            scopes, struct = self.scopes, self.current_struct
        for frame in reversed(scopes):
            if name in frame:
                return frame[name]
        if struct is not None and name in struct.index:  # then the fields of the object
            return struct.fields[struct.index[name]]
        return None

    def check_captured(self, node, name):
        """Inside a lambda: a name of an outer level that the capture list does not take."""
        if any(self.find_var(name, scopes, struct) is not None for scopes, struct in self.enclosing):
            raise CompileError(f"line {node.line}:{node.col}: '{name}' is not captured by lambda "
                               f"'{self.current_struct.lambda_name}': add it to the capture list, [{name}]")

    def lookup(self, node, name):
        decl = self.find_var(name)
        if decl is not None:
            return decl
        struct = self.current_struct
        if struct is not None and struct.lambda_name:
            self.check_captured(node, name)
            if name not in self.structs:
                raise CompileError(f"line {node.line}:{node.col}: '{name}' is not a parameter, a local "
                                   f"variable or a capture of lambda '{struct.lambda_name}'")
        if name in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: '{name}' is a type, not an object")
        if struct is not None:
            raise CompileError(f"line {node.line}:{node.col}: '{name}' is not a parameter, a local "
                               f"variable or a field of '{struct.name}'")
        raise CompileError(f"line {node.line}:{node.col}: variable '{name}' is used before its declaration")

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        self.reject_lambda(expr, f"cannot {what}: its type has no name")
        if isinstance(expr, ConstNode) and want in INT_TYPES:
            raise CompileError(f"line {expr.line}:{expr.col}: constant {expr.value} does not fit in {want}")
        raise CompileError(f"line {at.line}:{at.col}: cannot {what} of type {want} "
                           f"with a value of type {have}")

def wider(a, b):
    return "i64" if "i64" in (a, b) else "i32"

# ---------------------------------------------------------------------------
# Code Generation (reads what the checker stored, checks nothing)
# ---------------------------------------------------------------------------
I1, I8, I32, I64 = ir.IntType(1), ir.IntType(8), ir.IntType(32), ir.IntType(64)
FN_PREFIX = "fn_"

class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="program")
        self.module.triple = llvm.get_default_triple()
        self.types = {"i32": I32, "i64": I64, "bool": I1}
        self.structs = {}             # name -> StructNode, for printing by the field table

        printf_ty = ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True)
        self.printf_fn = ir.Function(self.module, printf_ty, name="printf")

        self.fmt_int  = self.global_string(".fmt_int",  b"Program exit with result %lld\n\0")
        self.fmt_bool = self.global_string(".fmt_bool", b"Program exit with result %s\n\0")
        self.str_true  = self.global_string(".str_true",  b"true\0")
        self.str_false = self.global_string(".str_false", b"false\0")
        self.texts = {}               # bytes -> global constant, for print formats and literals
        self.scanf_fn = None          # declared on the first read()
        self.this = None              # inside a member function: the pointer to its object

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

    def text(self, data):
        """An i8* to a global constant holding data and a trailing zero; one global per text."""
        if data not in self.texts:
            self.texts[data] = self.global_string(f".str.{len(self.texts)}", data + b"\0")
        return self.cstr(self.texts[data])

    def printf(self, fmt, *args):
        self.builder.call(self.printf_fn, [self.text(fmt), *args])

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    def start_function(self, fn):
        self.fn = fn
        self.entry_block = fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(self.entry_block)

    def visit_program(self, node):
        for s in node.structs:
            s.accept(self)
        methods = [m for s in node.structs for m in s.methods]
        for f in methods + node.fns:                    # declare every function before any body
            params = [self.types[p.type_name] for p in f.params]
            if isinstance(f, MemberFnNode):             # Point_shift(Point* this, ...)
                params.insert(0, self.types[f.owner.name].as_pointer())
                name = f"{f.owner.name}_{f.name}"
            else:
                name = FN_PREFIX + f.name
            f.ir_fn = ir.Function(self.module, ir.FunctionType(self.types[f.ret_type], params), name=name)
        for f in methods + node.fns:
            f.accept(self)
        self.this = None
        self.start_function(ir.Function(self.module, ir.FunctionType(I32, []), name="main"))
        for stmt in node.stmts:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_struct(self, node):
        self.structs[node.name] = node
        ty = self.module.context.get_identified_type(node.name)
        ty.set_body(*[self.types[f.type_name] for f in node.fields])
        self.types[node.name] = ty

    def visit_fn(self, node):
        self.start_function(node.ir_fn)
        args = list(node.ir_fn.args)
        self.this = None
        if isinstance(node, MemberFnNode):              # the object is passed by address
            self.this = args.pop(0)
            self.this.name = "this"
        for p, arg in zip(node.params, args):           # parameters are copied into slots
            arg.name = p.name
            p.slot = self.alloca(self.types[p.type_name], p.name)
            self.builder.store(arg, p.slot)
        node.body.accept(self)

    visit_member_fn = visit_fn

    def visit_lambda(self, node):                       # its struct and call are ordinary by now
        node.decl.accept(self)

    def visit_decl(self, node):
        if isinstance(node.init, InitListNode):
            val = node.init.accept(self)
        else:
            val = self.coerce(node.init.accept(self), node.init.type, node.type_name)
        node.slot = self.alloca(self.types[node.type_name], node.name)
        self.builder.store(val, node.slot)

    def visit_init_list(self, node):
        """One value of the struct type, built field by field with insertvalue."""
        agg = ir.Constant(self.types[node.type], ir.Undefined)
        for i, (item, field) in enumerate(zip(node.items, node.fields)):
            val = item.accept(self)
            if not isinstance(item, InitListNode):
                val = self.coerce(val, item.type, field.type_name)
            agg = self.builder.insert_value(agg, val, i, name=node.type.lower())
        return agg

    def alloca(self, ty, name):
        """Every slot goes to the start of the entry block, wherever the builder is now."""
        here = self.builder.block
        self.builder.position_at_start(self.entry_block)
        slot = self.builder.alloca(ty, name=name)
        self.builder.position_at_end(here)
        return slot

    def field_ptr(self, ptr, index, name):
        zero = ir.Constant(I32, 0)
        return self.builder.gep(ptr, [zero, ir.Constant(I32, index)], inbounds=True, name=name + ".ptr")

    @staticmethod
    def addressable(node):
        """A chain of fields rooted at a variable lives in memory; one through a call result does not."""
        links = Parser.links(node)
        return isinstance(links[-1], VarNode) and all(isinstance(n, FieldNode) for n in links[:-1])

    def address(self, node):
        """Pointer to a variable, to a field of `this`, or to a field reached through a chain (gep)."""
        if isinstance(node, VarNode):
            if node.member_index is not None:
                return self.field_ptr(self.this, node.member_index, node.name)
            return node.decl.slot
        return self.field_ptr(self.address(node.obj), node.index, node.name)

    def pointer_to(self, node):
        """The address of an object: its own memory, or a temporary alloca for a call result."""
        if self.addressable(node):
            return self.address(node)
        tmp = self.alloca(self.types[node.type], "tmp")
        self.builder.store(node.accept(self), tmp)
        return tmp

    def visit_assign(self, node):
        val = self.coerce(node.value.accept(self), node.value.type, node.target.type)
        self.builder.store(val, self.address(node.target))

    def visit_block(self, node):
        for stmt in node.stmts:
            stmt.accept(self)
        if node.exit_node:
            node.exit_node.accept(self)

    def visit_if(self, node):
        cond = node.cond.accept(self)
        then_bb = self.fn.append_basic_block("then")
        else_bb = self.fn.append_basic_block("else") if node.else_block else None
        merge_bb = self.fn.append_basic_block("merge")
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
        cond_bb = self.fn.append_basic_block("cond")
        body_bb = self.fn.append_basic_block("body")
        end_bb = self.fn.append_basic_block("end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        self.builder.cbranch(node.cond.accept(self), body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)              # the back edge

        self.builder.position_at_end(end_bb)

    def visit_exit(self, node):
        val = node.value.accept(self)
        if node.fn is not None:                         # exit of a function: its result
            self.builder.ret(self.coerce(val, node.value.type, node.fn.ret_type))
            return
        if node.value.type == "bool":
            text = self.builder.select(val, self.cstr(self.str_true), self.cstr(self.str_false), name="text")
            self.builder.call(self.printf_fn, [self.cstr(self.fmt_bool), text])
        else:
            val = self.coerce(val, node.value.type, "i64")
            self.builder.call(self.printf_fn, [self.cstr(self.fmt_int), val])
        self.builder.ret(ir.Constant(I32, 0))

    def visit_not(self, node):
        return self.builder.not_(node.operand.accept(self), name="not")

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
        return self.builder.load(self.address(node), name=node.name)

    def visit_field(self, node):
        if self.addressable(node):
            return self.builder.load(self.address(node), name=node.name)
        return self.builder.extract_value(node.obj.accept(self), node.index, name=node.name)

    def call_args(self, node):
        return [self.coerce(a.accept(self), a.type, p.type_name) for a, p in zip(node.args, node.fn.params)]

    def visit_call(self, node):
        this = [self.this] if node.on_this else []      # a sibling member function: same object
        return self.builder.call(node.fn.ir_fn, this + self.call_args(node), name="call")

    def visit_member_call(self, node):
        this = self.pointer_to(node.obj)
        return self.builder.call(node.fn.ir_fn, [this] + self.call_args(node), name=node.name)

    def visit_read(self, node):
        if self.scanf_fn is None:
            scanf_ty = ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True)
            self.scanf_fn = ir.Function(self.module, scanf_ty, name="scanf")
        slot = self.alloca(I32, "in")
        self.builder.store(ir.Constant(I32, 0), slot)   # 0 if the input has no number
        self.builder.call(self.scanf_fn, [self.text(b"%d"), slot])
        return self.builder.load(slot, name="read")

    def visit_print(self, node):
        arg = node.arg
        if isinstance(arg, StringNode):                 # through %s, so a '%' in the text stays '%'
            self.printf(b"%s\n", self.text(arg.value))
        elif arg.type in self.types and isinstance(self.types[arg.type], ir.IdentifiedStructType):
            self.print_struct(self.pointer_to(arg), arg.type, b"")
            self.printf(b"\n")
        else:
            self.print_value(arg.accept(self), arg.type, b"", b"\n")

    def print_value(self, val, type_name, before, after):
        if type_name == "bool":
            val = self.builder.select(val, self.cstr(self.str_true), self.cstr(self.str_false), name="text")
        spec = {"i32": b"%d", "i64": b"%lld", "bool": b"%s"}[type_name]
        self.printf(before + spec + after, val)

    def print_struct(self, ptr, struct_name, before):
        """Point{x: 3, y: 4}: walks the field table, so a new struct needs no new code."""
        self.printf(before + struct_name.encode() + b"{")
        for i, f in enumerate(self.structs[struct_name].fields):
            label = (b", " if i else b"") + f.name.encode() + b": "
            fptr = self.field_ptr(ptr, i, f.name)
            if f.type_name in self.structs:
                self.print_struct(fptr, f.type_name, label)
            else:
                self.print_value(self.builder.load(fptr, name=f.name), f.type_name, label, b"")
        self.printf(b"}")

    def visit_const(self, node):
        return ir.Constant(self.types[node.type], node.value)

    def visit_bool(self, node):
        return ir.Constant(I1, 1 if node.value else 0)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 compiler.py [--ast | --ast-checked] input.txt [output.ll]", file=sys.stderr)
        sys.exit(1)

    dump_ast = dump_checked = False
    args = sys.argv[1:]
    if args[0] in ("--ast", "--ast-checked"):
        dump_ast, dump_checked = args[0] == "--ast", args[0] == "--ast-checked"
        flag = args[0]
        args = args[1:]
        if len(args) != 1:
            print(f"Usage: python3 compiler.py {flag} input.txt", file=sys.stderr)
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
        if dump_checked:
            ast.dump()
            sys.exit(0)
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
