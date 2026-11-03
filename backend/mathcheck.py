"""Small, auditable scalar formula checker; never treats a transcription as verified PDF text."""

import ast
import re

import numpy as np
import sympy as sp


FUNCTIONS = {"sqrt": sp.sqrt, "sin": sp.sin, "cos": sp.cos,
             "exp": sp.exp, "log": sp.log, "abs": sp.Abs}
ASSUMPTIONS = {"real", "positive", "nonzero", "integer"}


def source_number_matches(lhs, rhs, left_source, right_source):
    """Locate literal inputs in the two cited excerpts; this does not verify table row meaning."""
    def inspect(expression, source):
        tree = ast.parse(expression, mode="eval")
        numbers = [ast.get_source_segment(expression, node) for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and type(node.value) in (int, float)]
        return [{"number": number, "present": bool(re.search(
            rf"(?<![A-Za-z0-9_.]){re.escape(number)}(?![A-Za-z0-9_.])", source))}
                for number in numbers]

    left = inspect(lhs, left_source)
    right = inspect(rhs, right_source)
    return {"left": left, "right": right,
            "all_present": all(item["present"] for item in left + right) if left or right else None}


def parse_expression(source, assumptions, symbols):
    if not isinstance(source, str) or not source or len(source) > 160:
        raise ValueError("表达式必须为 1 到 160 个字符")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError("表达式语法无效") from exc
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError("表达式过于复杂")

    def convert(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1_000_000:
                raise ValueError("数字超出限制")
            return sp.Rational(str(node.value))
        if isinstance(node, ast.Name) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", node.id):
            if node.id not in symbols:
                kind = assumptions.get(node.id, "real")
                symbols[node.id] = sp.Symbol(node.id, real=True, **({kind: True} if kind != "real" else {}))
            return symbols[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = convert(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = convert(node.left), convert(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
            if isinstance(node.op, ast.Pow):
                if not right.is_Integer or abs(int(right)) > 8:
                    raise ValueError("仅支持绝对值不超过 8 的整数幂")
                return left ** right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords:
                return FUNCTIONS[node.func.id](convert(node.args[0]))
        raise ValueError("表达式含不支持的运算")

    return convert(tree.body)


def verify_equation_pair(lhs, rhs, assumptions=None):
    assumptions = assumptions or {}
    if not isinstance(assumptions, dict) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", name)
        or kind not in ASSUMPTIONS for name, kind in assumptions.items()
    ):
        raise ValueError("符号假设只能是 real、positive、nonzero 或 integer")
    symbols = {}
    left = parse_expression(lhs, assumptions, symbols)
    right = parse_expression(rhs, assumptions, symbols)
    difference = sp.simplify(left - right)
    result = {"status": "inconclusive", "lhs": str(left), "rhs": str(right),
              "difference": str(difference), "assumptions": assumptions,
              "counterexample": None,
              "tool_versions": {"sympy": sp.__version__, "numpy": np.__version__}}
    if difference == 0:
        result["status"] = "equivalent"
        return result

    ordered = sorted(symbols)
    inputs = [symbols[name] for name in ordered]
    left_fn = sp.lambdify(inputs, left, modules="numpy")
    right_fn = sp.lambdify(inputs, right, modules="numpy")
    rng = np.random.default_rng(2026)
    for attempt in range(16):
        sample = {}
        for name in ordered:
            value = float(rng.uniform(0.3, 3) if assumptions.get(name) == "positive"
                          else rng.choice([-3, -2, -1, 1, 2, 3]) if assumptions.get(name) == "integer"
                          else rng.uniform(-3, 3))
            if assumptions.get(name) == "nonzero" and abs(value) < 0.2:
                value = 1.0
            sample[name] = value
        try:
            with np.errstate(all="ignore"):
                left_value = float(left_fn(*(sample[name] for name in ordered)))
                right_value = float(right_fn(*(sample[name] for name in ordered)))
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            continue
        if np.isfinite(left_value) and np.isfinite(right_value) and not np.isclose(
            left_value, right_value, rtol=1e-9, atol=1e-9
        ):
            result["status"] = "counterexample"
            result["counterexample"] = {"symbols": sample, "lhs": left_value, "rhs": right_value,
                                        "sample_index": attempt}
            return result
    return result
