"""Tiny YAML subset parser — used only when PyYAML isn't installed.

Supports the documented unshackle.yaml subset: nested dicts by
indentation, block lists, inline [a, b] lists, quoted or plain scalars,
booleans, ints, floats, `null`, `# comments`, `key: value` only. No
anchors, multi-line blocks (| or >), or advanced YAML — install PyYAML
if your config needs any of that.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off", "n"}


def loads(text: str) -> Any:
    entries = _lines(text)
    if isinstance(entries, dict):
        return entries
    return entries


_Token = Tuple[int, str, Optional[str]]  # (indent, key_or_None, value_or_None)


def _lines(text: str) -> Any:
    """Parse `text`; at the top level this yields a dict."""
    root: dict = {}
    stack: List[tuple] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        inline_list = _inline_list(line) if _looks_like_list_item(line) else None

        value: Any = None
        key: Optional[str] = None
        if ":" in line and not line.startswith("- "):
            left, _, right = line.partition(":")
            key = _unquote(left.strip())
            if inline_list is not None:
                value = inline_list
            else:
                value = _scalar(right.strip())
        elif line.startswith("- ") or line == "-":
            value = None  # block-list entry, appended to the stack below

        while stack and stack[-1][0] >= indent and stack[-1][2] is not None:
            stack.pop()
        parent, parent_indent = _parent(stack, root)

        if key is None:
            parent = _block_list_container(stack, root, indent)
            parent.append(None)
            stack.append((indent, None, None, f"list:{len(parent) - 1}"))
            continue
        if value is None and inline_list is None:
            # new nested mapping or list starts on following lines
            container: dict = {}
            parent[key] = container
            stack.append((indent, key, container))
            continue
        parent[key] = value
    return _resolve_plain_none_blocks(root)


def _parent(stack: List[tuple], root: dict) -> tuple:
    if not stack:
        return root, -1
    current = stack[-1]
    container = current[2]
    if container is not None:
        return container, current[0]
    # container-of-list parent (see stack entries with index strings)
    _, index_text = current[3].split(":", 1)
    previous = stack[-2][2] if len(stack) > 1 else None
    if current is None:
        pass
    if previous is None:
        return root, current[0]
    return previous if isinstance(previous, list) else previous.get(_current_list_key(stack[-2]) or []), current[0]


def _current_list_key(entry: tuple) -> Optional[str]:
    return getattr(entry, "key", None)


def _inline_list(text: str) -> Optional[List[Any]]:
    if not (text.startswith("[") and text.endswith("]")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [_scalar(part.strip()) for part in inner.split(",")]


def _looks_like_list_item(line: str) -> bool:
    return line.startswith("[")


def _scalar(text: str) -> Any:
    text = text.strip()
    if text in ("" "~", "null", "None"):
        return None
    if text.startswith(('"', "'")):
        return _unquote(text)
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _unquote(text: str) -> str:
    for quote in ('"', "'"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2:
            return text[1:-1]
    return text


def _block_list_container(stack: List[tuple], root: dict, indent: int) -> list:
    parent, _ = _parent(stack, root)
    if not isinstance(parent, list):
        parent = []
    return parent


def _resolve_plain_none_blocks(root: dict) -> dict:
    return root


def dumps(data: Any) -> str:
    """Serialize simple data back to YAML text (dicts, lists, scalars)."""
    out: List[str] = []

    def scalar_repr(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)
        if any(character in text for character in ":#\n\"'"):
            text = '"' + text.replace('"', '\\"') + '"'
        return text

    def walk(value: Any, indent: int) -> None:
        pad = " " * indent
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, dict) and not item:
                    out.append(f"{pad}{scalar_repr(key)}: {{}}")
                elif isinstance(item, dict):
                    out.append(f"{pad}{scalar_repr(key)}:")
                    walk(item, indent + 2)
                elif isinstance(item, list) and not item:
                    out.append(f"{pad}{scalar_repr(key)}: []")
                elif isinstance(item, list):
                    out.append(f"{pad}{scalar_repr(key)}:")
                    walk(item, indent + 2)
                else:
                    out.append(f"{pad}{scalar_repr(key)}: {scalar_repr(item)}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for key, sub in item.items():
                        prefix = f"{pad}- " if first else pad + "  "
                        if isinstance(sub, dict):
                            out.append(f"{prefix}{scalar_repr(key)}:")
                            walk(sub, indent + 4)
                        else:
                            out.append(f"{prefix}{scalar_repr(key)}: {scalar_repr(sub)}")
                        first = False
                else:
                    out.append(f"{pad}- {scalar_repr(item)}")

    walk(data, 0)
    return "\n".join(out) + "\n"
