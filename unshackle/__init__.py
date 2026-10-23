"""Compatibility shim: `unshackle.*` resolves to `ulm.*`.

Old (and third-party) service modules do `from unshackle.core.service
import Service` etc. This package aliases every `ulm` module so those
imports keep working with zero changes. It is a shell, not a code
mirror: there is exactly one implementation (in `ulm`).
"""
from __future__ import annotations

import importlib
import sys

try:
    _PARENT = importlib.import_module("ulm")
except ImportError as error:  # pragma: no cover
    raise ImportError(
        "Install the 'ulm' package; this 'unshackle' shim only aliases it."
    ) from error

sys.modules["unshackle"] = _PARENT
# Expose `ulm` submodules under the `unshackle.` prefix as they load.
import importlib.machinery  # noqa: E402
import importlib.abc  # noqa: E402
import importlib.util  # noqa: E402
from importlib.machinery import PathFinder  # noqa: E402

_SHIM_PREFIX = "unshackle."


class _AliasLoader(importlib.abc.Loader):
    """Module finder that maps `unshackle.X` to `ulm.X` by name."""

    def find_spec(self, fullname: str, path=None, target=None):  # type: ignore[override]
        if not fullname.startswith(_SHIM_PREFIX):
            return None
        real_name = "ulm." + fullname[len(_SHIM_PREFIX):]
        spec = PathFinder.find_spec(real_name, None)
        if spec is None:
            return None
        return spec

    def find_module(self, fullname, path=None):  # noqa: N802 (legacy name)
        return self.find_spec(fullname, path)


sys.meta_path.insert(0, _AliasLoader())

__all__ = ["__version__", "__code_hash__"]
__version__ = getattr(_PARENT, "__version__", "?")
__code_hash__ = getattr(_PARENT, "__code_hash__", "")
