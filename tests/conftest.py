"""Test bootstrap.

`custom_components/translink/__init__.py` imports Home Assistant,
which is not installed on a dev machine. The client and const modules deliberately have
no HA imports, so load them directly under a synthetic package name and let
the tests use them without a Home Assistant install.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / "translink"

if "tl" not in sys.modules:
    package = types.ModuleType("tl")
    package.__path__ = [str(COMPONENT)]  # type: ignore[attr-defined]
    sys.modules["tl"] = package

    for module_name in ("const", "translink_client"):
        full = f"tl.{module_name}"
        spec = importlib.util.spec_from_file_location(full, COMPONENT / f"{module_name}.py")
        assert spec and spec.loader, f"could not load {module_name}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
