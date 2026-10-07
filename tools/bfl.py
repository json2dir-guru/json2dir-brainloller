#!/usr/bin/env python3
"""bfl.py -- compile a small structured language (.bfl) into pure Brainfuck.

    python3 tools/bfl.py json2dir.bfl > json2dir.bf

The output uses only the eight commands + - < > . , [ ] (plus line breaks).
Target machine: 8-bit wrapping cells, tape unbounded to the right, the
pointer never moves left of cell 0.  Every ',' is emitted as "[-]," so the
program sees 0 at end of input under the "unchanged" and "0" EOF conventions
(and 255 under "-1"); the source decides what that means.

Language
--------
    # comment
    var a, b c                 byte variables, each one fixed tape cell
    macro name(p, q) { ... }   inlined at every call; params alias variables
    name(x, y)   name          macro call

    x = expr     x += expr     x -= expr
        expr := term (('+'|'-') term)*
        term := NUM | 'c' | var | var '*' NUM | getc (alone)
    putc x       putc 'c'      print "text"
    divmod x, K, q, r          q = x / K, r = x % K   (x preserved)
    if cond { } elif cond { } else { }
    while cond { }
    fail "message"             first failure wins: ok = 0, err = message id
    failreport                 prints "\njson2dir: <message>\n" for err
    pushpath x                 appends byte x (must be nonzero) to the path
    popname                    removes the last "/name" from the path
    printpath                  prints the path without its leading "/",
                               shell-quoted for use inside '...'

    cond := and ('||' and)* ; and := not ('&&' not)* ; not := '!' not | '(' cond ')' | cmp
    cmp  := operand [('=='|'!='|'<'|'<='|'>'|'>=') operand] ; operand := var | NUM | 'c'

Tape layout: variables, then a scratch pool, then the path area.  The path is
stored as one byte per even cell (all nonzero, so [>>] / [<<] walk over it),
with a zero "lane" cell after each byte used to carry values along the walk.
"""

import re
import sys

POOL_SIZE = 48


# ---------------------------------------------------------------- lexer

TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r\n]+|\#[^\n]*)
  | (?P<num>\d+)
  | (?P<chr>'(?:\\.|[^\\'])')
  | (?P<str>"(?:\\.|[^\\"])*")
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|\+=|-=|&&|\|\||[{}()<>=+\-*,!])
""", re.X)

KEYWORDS = {"var", "macro", "if", "elif", "else", "while", "putc", "print", "fail",
            "failreport", "pushpath", "popname", "printpath", "divmod", "getc"}

ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'", "0": "\0"}


def unescape(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\":
            out.append(ESC[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def lex(text):
    toks, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise SyntaxError("bad character at %r" % text[pos:pos + 20])
        pos = m.end()
        kind = m.lastgroup
        v = m.group()
        if kind == "ws":
            continue
        if kind == "num":
            toks.append(("num", int(v)))
        elif kind == "chr":
            toks.append(("num", ord(unescape(v[1:-1]))))
        elif kind == "str":
            toks.append(("str", unescape(v[1:-1])))
        elif kind == "id":
            toks.append(("id", v))
        else:
            toks.append(("op", v))
    toks.append(("eof", None))
    return toks


# ---------------------------------------------------------------- parser

class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def accept(self, kind, val=None):
        tok = self.peek()
        if tok[0] == kind and (val is None or tok[1] == val):
            self.i += 1
            return tok
        return None

    def expect(self, kind, val=None):
        tok = self.accept(kind, val)
        if not tok:
            raise SyntaxError("expected %s %r, got %r" % (kind, val, self.peek()))
        return tok

    def program(self):
        items = []
        while self.peek()[0] != "eof":
            items.append(self.stmt(top=True))
        return items

    def block(self):
        self.expect("op", "{")
        body = []
        while not self.accept("op", "}"):
            body.append(self.stmt())
        return body

    def stmt(self, top=False):
        tok = self.next()
        if tok[0] != "id":
            raise SyntaxError("statement expected, got %r" % (tok,))
        w = tok[1]
        if w == "var":
            names = [self.expect("id")[1]]
            while (self.peek()[0] == "id" and self.peek()[1] not in KEYWORDS) or self.peek() == ("op", ","):
                self.accept("op", ",")
                names.append(self.expect("id")[1])
            return ("var", names)
        if w == "macro":
            name = self.expect("id")[1]
            params = []
            if self.accept("op", "("):
                if not self.accept("op", ")"):
                    params.append(self.expect("id")[1])
                    while self.accept("op", ","):
                        params.append(self.expect("id")[1])
                    self.expect("op", ")")
            return ("macro", name, params, self.block())
        if w == "if":
            arms = [(self.cond(), self.block())]
            other = None
            while True:
                if self.accept("id", "elif"):
                    arms.append((self.cond(), self.block()))
                elif self.accept("id", "else"):
                    other = self.block()
                    break
                else:
                    break
            return ("if", arms, other)
        if w == "while":
            return ("while", self.cond(), self.block())
        if w == "putc":
            tok = self.next()
            return ("putc", tok)
        if w == "print":
            return ("print", self.expect("str")[1])
        if w == "fail":
            return ("fail", self.expect("str")[1])
        if w == "failreport":
            return ("failreport",)
        if w == "pushpath":
            return ("pushpath", self.expect("id")[1])
        if w == "popname":
            return ("popname",)
        if w == "printpath":
            return ("printpath",)
        if w == "divmod":
            x = self.expect("id")[1]
            self.expect("op", ",")
            k = self.expect("num")[1]
            self.expect("op", ",")
            q = self.expect("id")[1]
            self.expect("op", ",")
            r = self.expect("id")[1]
            return ("divmod", x, k, q, r)
        op = self.peek()
        if op in (("op", "="), ("op", "+="), ("op", "-=")):
            self.next()
            return ("assign", w, op[1], self.expr())
        args = []
        if self.accept("op", "("):
            if not self.accept("op", ")"):
                args.append(self.expect("id")[1])
                while self.accept("op", ","):
                    args.append(self.expect("id")[1])
                self.expect("op", ")")
        return ("call", w, args)

    def expr(self):
        if self.accept("id", "getc"):
            return "getc"
        terms = [(1,) + self.term()]
        while self.peek() in (("op", "+"), ("op", "-")):
            sign = 1 if self.next()[1] == "+" else -1
            terms.append((sign,) + self.term())
        return terms

    def term(self):
        tok = self.next()
        if tok[0] == "num":
            return ("const", tok[1])
        if tok[0] == "id":
            if self.accept("op", "*"):
                return ("mul", tok[1], self.expect("num")[1])
            return ("var", tok[1])
        raise SyntaxError("bad term %r" % (tok,))

    def cond(self):
        c = self.cand()
        while self.accept("op", "||"):
            c = ("or", c, self.cand())
        return c

    def cand(self):
        c = self.cnot()
        while self.accept("op", "&&"):
            c = ("and", c, self.cnot())
        return c

    def cnot(self):
        if self.accept("op", "!"):
            return ("not", self.cnot())
        if self.accept("op", "("):
            c = self.cond()
            self.expect("op", ")")
            return c
        a = self.operand()
        for rel in ("==", "!=", "<", "<=", ">", ">="):
            if self.accept("op", rel):
                return ("rel", rel, a, self.operand())
        return ("truth", a)

    def operand(self):
        tok = self.next()
        if tok[0] == "num":
            return ("const", tok[1] & 255)
        if tok[0] == "id":
            return ("var", tok[1])
        raise SyntaxError("bad operand %r" % (tok,))


# ---------------------------------------------------------------- code generator

class Gen:
    def __init__(self):
        self.out = []
        self.pos = 0
        self.vars = {}
        self.macros = {}
        self.pool_base = None
        self.pool_used = set()
        self.max_pool = 0
        self.messages = []
        self.env = [{}]

    # -- low level
    def emit(self, s):
        self.out.append(s)

    def go(self, cell):
        assert cell >= 0
        d = cell - self.pos
        self.emit(">" * d if d > 0 else "<" * -d)
        self.pos = cell

    def add(self, cell, k):
        k &= 255
        self.go(cell)
        self.emit("+" * k if k <= 128 else "-" * (256 - k))

    def clear(self, cell):
        self.go(cell)
        self.emit("[-]")

    def loop(self, cell, body):
        """while cell != 0 { body }  -- body must leave the pointer anywhere known."""
        self.go(cell)
        self.emit("[")
        body()
        self.go(cell)
        self.emit("]")

    def move(self, src, dsts):
        """dst_i += src * k_i for (dst, k) in dsts; src = 0."""
        def body():
            self.add(src, -1)
            for d, k in dsts:
                self.add(d, k)
        self.loop(src, body)

    # -- temps
    def tmp(self):
        for i in range(POOL_SIZE):
            if i not in self.pool_used:
                self.pool_used.add(i)
                self.max_pool = max(self.max_pool, i + 1)
                return self.pool_base + i
        raise RuntimeError("scratch pool exhausted")

    def free(self, *cells):
        for c in cells:
            self.pool_used.remove(c - self.pool_base)

    # -- variables
    def var(self, name):
        for scope in reversed(self.env):
            if name in scope:
                name = scope[name]
                break
        if name not in self.vars:
            raise NameError("unknown variable " + name)
        return self.vars[name]

    def copy_into(self, src, dst, k=1):
        """dst += src * k, src preserved."""
        t = self.tmp()
        self.move(src, [(dst, k), (t, 1)])
        self.move(t, [(src, 1)])
        self.free(t)

    # -- path area
    @property
    def guard(self):
        return self.path_base - 2

    def pushpath(self, x):
        lane = self.path_base - 1
        self.copy_into(x, lane)
        self.go(self.guard)
        self.emit(">>[<[->>+<<]>>>]<[->+<]>")   # walk to the end carrying the byte
        self.emit("<<[<<]")                      # back to the guard
        self.pos = self.guard

    def popname(self):
        self.go(self.guard)
        self.emit(">>[>>]<+[-<" + "-" * 47 + "[[-]<+>]<]<[<<]")
        self.pos = self.guard

    def printpath(self):
        self.go(self.guard)
        self.emit(">>>>[[->+>>+<<<]>>>[-<<<+>>>]+<<" + "-" * 39 + "[[-]<.>>>-<<]>>[-"
                  + "+" * 39 + "." + "+" * 53 + "." + "-" * 53 + "..[-]]<]<<[<<]")
        self.pos = self.guard

    # -- printing constants
    def print_str(self, s):
        p = self.tmp()
        a = self.tmp()
        cur = 0
        for ch in s.encode("utf-8"):
            d = (ch - cur) & 255
            if d > 128:
                d -= 256
            self.delta(p, a, d)
            self.go(p)
            self.emit(".")
            cur = ch
        self.clear(p)
        self.free(p, a)

    def delta(self, p, a, d):
        n = abs(d)
        sign = "+" if d > 0 else "-"
        best = (n, None)
        for x in range(2, 16):
            y, r = divmod(n, x)
            if r > x // 2:
                y, r = y + 1, r - x
            cost = x + y + abs(r) + 6 + 2 * abs(a - p)
            if y > 0 and cost < best[0]:
                best = (cost, (x, y, r))
        if best[1] is None:
            self.go(p)
            self.emit(sign * n)
            return
        x, y, r = best[1]
        self.add(a, x)
        self.go(a)
        self.emit("[-")
        self.go(p)
        self.emit(sign * y)
        self.go(a)
        self.emit("]")
        self.go(p)
        if r:
            rs = sign if r > 0 else ("-" if sign == "+" else "+")
            self.emit(rs * abs(r))

    # -- expressions
    def refs(self, terms, x):
        return any(t[1] in ("var", "mul") and self.var(t[2]) == x for t in terms)

    def add_terms(self, dst, terms, sign=1):
        const = 0
        for t in terms:
            s = t[0] * sign
            if t[1] == "const":
                const += s * t[2]
            elif t[1] == "var":
                self.copy_into(self.var(t[2]), dst, s)
            else:
                self.copy_into(self.var(t[2]), dst, s * t[3])
        if const & 255:
            self.add(dst, const)

    def assign(self, name, op, e):
        x = self.var(name)
        if e == "getc":
            assert op == "="
            self.go(x)
            self.emit("[-],")
            return
        if op == "=":
            if self.refs(e, x):
                t = self.tmp()
                self.add_terms(t, e)
                self.clear(x)
                self.move(t, [(x, 1)])
                self.free(t)
            else:
                self.clear(x)
                self.add_terms(x, e)
        else:
            sign = 1 if op == "+=" else -1
            if self.refs(e, x):
                t = self.tmp()
                self.add_terms(t, e)
                self.move(t, [(x, sign)])
                self.free(t)
            else:
                self.add_terms(x, e, sign)

    # -- conditions: each returns a fresh temp holding 0 or 1
    def load(self, operand):
        t = self.tmp()
        if operand[0] == "const":
            self.add(t, operand[1])
        else:
            self.copy_into(self.var(operand[1]), t)
        return t

    def nz_into(self, t, f):
        """f = (t != 0); t = 0.  f must be 0."""
        self.go(t)
        self.emit("[[-]")
        self.add(f, 1)
        self.go(t)
        self.emit("]")

    def cond(self, c):
        kind = c[0]
        f = self.tmp()
        if kind == "truth":
            t = self.load(c[1])
            self.nz_into(t, f)
            self.free(t)
        elif kind == "not":
            g = self.cond(c[1])
            self.add(f, 1)
            self.move(g, [(f, -1)])
            self.free(g)
        elif kind == "and":
            g = self.cond(c[1])
            h = self.cond(c[2])
            self.go(g)
            self.emit("[-")
            self.go(h)
            self.emit("[-")
            self.add(f, 1)
            self.go(h)
            self.emit("]")
            self.go(g)
            self.emit("]")
            self.clear(h)
            self.free(g, h)
        elif kind == "or":
            g = self.cond(c[1])
            h = self.cond(c[2])
            self.move(g, [(f, 1)])
            self.go(h)
            self.emit("[-")
            self.clear(f)
            self.add(f, 1)
            self.go(h)
            self.emit("]")
            self.free(g, h)
        else:
            _, rel, a, b = c
            if rel in ("==", "!="):
                t = self.load(a)
                if b[0] == "const":
                    self.add(t, -b[1])
                else:
                    self.copy_into(self.var(b[1]), t, -1)
                if rel == "!=":
                    self.nz_into(t, f)
                else:
                    self.add(f, 1)
                    self.go(t)
                    self.emit("[[-]")
                    self.add(f, -1)
                    self.go(t)
                    self.emit("]")
                self.free(t)
            else:
                if rel in (">", "<="):
                    a, b = b, a
                self.lt(a, b, f)
                if rel in ("<=", ">="):
                    g = self.tmp()
                    self.move(f, [(g, 1)])
                    self.add(f, 1)
                    self.move(g, [(f, -1)])
                    self.free(g)
        return f

    def lt(self, a, b, f):
        """f = (a < b), unsigned bytes."""
        u = self.load(a)
        v = self.load(b)
        g = self.tmp()

        def both():
            x = self.tmp()
            y = self.tmp()
            self.copy_into(u, x)
            self.copy_into(v, y)
            self.go(x)
            self.emit("[[-]")
            self.go(y)
            self.emit("[[-]")
            self.add(g, 1)
            self.go(y)
            self.emit("]")
            self.go(x)
            self.emit("]")
            self.clear(y)
            self.free(x, y)

        both()

        def body():
            self.add(g, -1)
            self.add(u, -1)
            self.add(v, -1)
            both()
        self.loop(g, body)
        self.clear(u)
        self.nz_into(v, f)
        self.free(u, v, g)

    # -- statements
    def block(self, stmts):
        for s in stmts:
            self.stmt(s)

    def stmt(self, s):
        k = s[0]
        if k == "var":
            for n in s[1]:
                assert n not in self.vars, n
                self.vars[n] = len(self.vars)
        elif k == "macro":
            self.macros[s[1]] = (s[2], s[3])
        elif k == "call":
            params, body = self.macros[s[1]]
            assert len(params) == len(s[2]), s
            scope = {}
            for p, a in zip(params, s[2]):
                for sc in reversed(self.env):
                    if a in sc:
                        a = sc[a]
                        break
                scope[p] = a
            self.env.append(scope)
            self.block(body)
            self.env.pop()
        elif k == "assign":
            self.assign(s[1], s[2], s[3])
        elif k == "putc":
            tok = s[1]
            if tok[0] == "num":
                self.print_str(chr(tok[1]))
            else:
                self.go(self.var(tok[1]))
                self.emit(".")
        elif k == "print":
            self.print_str(s[1])
        elif k == "fail":
            if s[1] not in self.messages:
                self.messages.append(s[1])
            n = self.messages.index(s[1]) + 1
            self.stmt(("if", [(("truth", ("var", "ok")),
                               [("assign", "ok", "=", [(1, "const", 0)]),
                                ("assign", "err", "=", [(1, "const", n)])])], None))
        elif k == "failreport":
            for n, msg in enumerate(self.messages, 1):
                self.stmt(("if", [(("rel", "==", ("var", "err"), ("const", n)),
                                   [("print", "\njson2dir: " + msg + "\n")])], None))
        elif k == "if":
            self.gen_if(s[1], s[2])
        elif k == "while":
            f = self.cond(s[1])

            def body():
                self.add(f, -1)
                self.block(s[2])
                g = self.cond(s[1])
                self.move(g, [(f, 1)])
                self.free(g)
            self.loop(f, body)
            self.free(f)
        elif k == "pushpath":
            self.pushpath(self.var(s[1]))
        elif k == "popname":
            self.popname()
        elif k == "printpath":
            self.printpath()
        elif k == "divmod":
            _, x, kk, q, r = s
            x, q, r = self.var(x), self.var(q), self.var(r)
            self.clear(q)
            self.clear(r)
            t = self.tmp()
            self.copy_into(x, t)

            def body():
                self.add(t, -1)
                self.add(r, 1)
                f = self.cond(("rel", "==", ("var", "__r"), ("const", kk)))
                self.loop(f, lambda: (self.add(f, -1), self.clear(r), self.add(q, 1)))
                self.free(f)
            self.vars["__r"] = r
            self.loop(t, body)
            del self.vars["__r"]
            self.free(t)
        else:
            raise ValueError(s)

    def gen_if(self, arms, other):
        c, body = arms[0]
        rest = arms[1:]
        f = self.cond(c)
        has_else = bool(rest) or other is not None
        e = None
        if has_else:
            e = self.tmp()
            self.add(e, 1)

        def then():
            self.add(f, -1)
            if e is not None:
                self.add(e, -1)
            self.block(body)
        self.loop(f, then)
        self.free(f)
        if has_else:
            def otherwise():
                self.add(e, -1)
                if rest:
                    self.gen_if(rest, other)
                else:
                    self.block(other)
            self.loop(e, otherwise)
            self.free(e)


def peephole(code):
    prev = None
    while prev != code:
        prev = code
        code = re.sub(r"<>|><|\+-|-\+", "", code)
    return code


def compile_source(text):
    items = Parser(lex(text)).program()
    g = Gen()
    for it in items:                       # declarations first
        if it[0] == "var":
            g.stmt(it)
    g.pool_base = len(g.vars) + 1          # +1: room for macro-private "__r" alias (unused cell)
    g.path_base = g.pool_base + POOL_SIZE + 2
    for it in items:
        if it[0] != "var":
            g.stmt(it)
    code = peephole("".join(g.out))
    assert code.count("[") == code.count("]")
    return code, g


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: bfl.py SOURCE.bfl > OUT.bf")
    with open(sys.argv[1], encoding="utf-8") as fh:
        code, g = compile_source(fh.read())
    sys.stderr.write("bfl: %d commands, %d variables, %d scratch cells, path area at cell %d\n"
                     % (len(code), len(g.vars), g.max_pool, g.path_base))
    width = 100
    sys.stdout.write("".join(code[i:i + width] + "\n" for i in range(0, len(code), width)))


if __name__ == "__main__":
    main()
