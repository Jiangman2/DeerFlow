"""Small, bounded Decimal evaluator for settlement workbook arithmetic.

No Excel engine, cached values, Python eval, external links or network access.
Only same-sheet monetary cells may be referenced.
"""
from __future__ import annotations

import re
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext

CELL = r"\$?[A-Z]{1,3}\$?[1-9][0-9]{0,5}"
TOKEN = re.compile(
    rf"\s*(?:(?P<ref>(?:(?:Sheet2|'Sheet2')!)?{CELL})"
    r"|(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:E[+-]?[0-9]{1,3})?)"
    r"|(?P<name>[A-Z_][A-Z_0-9]*)|(?P<symbol>[()+*/,:\-]))", re.I
)


class FormulaError(ValueError):
    pass


def cell_parts(ref):
    match = re.fullmatch(r"(\$?)([A-Z]{1,3})(\$?)([1-9][0-9]{0,5})", ref.upper())
    if not match:
        raise FormulaError("无效单元格引用")
    col = 0
    for letter in match[2]:
        col = col * 26 + ord(letter) - 64
    return match[1], col, match[3], int(match[4])


def cell_name(col, row):
    if not (1 <= col <= 16384 and 1 <= row <= 1048576):
        raise FormulaError("单元格引用越界")
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters + str(row)


def tokens(formula):
    text = formula.strip().removeprefix("=")
    if not text or len(text) > 4096:
        raise FormulaError("公式为空或过长")
    result, position = [], 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match:
            raise FormulaError("仅支持本表金额引用、四则运算、SUM 和 ROUND；不支持外部引用或其他函数")
        result.append((match.lastgroup, match.group(match.lastgroup)))
        position = match.end()
        if len(result) > 512:
            raise FormulaError("公式过于复杂")
    return result


def translate_shared(formula, origin, target):
    _, oc, _, ro = cell_parts(origin)
    _, tc, _, rt = cell_parts(target)
    translated = []
    for kind, value in tokens(formula):
        if kind == "ref":
            prefix, sep, cell = value.rpartition("!")
            a, c, b, r = cell_parts(cell if sep else value)
            shifted = cell_name(c if a else c + tc - oc, r if b else r + rt - ro)
            col, row = re.fullmatch(r"([A-Z]+)([0-9]+)", shifted).groups()
            value = (prefix + "!" if sep else "") + a + col + b + row
        translated.append(value)
    return "".join(translated)


def finite_number(value):
    if isinstance(value, bool):
        raise FormulaError("金额不能是布尔值")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (DecimalException, ValueError):
        raise FormulaError("金额必须为数值，单位为万元") from None
    if not number.is_finite() or abs(number) > Decimal("1e18"):
        raise FormulaError("金额不是有限数值或超出支持范围")
    return number


class FormulaEvaluator:
    def __init__(self, values, formulas, allowed):
        self.values, self.formulas, self.allowed = values, formulas, allowed
        self.cache = {}
        self.active = set()
        self.steps = 0

    def value(self, ref):
        ref = ref.split("!")[-1].replace("$", "").upper()
        self.steps += 1
        if self.steps > 20000:
            raise FormulaError("公式总计算量超过上限")
        if ref not in self.allowed:
            raise FormulaError("公式只能引用本表业务金额区域")
        if ref in self.cache:
            return self.cache[ref]
        if ref in self.active or len(self.active) >= 48:
            raise FormulaError("公式存在循环引用或引用层级过深")
        self.active.add(ref)
        try:
            if ref in self.formulas:
                with localcontext() as ctx:
                    ctx.prec = 32
                    result = Parser(tokens(self.formulas[ref]), self).parse()
            else:
                raw = self.values.get(ref)
                result = Decimal(0) if raw is None or str(raw).strip() == "" else finite_number(raw)
            result = finite_number(result)
            self.cache[ref] = result
            return result
        except DecimalException:
            raise FormulaError("公式计算失败（例如除零或无效金额）") from None
        finally:
            self.active.remove(ref)


class Parser:
    def __init__(self, items, evaluator):
        self.items, self.evaluator, self.pos, self.depth = items, evaluator, 0, 0

    def peek(self, value=None):
        item = self.items[self.pos] if self.pos < len(self.items) else (None, None)
        return item if value is None else item[1] == value

    def take(self, value=None):
        item = self.peek()
        if item[0] is None or (value is not None and item[1] != value):
            raise FormulaError("公式语法不完整")
        self.pos += 1
        return item

    def parse(self):
        result = self.expression()
        if self.pos != len(self.items):
            raise FormulaError("公式包含不支持的表达式")
        return result

    def expression(self):
        value = self.product()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            other = self.product()
            value = value + other if op == "+" else value - other
        return value

    def product(self):
        value = self.atom()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            other = self.atom()
            value = value * other if op == "*" else value / other
        return value

    def atom(self):
        self.depth += 1
        if self.depth > 48:
            raise FormulaError("公式嵌套过深")
        try:
            kind, value = self.take()
            if value in ("+", "-"):
                return self.atom() * (1 if value == "+" else -1)
            if value == "(":
                result = self.expression()
                self.take(")")
                return result
            if kind == "number":
                return finite_number(value)
            if kind == "ref":
                return self.evaluator.value(value)
            if kind == "name" and value.upper() in ("SUM", "ROUND"):
                self.take("(")
                if value.upper() == "ROUND":
                    number = self.expression()
                    self.take(",")
                    digits = self.expression()
                    self.take(")")
                    if digits != digits.to_integral_value() or abs(digits) > 12:
                        raise FormulaError("ROUND 位数须为 -12 至 12 的整数")
                    return number.quantize(Decimal(1).scaleb(int(-digits)), rounding=ROUND_HALF_UP)
                result = Decimal(0)
                while True:
                    if (self.peek()[0] == "ref" and self.pos + 1 < len(self.items)
                            and self.items[self.pos + 1][1] == ":"):
                        first = self.take()[1].split("!")[-1]
                        self.take(":")
                        kind, last = self.take()
                        if kind != "ref":
                            raise FormulaError("SUM 区间无效")
                        _, c1, _, r1 = cell_parts(first)
                        _, c2, _, r2 = cell_parts(last.split("!")[-1])
                        if c2 < c1 or r2 < r1 or (c2 - c1 + 1) * (r2 - r1 + 1) > 2048:
                            raise FormulaError("SUM 区间无效或过大")
                        result += sum((self.evaluator.value(cell_name(c, r))
                                       for c in range(c1, c2 + 1) for r in range(r1, r2 + 1)), Decimal(0))
                    else:
                        result += self.expression()
                    if self.peek(")"):
                        self.take(")")
                        return result
                    self.take(",")
            raise FormulaError("仅支持四则运算、SUM 和 ROUND")
        finally:
            self.depth -= 1
