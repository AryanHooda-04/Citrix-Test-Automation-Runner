from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable


@dataclass(frozen=True)
class TestCase:
    id: str
    name: str
    description: str
    source_path: Path
    run: Callable
    evidence_name: str | None = None
    capture_screenshot: bool = True


def discover_test_cases(test_cases_dir: Path) -> list[TestCase]:
    test_cases = []
    for path in sorted(test_cases_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = _load_module(path)
        metadata = getattr(module, "TEST_CASE", None)
        run = getattr(module, "run", None)
        if not isinstance(metadata, dict) or not callable(run):
            continue
        if metadata.get("show_in_gui", True) is False:
            continue
        test_cases.append(
            TestCase(
                id=str(metadata.get("id", path.stem)),
                name=str(metadata.get("name", path.stem.replace("_", " ").title())),
                description=str(metadata.get("description", "")),
                source_path=path,
                run=run,
                evidence_name=metadata.get("evidence_name"),
                capture_screenshot=bool(metadata.get("capture_screenshot", True)),
            )
        )
    return test_cases


def _load_module(path: Path) -> ModuleType:
    module_name = f"citrix_test_case_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load test case module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
