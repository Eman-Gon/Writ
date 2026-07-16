"""Small local PEP 604 evaluator for Pydantic on the system Python 3.9.

The shared schema intentionally uses modern annotations.  The demo machine's
preinstalled Pydantic supports 3.9 when this compatibility hook is present.
"""

from __future__ import annotations

import ast
import typing
from typing import Any, Mapping


class _UnionTransformer(ast.NodeTransformer):
    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        node = self.generic_visit(node)
        if isinstance(node.op, ast.BitOr):
            return ast.Subscript(
                value=ast.Attribute(value=ast.Name(id="typing", ctx=ast.Load()), attr="Union", ctx=ast.Load()),
                slice=ast.Tuple(elts=[node.left, node.right], ctx=ast.Load()),
                ctx=ast.Load(),
            )
        return node


def eval_type_backport(
    value: Any,
    globalns: dict[str, Any] | None = None,
    localns: Mapping[str, Any] | None = None,
    *,
    try_default: bool = True,
) -> Any:
    expression = getattr(value, "__forward_arg__", value)
    if not isinstance(expression, str):
        return value
    tree = _UnionTransformer().visit(ast.parse(expression, mode="eval"))
    ast.fix_missing_locations(tree)
    namespace = dict(globalns or {})
    namespace["typing"] = typing
    return eval(compile(tree, "<annotation>", "eval"), namespace, dict(localns or {}))
