from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass
class ExecutionLog:
    test_case_name: str
    logs_dir: Path
    desktop_name: str | None = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    status: str = "Running"
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, str] | None = None
    screenshot: str | None = None
    evidence_screenshots: list[str] = field(default_factory=list)
    log_path: Path | None = None

    def add_step(self, message: str, level: str = "INFO") -> None:
        self.steps.append(
            {
                "timestamp": now_iso(),
                "level": level,
                "message": message,
            }
        )

    def set_error(self, error: BaseException) -> None:
        self.error = {
            "type": error.__class__.__name__,
            "message": str(error),
        }
        self.add_step(f"{error.__class__.__name__}: {error}", "ERROR")

    def finish(
        self,
        status: str,
        screenshot: Path | None = None,
        evidence_screenshots: list[Path] | None = None,
    ) -> Path:
        self.status = status
        self.end_time = datetime.now()
        if screenshot:
            self.screenshot = str(screenshot)
        if evidence_screenshots is not None:
            self.evidence_screenshots = [str(path) for path in evidence_screenshots]
        self.log_path = self._write()
        return self.log_path

    def _write(self) -> Path:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        safe_name = safe_filename(self.test_case_name)
        path = self.logs_dir / f"{safe_name}_{timestamp}.json"
        with path.open("w", encoding="utf-8") as file:
            json.dump(self.to_dict(), file, indent=2)
        return path

    def to_dict(self) -> dict[str, Any]:
        end_time = self.end_time or datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        return {
            "test_case": self.test_case_name,
            "desktop_name": self.desktop_name,
            "status": self.status,
            "start_time": self.start_time.replace(microsecond=0).isoformat(),
            "end_time": end_time.replace(microsecond=0).isoformat(),
            "duration_seconds": round(duration, 3),
            "steps": self.steps,
            "error": self.error,
            "screenshot": self.screenshot,
            "evidence_screenshots": self.evidence_screenshots,
        }


def safe_filename(value: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum():
            cleaned.append(char)
        elif char == "-":
            cleaned.append(char)
        elif char in (" ", "_"):
            cleaned.append("_")
    name = "".join(cleaned).strip("_")
    return name or "TestCase"


def desktop_scoped_path(base_path: Path, desktop_name: str | None) -> Path:
    safe_desktop_name = safe_folder_name(desktop_name or "Unknown Desktop")
    return base_path.parent / safe_desktop_name / base_path.name


def safe_folder_name(value: str) -> str:
    invalid_chars = set('<>:"/\\|?*')
    cleaned = []
    for char in value.strip():
        if char in invalid_chars or ord(char) < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)
    name = "".join(cleaned).strip(" .")
    return name or "Unknown Desktop"
