import ast
import operator

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class TransformExpr:
    """
    Safe arithmetic transform applied to an incoming telemetry value.

    The transform may be specified as:
      - a numeric multiplier (int or float)
      - a string arithmetic expression using variable 'x'

    Expressions may use any arithmetic operators supported by Python,
    including +, -, *, /, //, %, and **.

    Non-arithmetic syntax (function calls, attributes, indexing, etc.)
    is explicitly disallowed.
    """

    def __init__(self, value):
        """
        Create a TransformExpr from either a numeric scale or an expression string.

        Args:
            value (int | float | str):
                Numeric value = simple multiplier
                String value  = arithmetic expression using 'x'

        Raises:
            TypeError:
                If value is not int, float, or str.
            ValueError:
                If the expression has invalid syntax or unsupported elements.
        """
        # Numeric scale (fast path)
        if isinstance(value, (int, float)):
            self._mode = "scale"
            self._scale = value
            return

        # Expression scale
        if isinstance(value, str):
            self._mode = "expr"
            self._expr = value

            try:
                self._tree = ast.parse(value, mode="eval")
            except SyntaxError as e:
                raise ValueError("Invalid expression syntax") from e

            self._validate(self._tree.body)
            return

        raise TypeError("Transform must be int, float, or expression string")

    def _validate(self, node):
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN_OPS:
                raise ValueError("Unsupported binary operator")
            self._validate(node.left)
            self._validate(node.right)

        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY_OPS:
                raise ValueError("Unsupported unary operator")
            self._validate(node.operand)

        elif isinstance(node, ast.Name):
            if node.id != "x":
                raise ValueError("Only variable 'x' is allowed")

        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("Only numeric constants allowed")

        else:
            raise ValueError(
                f"Unsupported expression element: {type(node).__name__}"
            )

    def apply(self, x):
        if self._mode == "scale":
            return x * self._scale
        return self._eval(self._tree.body, x)

    def _eval(self, node, x):
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            return x

        if isinstance(node, ast.BinOp):
            return _BIN_OPS[type(node.op)](
                self._eval(node.left, x),
                self._eval(node.right, x),
            )

        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPS[type(node.op)](
                self._eval(node.operand, x)
            )

        raise RuntimeError("Invalid AST node encountered")

