#!/usr/bin/env python3
"""
Math agent — a standalone deterministic solver (SymPy).

Runs as its own process. Reads a JSON job from stdin:
    {"operation": "solve|evaluate|differentiate|integrate|simplify|factor|expand",
     "expression": "...", "variable": "x"}
and writes a JSON result to stdout. It does the actual computation — the
language model only *prepares* the job and *calls* this agent.
"""
import sys
import re
import json
import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations,
    implicit_multiplication_application, convert_xor,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
# block anything that could be code rather than maths
BAD = re.compile(r"__|\bimport\b|\blambda\b|\bexec\b|\beval\b|os\.|sys\.|\bopen\b|subprocess|globals|locals")


def parse(s: str):
    s = s.strip()
    if BAD.search(s):
        raise ValueError("disallowed token in expression")
    return parse_expr(s, transformations=TRANSFORMS, evaluate=True)


def pick_var(expr, given):
    if given:
        return sp.Symbol(given)
    syms = sorted(expr.free_symbols, key=lambda x: x.name)
    return syms[0] if syms else sp.Symbol("x")


def approx(value):
    try:
        if value.free_symbols or value.is_Integer:
            return None
        return str(sp.N(value, 10))
    except Exception:
        return None


def run(operation, expression, variable=None):
    op = (operation or "").lower().strip()
    expression = (expression or "").strip()
    if not expression:
        raise ValueError("empty expression")
    if len(expression) > 500:
        raise ValueError("expression too long")

    if op in ("solve", "solve_equation", "roots"):
        if "=" in expression:
            lhs, rhs = expression.split("=", 1)
            eq = sp.Eq(parse(lhs), parse(rhs))
        else:
            eq = sp.Eq(parse(expression), 0)
        var = sp.Symbol(variable) if variable else pick_var(eq.lhs - eq.rhs, None)
        sols = sp.solve(eq, var, dict=False)
        sols = sols if isinstance(sols, list) else [sols]
        return {
            "operation": "solve", "equation": "%s = %s" % (eq.lhs, eq.rhs), "variable": str(var),
            "solutions": [str(s) for s in sols],
            "approx": [a for a in (approx(s) for s in sols) if a],
        }

    if op in ("evaluate", "compute", "arithmetic", "calc", "value"):
        e = parse(expression)
        val = sp.simplify(e)
        return {"operation": "evaluate", "input": expression, "exact": str(val), "numeric": approx(val)}

    if op in ("differentiate", "derivative", "diff"):
        e = parse(expression); v = pick_var(e, variable)
        return {"operation": "differentiate", "input": str(e), "variable": str(v),
                "result": str(sp.diff(e, v))}

    if op in ("integrate", "integral", "antiderivative"):
        e = parse(expression); v = pick_var(e, variable)
        return {"operation": "integrate", "input": str(e), "variable": str(v),
                "result": str(sp.integrate(e, v)) + " + C"}

    if op == "simplify":
        e = parse(expression)
        return {"operation": "simplify", "input": str(e), "result": str(sp.simplify(e))}

    if op == "factor":
        e = parse(expression)
        return {"operation": "factor", "input": str(e), "result": str(sp.factor(e))}

    if op == "expand":
        e = parse(expression)
        return {"operation": "expand", "input": str(e), "result": str(sp.expand(e))}

    raise ValueError("unsupported operation: %s" % operation)


def main():
    try:
        job = json.loads(sys.stdin.read() or "{}")
        res = run(job.get("operation"), job.get("expression"), job.get("variable"))
        print(json.dumps({"ok": True, **res}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:200]}))


if __name__ == "__main__":
    main()
