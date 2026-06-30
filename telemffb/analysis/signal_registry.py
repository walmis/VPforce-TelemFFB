"""Extract signal metadata from BaseTelemetryData attribute docstrings.

Parses the AST of BaseTelemetryData.py at import time to build a
signal registry mapping field names to their type annotations and
docstring documentation. This keeps signal metadata in sync with
the source automatically.
"""

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class SignalInfo:
    """Metadata for a single telemetry signal."""
    name: str
    type_annotation: str
    docstring: str
    available_sims: List[str] = field(default_factory=list)
    units: Optional[str] = None
    category: Optional[str] = None


_SIM_NAMES = {"DCS", "MSFS", "IL2", "BMS", "XPLANE", "XP", "X-Plane"}
_SIM_NORMALIZE = {"XP": "XPLANE", "X-Plane": "XPLANE"}

# Patterns that indicate a sim provides the field
_SIM_POSITIVE = re.compile(
    r"\b(DCS|MSFS|IL2|BMS|XPLANE|XP|X-Plane)\b\s*[:.]",
    re.IGNORECASE,
)
# Patterns that indicate a sim does NOT provide the field
_SIM_NEGATIVE = re.compile(
    r"\b(DCS|MSFS|IL2|BMS|XPLANE|XP|X-Plane)\b\s*:\s*not available",
    re.IGNORECASE,
)
_SIMS_ALL_PATTERN = re.compile(r"Sims:\s*All\b", re.IGNORECASE)


def _normalize_sim(name: str) -> str:
    upper = name.upper()
    return _SIM_NORMALIZE.get(upper, upper)


def _infer_available_sims(docstring: str) -> List[str]:
    """Infer which simulators provide this field from the docstring."""
    if _SIMS_ALL_PATTERN.search(docstring):
        return ["DCS", "MSFS", "IL2", "BMS", "XPLANE"]

    positive: Set[str] = set()
    for m in _SIM_POSITIVE.finditer(docstring):
        positive.add(_normalize_sim(m.group(1)))

    negative: Set[str] = set()
    for m in _SIM_NEGATIVE.finditer(docstring):
        negative.add(_normalize_sim(m.group(1)))

    return sorted(positive - negative)


def _infer_units(docstring: str) -> Optional[str]:
    """Try to extract units from docstring patterns like '— m/s' or '(degrees)'."""
    # Pattern: "— unit" or "- unit" at end of a line/sentence
    m = re.search(r"[—–-]\s*(\S+(?:\s*\S+)?)\s*[.\n]", docstring)
    if m:
        candidate = m.group(1).rstrip(".")
        if candidate.lower() in (
            "m/s", "m", "degrees", "deg", "pa", "kg/m³", "knots", "kt",
            "g", "percent", "%", "psi", "ft/s²", "m/s²", "hz", "rad/s",
            "rad/s²", "bool",
        ):
            return candidate
    # Pattern: "(unit)" parenthetical
    m = re.search(r"\((\w+(?:/\w+)?)\)", docstring)
    if m:
        candidate = m.group(1)
        if candidate.lower() in ("m/s", "degrees", "meters", "pa", "psi", "knots"):
            return candidate
    return None


def extract_signal_registry(source_path: Optional[str] = None) -> Dict[str, SignalInfo]:
    """Parse BaseTelemetryData.py and extract signal metadata.

    Args:
        source_path: Path to BaseTelemetryData.py. If None, auto-detects
                     from the installed package location.

    Returns:
        Dict mapping field name to SignalInfo.
    """
    if source_path is None:
        from telemffb.sim import BaseTelemetryData as _mod
        source_path = inspect.getfile(_mod)

    source = Path(source_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    registry: Dict[str, SignalInfo] = {}
    current_category: Optional[str] = None

    # Find the class body
    class_body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BaseTelemetryData":
            class_body = node.body
            break

    if class_body is None:
        return registry

    for i, node in enumerate(class_body):
        # Track category from comments (represented as string expressions or
        # we parse from source lines)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            val = node.value.value
            if isinstance(val, str) and val.strip().startswith("--"):
                # Skip standalone docstrings that are category markers
                pass

        # Look for annotated assignments: `name: Type`
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_name = node.target.id
            if field_name.startswith("_"):
                continue

            # Get type annotation as source text
            type_str = ast.get_source_segment(source, node.annotation) or "Any"

            # Check if next node is a docstring (Expr(Constant(str)))
            docstring = ""
            if i + 1 < len(class_body):
                next_node = class_body[i + 1]
                if (isinstance(next_node, ast.Expr)
                        and isinstance(next_node.value, ast.Constant)
                        and isinstance(next_node.value.value, str)):
                    docstring = textwrap.dedent(next_node.value.value).strip()

            info = SignalInfo(
                name=field_name,
                type_annotation=type_str,
                docstring=docstring,
                available_sims=_infer_available_sims(docstring) if docstring else [],
                units=_infer_units(docstring) if docstring else None,
            )
            registry[field_name] = info

    # Infer categories from source comments (# -- Category --)
    _assign_categories(source, registry)

    return registry


def _assign_categories(source: str, registry: Dict[str, SignalInfo]) -> None:
    """Assign categories based on section comment headers in the source."""
    category_pattern = re.compile(r"#\s*--\s*(.+?)\s*--")
    lines = source.split("\n")

    current_category = None
    field_order: List[str] = []

    for line in lines:
        m = category_pattern.search(line)
        if m:
            current_category = m.group(1).strip()
            continue

        # Check if this line contains a field annotation
        for field_name in registry:
            if re.match(rf"\s+{re.escape(field_name)}\s*:", line):
                if current_category and field_name not in field_order:
                    registry[field_name].category = current_category
                    field_order.append(field_name)
                break


# Singleton cache
_registry_cache: Optional[Dict[str, SignalInfo]] = None


def get_signal_registry() -> Dict[str, SignalInfo]:
    """Get the cached signal registry, building it on first access."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = extract_signal_registry()
    return _registry_cache


def explain_signal(signal_name: str) -> Optional[dict]:
    """Return MCP-formatted metadata for a signal.

    Returns None if the signal is not found in the registry.
    """
    registry = get_signal_registry()
    info = registry.get(signal_name)
    if info is None:
        return None

    return {
        "signal": info.name,
        "type": info.type_annotation,
        "description": info.docstring.split("\n")[0] if info.docstring else "",
        "docstring": info.docstring,
        "available_sims": info.available_sims,
        "units": info.units,
        "category": info.category,
    }


def list_all_signals() -> List[dict]:
    """Return a summary list of all known signals."""
    registry = get_signal_registry()
    return [
        {
            "name": info.name,
            "type": info.type_annotation,
            "category": info.category,
            "available_sims": info.available_sims,
        }
        for info in registry.values()
    ]
