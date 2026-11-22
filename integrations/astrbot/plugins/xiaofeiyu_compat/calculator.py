"""Bounded numeric expressions, without Python evaluation or symbolic execution."""
import ast
import math
import operator

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
FUNCS = {'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
         'log': math.log, 'ln': math.log, 'log10': math.log10, 'exp': math.exp,
         'abs': abs, 'round': round, 'floor': math.floor, 'ceil': math.ceil}


def _bounded(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError('number outside range')
    return value


def calculate(expression):
    if not isinstance(expression, str) or not 1 <= len(expression.strip()) <= 180:
        raise ValueError('expression length')
    try:
        tree = ast.parse(expression.replace('^', '**').strip(), mode='eval')
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('invalid expression') from exc
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError('too many operations')

    def visit(node):
        if isinstance(node, ast.Constant):
            return _bounded(node.value)
        if isinstance(node, ast.Name) and node.id in ('pi', 'e'):
            return getattr(math, node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _bounded((-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand))
        if isinstance(node, ast.BinOp):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(b) > 100:
                    raise ValueError('power limit')
                return _bounded(a ** b)
            if type(node.op) in OPS:
                return _bounded(OPS[type(node.op)](a, b))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords and 1 <= len(node.args) <= 2:
            values = [visit(x) for x in node.args]
            if node.func.id == 'factorial':
                n = values[0]
                if len(values) != 1 or type(n) != int or not 0 <= n <= 60:
                    raise ValueError('factorial limit')
                return math.factorial(n)
            if node.func.id in FUNCS:
                try:
                    return _bounded(FUNCS[node.func.id](*values))
                except TypeError as exc:
                    raise ValueError('function arguments') from exc
        raise ValueError('unsupported expression')

    value = visit(tree.body)
    return str(value) if type(value) == int else format(value, '.12g')
