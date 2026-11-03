"""Check explicit matrix multiplication dimensions in extracted algorithm prose."""

import re

import sympy as sp


MATMUL = re.compile(
    r"\b(?P<count>[A-Za-z][A-Za-z0-9_]*|\d+)\s+parallel\s+multiplications\s+of\s+"
    r"(?P<left_rows>[A-Za-z][A-Za-z0-9_]*|\d+)\s*×\s*(?P<left_cols>[A-Za-z][A-Za-z0-9_]*|\d+)"
    r"\s+and\s+(?P<right_rows>[A-Za-z][A-Za-z0-9_]*|\d+)\s*×\s*"
    r"(?P<right_cols>[A-Za-z][A-Za-z0-9_]*|\d+)\s+matrices\b", re.IGNORECASE)


def dimension(token: str):
    if token.isdecimal():
        return sp.Integer(token)
    if token == "bh":
        return sp.Symbol("b", positive=True, integer=True) * sp.Symbol("h", positive=True, integer=True)
    return sp.Symbol(token, positive=True, integer=True)


def check_matmul_description(excerpt: str) -> dict | None:
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", excerpt)
    text = " ".join(text.split())
    match = MATMUL.search(text)
    if not match:
        return None
    parts = match.groupdict()
    left_inner = dimension(parts["left_cols"])
    right_inner = dimension(parts["right_rows"])
    valid = sp.simplify(left_inner - right_inner) == 0
    total = (sp.expand(dimension(parts["count"]) * dimension(parts["left_rows"])
                       * dimension(parts["right_cols"])) if valid else None)
    return {"status": "shape_consistent" if valid else "shape_mismatch",
            "parallel_count": parts["count"],
            "left_shape": [parts["left_rows"], parts["left_cols"]],
            "right_shape": [parts["right_rows"], parts["right_cols"]],
            "output_shape": [parts["left_rows"], parts["right_cols"]] if valid else None,
            "total_output_elements": str(total) if total is not None else None,
            "parser_version": "explicit-matmul-v1"}
