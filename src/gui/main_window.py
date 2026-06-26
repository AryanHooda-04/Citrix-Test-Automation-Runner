from __future__ import annotations

import os
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import customtkinter as ctk
import tkinter as tk
from datetime import datetime
from pathlib import Path
from threading import Event
from tkinter import filedialog, messagebox
from PIL import Image

from core.automation_context import AutomationContext
from core.config import AppConfig, load_config
from core.desktop_history import DesktopNameHistory
from core.evidence_audit import EvidenceAuditResult, audit_evidence_folder
from core.execution_log import desktop_scoped_path
from core.master_runner import (
    CompleteExecutionResult,
    CompleteTestingRunner,
    MasterExecutionResult,
    MasterRunner,
    ShakedownRunner,
)
from core.openai_settings import (
    clear_saved_openai_api_key,
    get_openai_key_status,
    save_openai_api_key,
    test_openai_api_key,
)
from core.preflight import PreflightResult, run_preflight_checks
from core.skip_control import CombinedStopSkipEvent, consume_skip_request
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused
from core.runner import EDGE_BROWSER_TEST_NAME, EDGE_WEBVIEW_TEST_NAME, ExecutionResult, TestRunner
from core.run_manifest import MANIFEST_FILENAME, TESTCASE_SCREENSHOT_PREFIXES, build_run_manifest
from core.support_bundle import create_support_bundle
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    IAT_TEST_CASE_ORDER,
    MANDATORY_EVIDENCE_FOLDER,
    MANDATORY_TEST_CASE_ORDER,
    POST_COMPLETE_ZSCALER_TEST_NAME,
    SHAKEDOWN_EVIDENCE_FOLDER,
    SHAKEDOWN_TEST_CASE_ORDER,
    SILO43_EVIDENCE_FOLDER,
    SILO43_TEST_CASE_ORDER,
    evidence_category_for_test_name,
    is_silo43_desktop,
    is_success_status,
    mandatory_order_for_desktop,
)
from core.test_loader import TestCase, discover_test_cases
from core.word_report import REPORT_STRUCTURE, generate_complete_testing_report, generate_report_from_screenshots


LIGHT_THEME = {
    "bg": "#f5f7fb",
    "bg_bottom": "#eaf0f7",
    "card": "#ffffff",
    "card_soft": "#f7f9fc",
    "card_hover": "#eef6ff",
    "card_running": "#e8f3ff",
    "card_running_glow": "#9ec9ff",
    "border": "#dbe3ee",
    "border_focus": "#2f80ed",
    "divider": "#d8e1ed",
    "text": "#0f1f33",
    "muted": "#657386",
    "primary": "#0f6cbd",
    "primary_hover": "#0b5cab",
    "primary_pressed": "#084a8f",
    "teal": "#0f766e",
    "danger": "#dc2626",
    "danger_soft": "#fff1f2",
    "danger_hover": "#fee2e2",
    "disabled": "#d7dee8",
    "disabled_text": "#8a95a6",
    "console": "#172033",
    "console_text": "#e7eefb",
    "console_muted": "#94a3b8",
    "console_warning": "#fde68a",
    "console_error": "#fca5a5",
    "input": "#ffffff",
    "input_disabled": "#eef3f8",
    "header_top": "#0f6cbd",
    "header_bottom": "#084c8f",
    "header_subtitle": "#dbeafe",
    "header_icon": "#e0f2fe",
    "header_icon_text": "#0f6cbd",
    "scrollbar": "#cbd5e1",
}

DARK_THEME = {
    "bg": "#0f1724",
    "bg_bottom": "#0b1220",
    "card": "#172033",
    "card_soft": "#1d293d",
    "card_hover": "#263449",
    "card_running": "#173256",
    "card_running_glow": "#38bdf8",
    "border": "#2a384d",
    "border_focus": "#38bdf8",
    "divider": "#26364f",
    "text": "#e5eefc",
    "muted": "#94a3b8",
    "primary": "#0b65b9",
    "primary_hover": "#0a5ca8",
    "primary_pressed": "#084c8e",
    "teal": "#2dd4bf",
    "danger": "#fb7185",
    "danger_soft": "#3b1f2a",
    "danger_hover": "#4c2631",
    "disabled": "#334155",
    "disabled_text": "#8b9aad",
    "console": "#101827",
    "console_text": "#dbe7f8",
    "console_muted": "#7f8da3",
    "console_warning": "#fde68a",
    "console_error": "#fda4af",
    "input": "#0f172a",
    "input_disabled": "#172033",
    "header_top": "#0f766e",
    "header_bottom": "#164e63",
    "header_subtitle": "#ccfbf1",
    "header_icon": "#ccfbf1",
    "header_icon_text": "#0f766e",
    "scrollbar": "#475569",
}

LIGHT_STATUS_BADGES = {
    "Idle": ("#eef2f7", "#475569"),
    "Running": ("#dbeafe", "#1d4ed8"),
    "Pass": ("#dcfce7", "#166534"),
    "Fail": ("#fee2e2", "#991b1b"),
    "Skipped": ("#fef3c7", "#92400e"),
    "Stopped": ("#fef3c7", "#92400e"),
    "Paused": ("#ede9fe", "#6d28d9"),
}

DARK_STATUS_BADGES = {
    "Idle": ("#263244", "#cbd5e1"),
    "Running": ("#173256", "#7dd3fc"),
    "Pass": ("#123524", "#86efac"),
    "Fail": ("#421b26", "#fca5a5"),
    "Skipped": ("#3d3014", "#fcd34d"),
    "Stopped": ("#3d3014", "#fcd34d"),
    "Paused": ("#34235a", "#c4b5fd"),
}


def _button_variants(theme: dict[str, str]) -> dict[str, dict[str, str | None]]:
    return {
        "primary": {
            "fill": theme["primary"],
            "hover": theme["primary_hover"],
            "pressed": theme["primary_pressed"],
            "text": "#ffffff",
            "disabled": theme["disabled"],
            "disabled_text": theme["disabled_text"],
            "outline": None,
        },
        "secondary": {
            "fill": theme["card_soft"],
            "hover": theme["card_running"],
            "pressed": theme["border"],
            "text": theme["primary"],
            "disabled": theme["disabled"],
            "disabled_text": theme["disabled_text"],
            "outline": theme["border"],
        },
        "danger": {
            "fill": theme["danger_soft"],
            "hover": theme["danger_hover"],
            "pressed": theme["danger_hover"],
            "text": theme["danger"],
            "disabled": theme["disabled"],
            "disabled_text": theme["disabled_text"],
            "outline": theme["danger_hover"],
        },
        "ghost": {
            "fill": theme["card_soft"],
            "hover": theme["card_running"],
            "pressed": theme["border"],
            "text": theme["text"],
            "disabled": theme["disabled"],
            "disabled_text": theme["disabled_text"],
            "outline": theme["border"],
        },
    }


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

THEME = LIGHT_THEME
STATUS_BADGES = LIGHT_STATUS_BADGES
BUTTON_VARIANTS = _button_variants(THEME)
DESKTOP_VIEWER_SUFFIX = " - Desktop Viewer"
KNOWN_DESKTOP_NAMES = [
    "SIL007-RING0-AP1",
    "SILO01-RING0-CE1",
    "SILO01-TEST",
    "SILO01-TEST-CE2",
    "SILO05-RING0-Int",
    "SILO05-TEST-Internal",
    "SILO07-RING0-AP2",
    "SILO07-TEST-AP1",
    "SILO07-TEST-AP2",
    "SILO18-RING0",
    "SILO18-RING0-Box1",
    "SILO18-RING0-Box2",
    "SILO18-RING0-Box3",
    "SILO18-TEST",
    "SILO18-TEST-BOX1",
    "SILO18-TEST-Box3",
    "SILO19-TEST",
    "SILO21-RING0",
    "SILO21-TEST",
    "SILO25-RING0",
    "SILO25-TEST",
    "SILO26-RING0",
    "SILO26-TEST",
    "SILO27-RING0",
    "SILO27-TEST",
    "SILO41-RING0-NA1",
    "SILO41-TEST-NA1",
    "SILO43-RING0",
    "SILO43-TEST",
    "SILO45-RING0",
    "SILO45-TEST",
    "SILO52-RING0",
    "SILO52-TEST",
]


def _activate_theme(theme_name: str) -> None:
    global THEME, STATUS_BADGES, BUTTON_VARIANTS
    if theme_name == "dark":
        THEME = DARK_THEME
        STATUS_BADGES = DARK_STATUS_BADGES
    else:
        THEME = LIGHT_THEME
        STATUS_BADGES = LIGHT_STATUS_BADGES
    BUTTON_VARIANTS = _button_variants(THEME)


class ModernButton(ctk.CTkButton):
    def __init__(
        self,
        master,
        text: str,
        command=None,
        variant: str = "secondary",
        height: int = 30,
        min_width: int = 78,
        radius: int = 7,
        font: tuple[str, int, str] = ("Segoe UI", 11, "bold"),
        **kwargs,
    ) -> None:
        self.variant = variant
        kwargs.pop("bg", None)
        palette = BUTTON_VARIANTS[variant]
        super().__init__(
            master,
            text=text,
            command=command,
            height=height,
            width=min_width,
            corner_radius=radius,
            fg_color=palette["fill"],
            hover_color=palette["hover"],
            text_color=palette["text"],
            border_width=1 if palette["outline"] else 0,
            border_color=palette["outline"] or palette["fill"],
            font=font,
            **kwargs,
        )

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        options = {}
        if cnf:
            options.update(cnf)
        options.update(kwargs)
        options.pop("bg", None)
        if "variant" in options:
            self.variant = options.pop("variant")
            palette = BUTTON_VARIANTS[self.variant]
            options.update(
                {
                    "fg_color": palette["fill"],
                    "hover_color": palette["hover"],
                    "text_color": palette["text"],
                    "border_width": 1 if palette["outline"] else 0,
                    "border_color": palette["outline"] or palette["fill"],
                }
            )
        if "state" in options:
            palette = BUTTON_VARIANTS[self.variant]
            if options["state"] == tk.DISABLED:
                quiet_fill = THEME["input_disabled"]
                options.setdefault("fg_color", quiet_fill)
                options.setdefault("hover_color", quiet_fill)
                options.setdefault("text_color", palette["disabled_text"])
                options.setdefault("border_color", THEME["border"])
            else:
                options.setdefault("fg_color", palette["fill"])
                options.setdefault("hover_color", palette["hover"])
                options.setdefault("text_color", palette["text"])
                options.setdefault("border_width", 1 if palette["outline"] else 0)
                options.setdefault("border_color", palette["outline"] or palette["fill"])
        return super().configure(**options)

    config = configure


class StatusBadge(ctk.CTkLabel):
    DISPLAY_TEXT = {
        "Pass": "Passed",
        "Fail": "Failed",
    }
    def __init__(self, master, text: str = "Idle", width: int = 68, height: int = 22, **kwargs) -> None:
        kwargs.pop("bg", None)
        fill, text_color = STATUS_BADGES.get(text, STATUS_BADGES["Idle"])
        super().__init__(
            master,
            width=width,
            height=height,
            text=self.DISPLAY_TEXT.get(text, text),
            fg_color=fill,
            text_color=text_color,
            corner_radius=999,
            font=("Segoe UI", 9, "bold"),
            **kwargs,
        )
        self.text = text

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        options = {}
        if cnf:
            options.update(cnf)
        options.update(kwargs)
        options.pop("bg", None)
        options.pop("foreground", None)
        options.pop("fg", None)
        if "text" in options:
            self.text = options.pop("text")
            fill, text_color = STATUS_BADGES.get(self.text, STATUS_BADGES["Idle"])
            options.setdefault("text", self.DISPLAY_TEXT.get(self.text, self.text))
            options.setdefault("fg_color", fill)
            options.setdefault("text_color", text_color)
        return super().configure(**options)

    config = configure


class HeaderLogo(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            width=34,
            height=34,
            corner_radius=12,
            fg_color=THEME["header_icon"],
            **kwargs,
        )
        self.pack_propagate(False)
        self.canvas = tk.Canvas(
            self,
            width=26,
            height=26,
            bg=THEME["header_icon"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(expand=True)
        self._draw_mark()

    def _draw_mark(self) -> None:
        self.canvas.delete("all")
        primary = THEME["header_icon_text"]
        accent = THEME["primary"]
        self.canvas.create_line(
            6,
            17,
            13,
            9,
            21,
            15,
            fill=primary,
            width=3,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
        )
        self.canvas.create_oval(3, 14, 9, 20, fill=primary, outline=primary)
        self.canvas.create_oval(10, 6, 16, 12, fill=THEME["header_icon"], outline=primary, width=2)
        self.canvas.create_oval(18, 12, 24, 18, fill=primary, outline=primary)
        self.canvas.create_line(9, 20, 13, 23, 22, 8, fill=accent, width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)


class ProgressRingPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, height=286, fg_color=THEME["card_soft"], corner_radius=14, **kwargs)
        self.completed = 0
        self.total = 0
        self.status = "Idle"
        self._layout_mode: str | None = None
        self._chart_size = 108
        self._chart_redraw_job: str | None = None
        self._metric_tiles: dict[str, ctk.CTkFrame] = {}
        self._metric_title_labels: dict[str, ctk.CTkLabel] = {}
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)

        self.summary_frame = ctk.CTkFrame(self, height=118, fg_color="transparent")
        self.summary_frame.grid_columnconfigure(1, weight=1)
        self.summary_frame.grid_propagate(False)

        self.chart_holder = ctk.CTkFrame(
            self.summary_frame,
            width=self._chart_size,
            height=self._chart_size,
            fg_color="transparent",
        )
        self.chart_holder.grid(row=0, column=0, sticky="nw")
        self.chart_holder.grid_propagate(False)
        self.chart_holder.pack_propagate(False)

        self.chart_canvas = tk.Canvas(
            self.chart_holder,
            width=self._chart_size,
            height=self._chart_size,
            bg=THEME["card_soft"],
            highlightthickness=0,
            bd=0,
        )
        self.chart_canvas.pack(fill=tk.BOTH, expand=True)

        self.info_frame = ctk.CTkFrame(self.summary_frame, fg_color="transparent")
        self.info_frame.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

        self.title_label = ctk.CTkLabel(
            self.info_frame,
            text="Run Progress",
            text_color=THEME["text"],
            font=("Segoe UI", 14, "bold"),
        )
        self.title_label.pack(anchor=tk.W, pady=(2, 0))

        self.scope_label = ctk.CTkLabel(
            self.info_frame,
            text="Ready",
            text_color=THEME["muted"],
            font=("Segoe UI", 11, "bold"),
            anchor=tk.W,
        )
        self.scope_label.pack(anchor=tk.W, fill=tk.X, pady=(9, 0))

        self.status_row = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.status_row.pack(anchor=tk.W, pady=(9, 0))
        self.status_badge = StatusBadge(self.status_row, text="Idle")
        self.status_badge.pack(side=tk.LEFT)
        self.count_label = ctk.CTkLabel(
            self.status_row,
            text="0 of 0 completed",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        self.count_label.pack(side=tk.LEFT, padx=(10, 0))

        self.details_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.elapsed_value = self._make_metric("Elapsed", "00:00")
        self.remaining_value = self._make_metric("Remaining", "0")
        self.current_value = self._make_metric("Current", "None")
        self.next_value = self._make_metric("Next", "None")
        self.bind("<Configure>", self._on_resize, add="+")
        self._apply_layout(expanded=False, force=True)
        self.set_progress("Ready", 0, 0, "Idle")

    def _make_metric(self, label: str, value: str) -> ctk.CTkLabel:
        tile = ctk.CTkFrame(self.details_frame, height=58, fg_color=THEME["card"], corner_radius=10)
        tile.grid_columnconfigure(0, weight=1)
        tile.grid_propagate(False)
        label_widget = ctk.CTkLabel(
            tile,
            text=label,
            text_color=THEME["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor=tk.W,
        )
        label_widget.grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        value_label = ctk.CTkLabel(
            tile,
            text=value,
            text_color=THEME["text"],
            font=("Segoe UI", 10, "bold"),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=150,
        )
        value_label.grid(row=1, column=0, sticky="ew", padx=10, pady=(1, 7))
        self._metric_tiles[label] = tile
        self._metric_title_labels[label] = label_widget
        return value_label

    def refresh_theme(self) -> None:
        self.configure(fg_color=THEME["card_soft"])
        for frame in (
            self.summary_frame,
            self.chart_holder,
            self.info_frame,
            self.status_row,
            self.details_frame,
        ):
            frame.configure(fg_color="transparent")
        self.chart_canvas.configure(bg=THEME["card_soft"])
        self.title_label.configure(text_color=THEME["text"], fg_color="transparent")
        self.scope_label.configure(text_color=THEME["muted"], fg_color="transparent")
        self.count_label.configure(text_color=THEME["muted"], fg_color="transparent")
        for tile in self._metric_tiles.values():
            tile.configure(fg_color=THEME["card"])
        for label in self._metric_title_labels.values():
            label.configure(text_color=THEME["muted"], fg_color="transparent")
        for value_label in (self.elapsed_value, self.remaining_value, self.current_value, self.next_value):
            value_label.configure(text_color=THEME["text"], fg_color="transparent")
        self.status_badge.configure(text=self.status)
        self._draw_chart()

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is self:
            self._schedule_chart_redraw()

    def set_panel_width(self, width: int, force: bool = False) -> None:
        self._apply_layout(expanded=width >= 560, force=force)

    def settle_layout(self, width: int) -> None:
        self.set_panel_width(width, force=True)
        self._schedule_chart_redraw()

    def _schedule_chart_redraw(self) -> None:
        if self._chart_redraw_job is not None:
            try:
                self.after_cancel(self._chart_redraw_job)
            except tk.TclError:
                pass
        self._chart_redraw_job = self.after(80, self._redraw_after_resize)

    def _redraw_after_resize(self) -> None:
        self._chart_redraw_job = None
        self._draw_chart()

    def _apply_layout(self, expanded: bool, force: bool = False) -> None:
        mode = "wide" if expanded else "compact"
        if self._layout_mode == mode and not force:
            return
        self._layout_mode = mode
        for column in range(2):
            self.grid_columnconfigure(column, weight=0, uniform="")
        for row in range(2):
            self.grid_rowconfigure(row, weight=0)
        for column in range(2):
            self.details_frame.grid_columnconfigure(column, weight=1, uniform="progress_metric")
        for row in range(2):
            self.details_frame.grid_rowconfigure(row, weight=1, uniform="progress_metric")
        self.summary_frame.grid_forget()
        self.details_frame.grid_forget()

        if expanded:
            self.configure(height=236)
            self.grid_columnconfigure(0, weight=5, uniform="progress_panel")
            self.grid_columnconfigure(1, weight=6, uniform="progress_panel")
            self.grid_rowconfigure(0, weight=1)
            self.summary_frame.configure(height=208)
            self.summary_frame.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=14)
            self.details_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=14)
            chart_size = 124
            tile_height = 70
            wrap_length = 170
        else:
            self.configure(height=286)
            self.grid_columnconfigure(0, weight=1)
            self.summary_frame.configure(height=112)
            self.summary_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
            self.details_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
            chart_size = 104
            tile_height = 58
            wrap_length = 145

        self._chart_size = chart_size
        self.chart_holder.configure(width=chart_size, height=chart_size)
        self.chart_canvas.configure(width=chart_size, height=chart_size)

        positions = {
            "Elapsed": (0, 0),
            "Remaining": (0, 1),
            "Current": (1, 0),
            "Next": (1, 1),
        }
        for label, tile in self._metric_tiles.items():
            row, column = positions[label]
            tile.grid_forget()
            tile.configure(height=tile_height)
            tile.grid(row=row, column=column, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0), pady=(0, 8))
        for value_label in (self.elapsed_value, self.remaining_value, self.current_value, self.next_value):
            value_label.configure(wraplength=wrap_length)
        self._draw_chart()

    def set_progress(
        self,
        title: str,
        completed: int,
        total: int,
        status: str,
        current: str = "None",
        next_item: str = "None",
        remaining: int = 0,
        elapsed_seconds: int = 0,
    ) -> None:
        self.completed = max(completed, 0)
        self.total = max(total, 0)
        self.status = status or "Idle"
        self.scope_label.configure(text=title)
        self.status_badge.configure(text=self.status)
        self.count_label.configure(text=f"{self.completed} of {self.total} completed")
        self.elapsed_value.configure(text=_format_elapsed(elapsed_seconds))
        self.current_value.configure(text=current)
        self.next_value.configure(text=next_item)
        self.remaining_value.configure(text=str(remaining))
        self._draw_chart()

    def _progress_color(self) -> str:
        if self.status == "Pass":
            return STATUS_BADGES["Pass"][1]
        if self.status == "Fail":
            return STATUS_BADGES["Fail"][1]
        if self.status in {"Skipped", "Stopped"}:
            return STATUS_BADGES["Skipped"][1]
        if self.status == "Paused":
            return STATUS_BADGES["Paused"][1]
        return THEME["primary"]

    def _draw_chart(self) -> None:
        self.chart_canvas.delete("all")
        self.chart_canvas.configure(bg=THEME["card_soft"])
        actual_size = min(self.chart_canvas.winfo_width(), self.chart_canvas.winfo_height())
        draw_size = actual_size if actual_size > 24 else self._chart_size

        ratio = min(self.completed / self.total, 1) if self.total else 0

        width = max(10, int(draw_size * 0.12))
        pad = max(width // 2 + 4, int(draw_size * 0.09))
        bounds = (pad, pad, draw_size - pad, draw_size - pad)
        self.chart_canvas.create_oval(
            *bounds,
            outline=THEME["border"],
            width=width,
        )
        if ratio >= 0.995:
            self.chart_canvas.create_oval(
                *bounds,
                outline=self._progress_color(),
                width=width,
            )
        elif ratio > 0:
            self.chart_canvas.create_arc(
                *bounds,
                start=90,
                extent=-359.9 * ratio,
                style=tk.ARC,
                outline=self._progress_color(),
                width=width,
            )
        percent_text = f"{round(ratio * 100):.0f}%"
        percent_font_size = max(18, int(draw_size * (0.15 if len(percent_text) >= 4 else 0.19)))
        self.chart_canvas.create_text(
            draw_size / 2,
            draw_size / 2,
            text=percent_text,
            fill=self._progress_color(),
            font=("Segoe UI", percent_font_size, "bold"),
        )


class AutomationApp(ctk.CTk):
    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.app_version = self._read_app_version()
        self.config: AppConfig = load_config(root_dir)
        self.runtime_mode_key = self.config.runtime_mode()
        self.runtime_mode_var = tk.StringVar(value=self.config.runtime_mode_label())
        self.desktop_history = DesktopNameHistory(self.config)
        self.test_cases: list[TestCase] = []
        self.status_labels: dict[str, StatusBadge] = {}
        self.run_buttons: dict[str, ModernButton] = {}
        self.stop_buttons: dict[str, ModernButton] = {}
        self.pause_buttons: dict[str, ModernButton] = {}
        self.skip_buttons: dict[str, ModernButton] = {}
        self.test_cards: dict[str, ctk.CTkFrame] = {}
        self.test_card_accents: dict[str, ctk.CTkFrame] = {}
        self.test_card_states: dict[str, str] = {}
        self.description_labels: dict[str, ctk.CTkLabel] = {}
        self.description_buttons: dict[str, ModernButton] = {}
        self.description_expanded: dict[str, bool] = {}
        self.section_frames: dict[str, ctk.CTkFrame] = {}
        self.section_containers: dict[str, ctk.CTkFrame] = {}
        self.section_buttons: dict[str, ModernButton] = {}
        self.section_selected_buttons: dict[str, ModernButton] = {}
        self.section_pause_buttons: dict[str, ModernButton] = {}
        self.section_stop_buttons: dict[str, ModernButton] = {}
        self.section_skip_buttons: dict[str, ModernButton] = {}
        self.section_selection_labels: dict[str, ctk.CTkLabel] = {}
        self.section_test_ids: dict[str, list[str]] = {}
        self.section_collapsed: dict[str, bool] = {}
        self.section_select_vars: dict[str, tk.BooleanVar] = {}
        self.section_select_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self.selection_vars: dict[str, tk.BooleanVar] = {}
        self.selection_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._syncing_section_selection = False
        self.global_selected_button: ModernButton | None = None
        self.global_rerun_failed_button: ModernButton | None = None
        self.global_selected_pause_button: ModernButton | None = None
        self.global_selected_stop_button: ModernButton | None = None
        self.global_selected_skip_button: ModernButton | None = None
        self.evidence_preview_button: ModernButton | None = None
        self.failed_recovery_button: ModernButton | None = None
        self.selected_progress_label: ctk.CTkLabel | None = None
        self.progress_panel: ProgressRingPanel | None = None
        self.events: queue.Queue = queue.Queue()
        self.desktop_name_var = tk.StringVar(value="")
        self.refresh_button: ModernButton | None = None
        self.theme_button: ModernButton | None = None
        self.ai_key_button: ModernButton | None = None
        self.complete_button: ModernButton | None = None
        self.complete_pause_button: ModernButton | None = None
        self.complete_stop_button: ModernButton | None = None
        self.complete_skip_button: ModernButton | None = None
        self.complete_status_label: StatusBadge | None = None
        self.complete_progress_label: ctk.CTkLabel | None = None
        self.complete_runtime_label: ctk.CTkLabel | None = None
        self.complete_card: ctk.CTkFrame | None = None
        self.dry_run_button: ModernButton | None = None
        self.latest_report_button: ModernButton | None = None
        self.build_doc_button: ModernButton | None = None
        self.schedule_complete_button: ModernButton | None = None
        self.schedule_status_card: ctk.CTkFrame | None = None
        self.schedule_status_title: ctk.CTkLabel | None = None
        self.schedule_status_counts: ctk.CTkLabel | None = None
        self.schedule_status_current: ctk.CTkLabel | None = None
        self.schedule_status_queue: ctk.CTkFrame | None = None
        self.evidence_root_label: ctk.CTkLabel | None = None
        self.evidence_root_button: ModernButton | None = None
        self.preflight_button: ModernButton | None = None
        self.evidence_audit_button: ModernButton | None = None
        self.support_bundle_button: ModernButton | None = None
        self.master_button: ModernButton | None = None
        self.master_pause_button: ModernButton | None = None
        self.master_stop_button: ModernButton | None = None
        self.master_skip_button: ModernButton | None = None
        self.master_status_label: StatusBadge | None = None
        self.master_progress_label: ctk.CTkLabel | None = None
        self.master_card: ctk.CTkFrame | None = None
        self.shakedown_button: ModernButton | None = None
        self.shakedown_pause_button: ModernButton | None = None
        self.shakedown_stop_button: ModernButton | None = None
        self.shakedown_skip_button: ModernButton | None = None
        self.shakedown_status_label: StatusBadge | None = None
        self.shakedown_progress_label: ctk.CTkLabel | None = None
        self.shakedown_card: ctk.CTkFrame | None = None
        self.input_card: ctk.CTkFrame | None = None
        self.main_canvas: ctk.CTkScrollableFrame | None = None
        self.main_window: int | None = None
        self.test_cases_card: ctk.CTkFrame | None = None
        self.list_canvas: ctk.CTkFrame | None = None
        self.list_window: int | None = None
        self.desktop_shortcuts_frame: ctk.CTkFrame | None = None
        self.desktop_dropdown_button: ModernButton | None = None
        self.desktop_suggestion_values: list[str] = []
        self.desktop_suggestion_popup: tk.Toplevel | None = None
        self.desktop_suggestion_listbox: tk.Listbox | None = None
        self.desktop_suggestion_index = -1
        self._desktop_suggestion_scroll_job: str | None = None
        self.content_frame: ctk.CTkFrame | None = None
        self.runtime_mode_menu: ctk.CTkOptionMenu | None = None
        self.log_width_label: ctk.CTkLabel | None = None
        self.log_filter_button: ModernButton | None = None
        self.log_entries: list[str] = []
        self.max_log_lines = int(self.config.raw.get("ui", {}).get("max_log_lines", 2500))
        self.log_errors_only = False
        self.log_panel_width = 420
        self.log_splitter: tk.Frame | None = None
        self._log_resize_start_x = 0
        self._log_resize_start_width = self.log_panel_width
        self._test_cases_resize_job: str | None = None
        self.active_stop_event: Event | None = None
        self.active_pause_event: Event | None = None
        self.active_skip_event: Event | None = None
        self.active_paused = False
        self.active_test_id: str | None = None
        self.active_selected_section: str | None = None
        self.active_mode: str | None = None
        self.theme_name = "dark"
        self.master_completed_count = 0
        self.master_total_count = 0
        self.shakedown_completed_count = 0
        self.shakedown_total_count = 0
        self.complete_completed_count = 0
        self.complete_total_count = 0
        self.complete_started_monotonic: float | None = None
        self.complete_runtime_tick_active = False
        self.complete_current_phase = "Idle"
        self.complete_current_test = "None"
        self.selected_completed_count = 0
        self.selected_total_count = 0
        self.selected_section_title = ""
        self.active_sequence_ids: list[str] = []
        self.active_sequence_started_monotonic: float | None = None
        self.active_run_statuses: dict[str, str] = {}
        self.last_failed_test_case_ids: list[str] = []
        self.latest_report_path: Path | None = None
        self.report_refresh_in_progress = False
        self.report_refresh_pending_desktop_name: str | None = None
        self.latest_manifest_path: Path | None = None
        self.manifest_refresh_in_progress = False
        self.manifest_refresh_pending_desktop_name: str | None = None
        self.scheduled_desktops: list[str] = []
        self.scheduled_results: list[dict[str, object]] = []
        self.scheduled_index = 0
        self.scheduled_waiting_desktop: str | None = None
        self.silo18_win_space_done_desktops: set[str] = set()
        self._last_progress_monotonic = time.monotonic()
        self._watchdog_last_warning_monotonic = 0.0

        self.title("Citrix Test Automation Runner")
        self.geometry("1180x720")
        self.minsize(900, 560)
        _activate_theme(self.theme_name)
        self.configure(fg_color=THEME["bg"])

        self._configure_styles()
        self._build_layout()
        self.refresh_tests()
        self.after_idle(self._settle_progress_panel_layout)
        self.after(250, self._settle_progress_panel_layout)
        self.after(150, self._process_events)
        self.after(1000, self._watchdog_tick)

    def _read_app_version(self) -> str:
        version_path = self.root_dir / "version.txt"
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            version = ""
        return version or "dev"

    def _runtime_mode_labels(self) -> list[str]:
        profiles = self.config.runtime_profile.get("profiles", {})
        labels: list[str] = []
        for key in ("fast", "normal", "safe"):
            if key in profiles:
                labels.append(str(profiles[key].get("label", key.title())))
        for key, profile in profiles.items():
            label = str(profile.get("label", key.title()))
            if label not in labels:
                labels.append(label)
        return labels or ["Normal"]

    def _runtime_label_to_key(self, label: str) -> str:
        profiles = self.config.runtime_profile.get("profiles", {})
        for key, profile in profiles.items():
            if str(profile.get("label", key.title())) == label:
                return key
        return self.config.runtime_mode()

    def _apply_runtime_mode_to_config(self, mode_key: str | None = None) -> None:
        runtime_profile = self.config.raw.setdefault("runtime_profile", {})
        profiles = runtime_profile.get("profiles", {})
        selected_mode = mode_key or runtime_profile.get("mode", "normal")
        if selected_mode not in profiles:
            selected_mode = "normal" if "normal" in profiles else next(iter(profiles), "normal")
        runtime_profile["mode"] = selected_mode
        self.runtime_mode_key = selected_mode
        if hasattr(self, "runtime_mode_var"):
            self.runtime_mode_var.set(self.config.runtime_mode_label())

    def _set_runtime_mode_from_label(self, label: str) -> None:
        if self.active_stop_event is not None:
            self.runtime_mode_var.set(self.config.runtime_mode_label())
            return
        mode_key = self._runtime_label_to_key(label)
        self._apply_runtime_mode_to_config(mode_key)
        self._append_message(
            f"Runtime mode set to {self.config.runtime_mode_label()} "
            f"({self.config.runtime_wait_multiplier():.2f}x configurable waits)"
        )

    def _reset_dashboard_statuses(self) -> None:
        self.complete_completed_count = 0
        self.complete_total_count = 0
        self.complete_started_monotonic = None
        self.complete_current_phase = "Idle"
        self.complete_current_test = "None"
        self.master_completed_count = 0
        self.master_total_count = 0
        self.shakedown_completed_count = 0
        self.shakedown_total_count = 0
        self.selected_completed_count = 0
        self.selected_total_count = 0
        self.selected_section_title = ""

        self._set_complete_status("Idle")
        self._set_complete_progress("Ready")
        self._set_complete_runtime_summary()
        self._set_master_status("Idle")
        self._set_master_progress("Ready")
        self._set_shakedown_status("Idle")
        self._set_shakedown_progress("Ready")
        self._set_progress_panel_idle()

        for test_case_id in list(self.status_labels):
            self._set_status(test_case_id, "Idle")
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self._set_complete_skip_enabled(False)
        self._set_master_skip_enabled(False)
        self._set_shakedown_skip_enabled(False)
        self.active_pause_event = None
        self.active_skip_event = None
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = None
        self.active_sequence_ids = []
        self.active_sequence_started_monotonic = None
        self.active_run_statuses = {}
        self.last_failed_test_case_ids = []
        self.scheduled_desktops = []
        self.scheduled_results = []
        self.scheduled_index = 0
        self.scheduled_waiting_desktop = None
        self._hide_schedule_status_panel()
        self._update_selection_cues()
        self._set_buttons_enabled(True)
        self._set_rerun_failed_enabled(False)
        self._update_desktop_input_state()

    def _evidence_root_path(self) -> Path:
        return self.config.path("screenshots_dir").parent

    def _refresh_evidence_root_label(self) -> None:
        if self.evidence_root_label is None:
            return
        self.evidence_root_label.configure(text=self._short_path_text(self._evidence_root_path(), 84))

    def _validate_evidence_root_path(self, root: Path | None = None) -> dict[str, object]:
        evidence_root = Path(root or self._evidence_root_path()).expanduser()
        failures: list[str] = []
        warnings: list[str] = []
        checked_paths: list[str] = []
        start = time.monotonic()
        probe_name = f".evidence_write_probe_{int(start * 1000)}.tmp"

        try:
            evidence_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failures.append(f"Evidence root is unavailable: {exc}")
            return {
                "ok": False,
                "root": evidence_root,
                "failures": failures,
                "warnings": warnings,
                "latency_seconds": 0.0,
                "checked_paths": checked_paths,
            }

        for folder in (evidence_root, evidence_root / "screenshots", evidence_root / "logs"):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                probe = folder / probe_name
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                checked_paths.append(str(folder))
            except OSError as exc:
                failures.append(f"{folder} is not writable: {exc}")

        latency = time.monotonic() - start
        root_text = str(evidence_root).casefold()
        if "onedrive" in root_text and latency >= 2.0:
            warnings.append(
                f"Evidence root is under OneDrive and the write check took {latency:.1f}s. Sync latency may slow screenshot/log saving."
            )

        try:
            attrs = getattr(evidence_root.stat(), "st_file_attributes", 0)
        except OSError:
            attrs = 0
        if attrs & 0x1000:
            warnings.append("Evidence root appears to be offline/cloud-only. Keep it available on this device before running.")

        return {
            "ok": not failures,
            "root": evidence_root,
            "failures": failures,
            "warnings": warnings,
            "latency_seconds": latency,
            "checked_paths": checked_paths,
        }

    def _format_evidence_root_health(self, health: dict[str, object]) -> str:
        root = health.get("root")
        lines = [f"Evidence root:\n{root}"]
        failures = list(health.get("failures") or [])
        warnings = list(health.get("warnings") or [])
        if failures:
            lines.extend(["", "Blocking issue(s):"])
            lines.extend(f"- {message}" for message in failures)
        if warnings:
            lines.extend(["", "Warning(s):"])
            lines.extend(f"- {message}" for message in warnings)
        return "\n".join(lines)

    def _ensure_evidence_root_ready(self, title: str = "Evidence Root") -> bool:
        health = self._validate_evidence_root_path()
        if not bool(health.get("ok")):
            messagebox.showerror(f"{title} Not Ready", self._format_evidence_root_health(health))
            return False
        warnings = list(health.get("warnings") or [])
        for warning in warnings:
            self._append_message(f"Evidence root warning: {warning}")
        if warnings:
            messagebox.showwarning(f"{title} Warning", self._format_evidence_root_health(health))
        return True

    def choose_evidence_root(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before changing the evidence folder.")
            return

        selected = filedialog.askdirectory(
            title="Select Evidence Storage Folder",
            initialdir=str(self._evidence_root_path()),
            mustexist=False,
        )
        if not selected:
            return

        root = Path(selected).expanduser()
        health = self._validate_evidence_root_path(root)
        if not bool(health.get("ok")):
            messagebox.showerror("Evidence Folder Failed", self._format_evidence_root_health(health))
            return
        warnings = list(health.get("warnings") or [])
        if warnings and not messagebox.askyesno(
            "Evidence Folder Warning",
            self._format_evidence_root_health(health) + "\n\nUse this evidence folder anyway?",
        ):
            return

        self.config.raw.setdefault("paths", {})["screenshots_dir"] = str(root / "screenshots")
        self.config.raw.setdefault("paths", {})["logs_dir"] = str(root / "logs")
        try:
            self._save_app_config()
        except OSError as exc:
            messagebox.showerror("Config Save Failed", f"Evidence folder was updated for this session, but config could not be saved:\n\n{exc}")
        self._refresh_evidence_root_label()
        self._append_message(f"Evidence storage path updated: {root}")

    def _save_app_config(self) -> None:
        config_path = self.root_dir / "config" / "config.json"
        config_path.write_text(json.dumps(self.config.raw, indent=2) + "\n", encoding="utf-8")

    def open_evidence_folder(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showinfo("Citrix Desktop Name Required", "Enter or select a Citrix Desktop Name first.")
            return
        evidence_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name).parent
        if not evidence_root.exists():
            messagebox.showinfo("Evidence Folder Not Found", f"Evidence folder was not found:\n\n{evidence_root}")
            return
        try:
            subprocess.Popen(["explorer", str(evidence_root)])
            self._append_message(f"Evidence folder opened: {evidence_root}")
        except OSError as exc:
            messagebox.showerror("Open Evidence Failed", f"Could not open evidence folder:\n\n{exc}")

    def open_latest_report(self) -> None:
        report_path = self._find_latest_report_path()
        if report_path is None:
            messagebox.showinfo("No Report Found", "No Word report was found for the selected Citrix Desktop Name.")
            return
        self._download_word_report(report_path)

    def build_word_document(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before rebuilding the Word document.")
            return

        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showinfo("Citrix Desktop Name Required", "Enter or select a Citrix Desktop Name first.")
            return

        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        if not screenshots_root.exists() or not any(screenshots_root.rglob("*.png")):
            messagebox.showinfo(
                "No Screenshots Found",
                f"No evidence screenshots were found for:\n\n{desktop_name}\n\nExpected folder:\n{screenshots_root}",
            )
            return

        if self.build_doc_button is not None:
            self.build_doc_button.configure(state=tk.DISABLED)
        self._append_message(f"Building Word report from screenshots folder: {screenshots_root}")
        threading.Thread(
            target=self._build_doc_worker,
            args=(desktop_name, self.config.path("screenshots_dir")),
            daemon=True,
        ).start()

    def _build_doc_worker(self, desktop_name: str, screenshots_base_dir: Path) -> None:
        try:
            report_path = generate_report_from_screenshots(screenshots_base_dir, desktop_name)
        except Exception as exc:
            self.events.put(("build_doc_failed", str(exc)))
        else:
            self.events.put(("build_doc_complete", desktop_name, report_path))

    def _handle_build_doc_complete(self, desktop_name: str, report_path: Path) -> None:
        self.latest_report_path = report_path
        if self.build_doc_button is not None:
            self.build_doc_button.configure(state=tk.NORMAL)
        self._append_message(f"Word report rebuilt from available screenshots: {report_path}")
        self._refresh_run_manifest_async(desktop_name)
        self._show_build_doc_notification(desktop_name, report_path)

    def _handle_build_doc_failed(self, error_message: str) -> None:
        if self.build_doc_button is not None:
            self.build_doc_button.configure(state=tk.NORMAL)
        self._append_message(f"Build Doc failed: {error_message}")
        messagebox.showerror("Build Doc Failed", f"Could not rebuild the Word report:\n\n{error_message}")

    def run_preflight_check(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before running Preflight.")
            return
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showinfo("Citrix Desktop Name Required", "Enter or select a Citrix Desktop Name first.")
            return

        if self.preflight_button is not None:
            self.preflight_button.configure(state=tk.DISABLED)
        self._append_message(f"Preflight started for: {desktop_name}")
        threading.Thread(target=self._preflight_worker, args=(desktop_name,), daemon=True).start()

    def _preflight_worker(self, desktop_name: str) -> None:
        try:
            result = run_preflight_checks(self.config, desktop_name)
        except Exception as exc:
            self.events.put(("preflight_failed", str(exc)))
        else:
            self.events.put(("preflight_complete", result))

    def _handle_preflight_complete(self, result: PreflightResult) -> None:
        if self.preflight_button is not None:
            self.preflight_button.configure(state=tk.NORMAL)
        status = "Pass" if result.ok else "Fail"
        self._append_message(
            f"Preflight completed: {status}. Warnings: {result.warning_count}, Failures: {result.failed_count}"
        )
        for item in result.items:
            self._append_message(f"Preflight {item.status}: {item.name} - {item.message}")
        self._show_preflight_notification(result)

    def _handle_preflight_failed(self, error_message: str) -> None:
        if self.preflight_button is not None:
            self.preflight_button.configure(state=tk.NORMAL)
        self._append_message(f"Preflight failed: {error_message}")
        messagebox.showerror("Preflight Failed", f"Could not complete preflight checks:\n\n{error_message}")

    def audit_evidence(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before auditing evidence.")
            return
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showinfo("Citrix Desktop Name Required", "Enter or select a Citrix Desktop Name first.")
            return

        if self.evidence_audit_button is not None:
            self.evidence_audit_button.configure(state=tk.DISABLED)
        self._append_message(f"Evidence audit started for: {desktop_name}")
        threading.Thread(target=self._evidence_audit_worker, args=(desktop_name,), daemon=True).start()

    def _evidence_audit_worker(self, desktop_name: str) -> None:
        try:
            result = audit_evidence_folder(self.config.path("screenshots_dir"), desktop_name)
        except Exception as exc:
            self.events.put(("evidence_audit_failed", str(exc)))
        else:
            self.events.put(("evidence_audit_complete", result))

    def _handle_evidence_audit_complete(self, result: EvidenceAuditResult) -> None:
        if self.evidence_audit_button is not None:
            self.evidence_audit_button.configure(state=tk.NORMAL)
        self._append_message(
            "Evidence audit completed: "
            f"Present {result.present_count}, Missing {result.missing_count}, "
            f"Failed {result.failed_count}, Warnings {result.warning_count}"
        )
        if result.audit_path:
            self._append_message(f"Evidence audit log: {result.audit_path}")
        self._show_evidence_audit_notification(result)

    def _handle_evidence_audit_failed(self, error_message: str) -> None:
        if self.evidence_audit_button is not None:
            self.evidence_audit_button.configure(state=tk.NORMAL)
        self._append_message(f"Evidence audit failed: {error_message}")
        messagebox.showerror("Evidence Audit Failed", f"Could not audit evidence:\n\n{error_message}")

    def create_support_bundle_action(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before creating a support bundle.")
            return
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showinfo("Citrix Desktop Name Required", "Enter or select a Citrix Desktop Name first.")
            return

        if self.support_bundle_button is not None:
            self.support_bundle_button.configure(state=tk.DISABLED)
        self._append_message(f"Support bundle creation started for: {desktop_name}")
        threading.Thread(target=self._support_bundle_worker, args=(desktop_name,), daemon=True).start()

    def _support_bundle_worker(self, desktop_name: str) -> None:
        try:
            audit_result = audit_evidence_folder(self.config.path("screenshots_dir"), desktop_name)
            bundle_path = create_support_bundle(self.config, desktop_name, audit_result=audit_result)
        except Exception as exc:
            self.events.put(("support_bundle_failed", str(exc)))
        else:
            self.events.put(("support_bundle_complete", desktop_name, bundle_path))

    def _handle_support_bundle_complete(self, desktop_name: str, bundle_path: Path) -> None:
        if self.support_bundle_button is not None:
            self.support_bundle_button.configure(state=tk.NORMAL)
        self._append_message(f"Support bundle created: {bundle_path}")
        self._show_support_bundle_notification(desktop_name, bundle_path)

    def _handle_support_bundle_failed(self, error_message: str) -> None:
        if self.support_bundle_button is not None:
            self.support_bundle_button.configure(state=tk.NORMAL)
        self._append_message(f"Support bundle failed: {error_message}")
        messagebox.showerror("Support Bundle Failed", f"Could not create support bundle:\n\n{error_message}")

    def _find_latest_report_path(self) -> Path | None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            return self.latest_report_path if self.latest_report_path is not None and self.latest_report_path.exists() else None
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        evidence_root = screenshots_root.parent
        if not evidence_root.exists():
            return None
        reports = sorted(evidence_root.glob("*_Testing_.docx"), key=lambda path: path.stat().st_mtime, reverse=True)
        return reports[0] if reports else None

    def _regenerate_latest_report_after_single_rerun(self, desktop_name: str) -> None:
        self._start_report_refresh_after_single_rerun(desktop_name)

    def _start_report_refresh_after_single_rerun(self, desktop_name: str) -> None:
        log_path = self._find_latest_complete_testing_log_path(desktop_name)
        if log_path is None:
            return
        if self.report_refresh_in_progress:
            self.report_refresh_pending_desktop_name = desktop_name
            self._append_message("Word report refresh is already running; latest rerun refresh queued.")
            return

        screenshots_base_dir = self.config.path("screenshots_dir")
        self.report_refresh_in_progress = True
        self._append_message("Refreshing Word report in background with latest rerun evidence...")
        threading.Thread(
            target=self._report_refresh_worker,
            args=(desktop_name, log_path, screenshots_base_dir),
            daemon=True,
        ).start()

    def _report_refresh_worker(self, desktop_name: str, log_path: Path, screenshots_base_dir: Path) -> None:
        try:
            report_path = generate_complete_testing_report(
                log_path=log_path,
                screenshots_base_dir=screenshots_base_dir,
                desktop_name=desktop_name,
            )
        except Exception as exc:
            self.events.put(("report_refresh_failed", str(exc)))
        else:
            self.events.put(("report_refresh_complete", desktop_name, report_path))

    def _handle_report_refresh_complete(self, desktop_name: str, report_path: Path) -> None:
        self.report_refresh_in_progress = False
        self.latest_report_path = report_path
        self._append_message(f"Word report refreshed with latest rerun evidence: {report_path}")
        self._refresh_run_manifest_async(desktop_name)
        self._run_pending_report_refresh_if_needed(desktop_name)

    def _handle_report_refresh_failed(self, error_message: str) -> None:
        self.report_refresh_in_progress = False
        self._append_message(f"Word report refresh after rerun failed: {error_message}")
        self._run_pending_report_refresh_if_needed(None)

    def _run_pending_report_refresh_if_needed(self, completed_desktop_name: str | None) -> None:
        pending_desktop_name = self.report_refresh_pending_desktop_name
        self.report_refresh_pending_desktop_name = None
        if pending_desktop_name:
            self.after(1, lambda name=pending_desktop_name: self._start_report_refresh_after_single_rerun(name))

    def _refresh_run_manifest_async(self, desktop_name: str | None = None) -> None:
        desktop_name = desktop_name or self._normalized_desktop_name()
        if not desktop_name:
            return
        if self.manifest_refresh_in_progress:
            self.manifest_refresh_pending_desktop_name = desktop_name
            return

        self.manifest_refresh_in_progress = True
        screenshots_base_dir = self.config.path("screenshots_dir")
        logs_base_dir = self.config.path("logs_dir")
        threading.Thread(
            target=self._run_manifest_worker,
            args=(desktop_name, screenshots_base_dir, logs_base_dir),
            daemon=True,
        ).start()

    def _run_manifest_worker(self, desktop_name: str, screenshots_base_dir: Path, logs_base_dir: Path) -> None:
        try:
            manifest_path = build_run_manifest(screenshots_base_dir, logs_base_dir, desktop_name)
        except Exception as exc:
            self.events.put(("manifest_refresh_failed", str(exc)))
        else:
            self.events.put(("manifest_refresh_complete", desktop_name, manifest_path))

    def _handle_manifest_refresh_complete(self, desktop_name: str, manifest_path: Path) -> None:
        self.manifest_refresh_in_progress = False
        self.latest_manifest_path = manifest_path
        self._append_message(f"Run manifest updated: {manifest_path}")
        self._run_pending_manifest_refresh_if_needed()

    def _handle_manifest_refresh_failed(self, error_message: str) -> None:
        self.manifest_refresh_in_progress = False
        self._append_message(f"Run manifest update failed: {error_message}")
        self._run_pending_manifest_refresh_if_needed()

    def _run_pending_manifest_refresh_if_needed(self) -> None:
        pending_desktop_name = self.manifest_refresh_pending_desktop_name
        self.manifest_refresh_pending_desktop_name = None
        if pending_desktop_name:
            self.after(1, lambda name=pending_desktop_name: self._refresh_run_manifest_async(name))

    def _find_latest_complete_testing_log_path(self, desktop_name: str) -> Path | None:
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), desktop_name)
        if not logs_dir.exists():
            return None
        logs = sorted(
            logs_dir.glob("Perform_Complete_Testing_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return logs[0] if logs else None

    def _on_test_selection_changed(self) -> None:
        self._update_selection_cues()

    def _on_section_selection_changed(self, section_title: str) -> None:
        if self._syncing_section_selection:
            return
        section_var = self.section_select_vars.get(section_title)
        if section_var is None:
            return
        selected = bool(section_var.get())
        self._syncing_section_selection = True
        try:
            for test_id in self.section_test_ids.get(section_title, []):
                variable = self.selection_vars.get(test_id)
                if variable is not None:
                    variable.set(selected)
        finally:
            self._syncing_section_selection = False
        self._update_selection_cues()

    def _update_selection_cues(self) -> None:
        total_selected = len(self._selected_test_case_ids())
        for title, label in self.section_selection_labels.items():
            selected_count = len(self._selected_test_case_ids(title))
            total_count = len(self.section_test_ids.get(title, []))
            section_var = self.section_select_vars.get(title)
            if section_var is not None:
                self._syncing_section_selection = True
                try:
                    section_var.set(bool(total_count and selected_count == total_count))
                finally:
                    self._syncing_section_selection = False
            if selected_count:
                if total_count and selected_count == total_count:
                    label.configure(text=f"All {selected_count} selected")
                else:
                    label.configure(text=f"Custom selection mode active: {selected_count} selected")
            else:
                label.configure(text="")
        if self.selected_progress_label is not None and self.active_mode is None:
            if total_selected:
                self.selected_progress_label.configure(
                    text=f"{total_selected} selected. Run them together or adjust the checklist below."
                )
            else:
                self.selected_progress_label.configure(
                    text="Select testcases from any section, then run them together."
                )
    def _selected_test_case_ids(self, section_title: str | None = None) -> list[str]:
        ids = self.section_test_ids.get(section_title, []) if section_title else [
            test_id
            for section_ids in self.section_test_ids.values()
            for test_id in section_ids
        ]
        return [
            test_id
            for test_id in ids
            if self.selection_vars.get(test_id) is not None and self.selection_vars[test_id].get()
        ]

    def _test_cases_for_ids(self, test_case_ids: list[str]) -> list[TestCase]:
        tests_by_id = {test_case.id: test_case for test_case in self.test_cases}
        return [tests_by_id[test_id] for test_id in test_case_ids if test_id in tests_by_id]

    def _begin_run_tracking(self, test_case_ids: list[str] | None = None) -> None:
        self.active_run_statuses = {}
        self.last_failed_test_case_ids = []
        if test_case_ids is not None:
            self.active_sequence_ids = list(test_case_ids)
        self._set_rerun_failed_enabled(False)

    def _track_test_status(self, test_case_id: str, status: str) -> None:
        self.active_run_statuses[test_case_id] = status

    def _failed_ids_from_active_run(self) -> list[str]:
        ordered_ids = self.active_sequence_ids or list(self.active_run_statuses)
        return [
            test_id
            for test_id in ordered_ids
            if self.active_run_statuses.get(test_id) == "Fail"
        ]

    def _finish_run_tracking(self) -> None:
        self.last_failed_test_case_ids = self._failed_ids_from_active_run()
        if self.last_failed_test_case_ids:
            failed_names = [
                self._test_name_for_id(test_id)
                for test_id in self.last_failed_test_case_ids
            ]
            self._append_message(
                "Rerun Failed ready for: " + ", ".join(failed_names)
            )
        self._set_rerun_failed_enabled(bool(self.last_failed_test_case_ids))

    def _set_rerun_failed_enabled(self, enabled: bool) -> None:
        if self.global_rerun_failed_button is not None:
            self.global_rerun_failed_button.configure(
                state=tk.NORMAL if enabled and self.active_mode is None else tk.DISABLED
            )

    def rerun_failed_testcases(self) -> None:
        if self.active_mode is not None:
            return
        failed_ids = [
            test_id
            for test_id in self.last_failed_test_case_ids
            if test_id in self.selection_vars or test_id == POST_COMPLETE_ZSCALER_TEST_NAME
        ]
        if not failed_ids:
            messagebox.showinfo("No Failed Testcases", "There are no failed testcases available to rerun.")
            self._set_rerun_failed_enabled(False)
            return
        if failed_ids == [POST_COMPLETE_ZSCALER_TEST_NAME]:
            self._append_message(f"Rerunning failed testcase: {POST_COMPLETE_ZSCALER_TEST_NAME}")
            self.rerun_post_complete_zscaler_evidence()
            return
        if POST_COMPLETE_ZSCALER_TEST_NAME in failed_ids:
            messagebox.showinfo(
                "Post-complete ZScaler Rerun",
                (
                    "Post-complete ZScaler recovery must be rerun by itself. "
                    "Run or select the other failed testcases first, then use Rerun Failed again for this item."
                ),
            )
            return

        for variable in self.selection_vars.values():
            variable.set(False)
        for test_id in failed_ids:
            self.selection_vars[test_id].set(True)
        self._update_selection_cues()
        self._append_message(
            "Rerunning failed testcases: "
            + ", ".join(self._test_name_for_id(test_id) for test_id in failed_ids)
        )
        self.run_selected_section(None)

    def rerun_post_complete_zscaler_evidence(self) -> None:
        if self.active_mode is not None:
            return
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return

        self._begin_run_tracking([POST_COMPLETE_ZSCALER_TEST_NAME])
        self._track_test_status(POST_COMPLETE_ZSCALER_TEST_NAME, "Running")
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(True)
        self._set_complete_pause_enabled(True)
        self._set_complete_skip_enabled(True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_skip_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = "post_complete_zscaler"
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_run_progress(
            title="Post-complete ZScaler",
            completed=0,
            total=1,
            status="Running",
            current=POST_COMPLETE_ZSCALER_TEST_NAME,
            next_item="None",
            remaining=1,
            elapsed_seconds=0,
        )
        self._append_message(f"Starting post-complete ZScaler recovery: {desktop_name}")
        self.after(1000, self._tick_sequence_runtime)

        thread = threading.Thread(
            target=self._run_post_complete_zscaler_worker,
            args=(desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def run_test(self, test_case: TestCase) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return
        if self._block_non_silo43_testcase_run(desktop_name, [test_case.name]):
            return
        if not self._ensure_evidence_root_ready("Evidence Root"):
            return

        self._begin_run_tracking([test_case.id])
        self._set_status(test_case.id, "Running")
        self._track_test_status(test_case.id, "Running")
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._enable_stop_button(test_case.id)
        self._set_pause_button_enabled(test_case.id, True)
        self._set_skip_button_enabled(test_case.id, True)
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_skip_event = Event()
        self.active_paused = False
        self.active_test_id = test_case.id
        self.active_selected_section = None
        self.active_mode = "single"
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_run_progress(
            title="Individual Testcase",
            completed=0,
            total=1,
            status="Running",
            current=test_case.name,
            next_item="None",
            remaining=1,
            elapsed_seconds=0,
        )
        self._append_message(f"Starting {test_case.name}")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")

        thread = threading.Thread(
            target=self._run_test_worker,
            args=(test_case, desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def _schedule_presets(self) -> dict[str, list[str]]:
        raw_presets = self.config.raw.get("schedule_presets", {})
        if not isinstance(raw_presets, dict):
            return {}
        presets: dict[str, list[str]] = {}
        for name, values in raw_presets.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(values, str):
                desktops = self._parse_scheduled_desktops(values)
            elif isinstance(values, list):
                desktops = self._parse_scheduled_desktops("\n".join(str(value) for value in values))
            else:
                desktops = []
            if desktops:
                presets[name.strip()] = desktops
        return dict(sorted(presets.items(), key=lambda item: item[0].casefold()))

    def _save_schedule_presets(self, presets: dict[str, list[str]]) -> None:
        self.config.raw["schedule_presets"] = presets
        self._save_app_config()

    def _inspect_scheduled_desktop_windows(self, desktop_names: list[str]) -> dict[str, str]:
        try:
            import pygetwindow as gw
        except Exception as exc:
            return {desktop_name: f"Window check unavailable: {exc}" for desktop_name in desktop_names}

        try:
            titles = [title.strip() for title in gw.getAllTitles() if title and title.strip()]
        except Exception as exc:
            return {desktop_name: f"Could not inspect windows: {exc}" for desktop_name in desktop_names}

        results: dict[str, str] = {}
        for desktop_name in desktop_names:
            normalized = desktop_name.casefold()
            match = next((title for title in titles if normalized and normalized in title.casefold()), "")
            results[desktop_name] = match
        return results

    def _scheduled_preflight_summary(
        self,
        desktop_names: list[str],
        check_all_windows: bool = True,
    ) -> tuple[bool, str]:
        evidence_health = self._validate_evidence_root_path()
        desktops_to_check = desktop_names if check_all_windows else desktop_names[:1]
        window_results = self._inspect_scheduled_desktop_windows(desktops_to_check)
        failures: list[str] = []
        warnings: list[str] = []

        if not bool(evidence_health.get("ok")):
            failures.extend(str(message) for message in evidence_health.get("failures") or [])
        warnings.extend(str(message) for message in evidence_health.get("warnings") or [])

        for desktop_name, match in window_results.items():
            if not match:
                failures.append(f"{desktop_name}: matching Citrix Desktop Viewer window is not open.")
            elif match.startswith("Window check unavailable") or match.startswith("Could not inspect"):
                failures.append(f"{desktop_name}: {match}")

        lines = [
            f"Scheduled desktops: {len(desktop_names)}",
            f"Evidence root: {evidence_health.get('root')}",
            "",
            "Desktop window check:",
        ]
        if not check_all_windows and len(desktop_names) > 1:
            lines.extend(
                [
                    "Attended queue mode: only the first desktop is checked now.",
                    "Queued desktops will be checked one-by-one before they run, after you log in or scan QR.",
                    "",
                ]
            )
        for desktop_name, match in window_results.items():
            status = "Open" if match and not match.startswith(("Window check unavailable", "Could not inspect")) else "Missing"
            detail = match if match else "No matching window found"
            lines.append(f"- {desktop_name}: {status} ({detail})")
        if failures:
            lines.extend(["", "Blocking issue(s):"])
            lines.extend(f"- {message}" for message in failures)
        if warnings:
            lines.extend(["", "Warning(s):"])
            lines.extend(f"- {message}" for message in warnings)
        return not failures, "\n".join(lines)

    def _confirm_scheduled_preflight(
        self,
        desktop_names: list[str],
        parent: tk.Misc | None = None,
        check_all_windows: bool = True,
    ) -> bool:
        ok, summary = self._scheduled_preflight_summary(desktop_names, check_all_windows=check_all_windows)
        if not ok:
            messagebox.showerror("Schedule Preflight Failed", summary, parent=parent)
            return False
        if "Warning(s):" in summary:
            return messagebox.askyesno(
                "Schedule Preflight Warning",
                summary + "\n\nStart scheduled Complete Testing anyway?",
                parent=parent,
            )
        return True

    def show_complete_schedule_dialog(self) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before scheduling multiple desktops.")
            return

        modal = ctk.CTkToplevel(self)
        modal.title("Schedule Complete Testing")
        modal.configure(fg_color=THEME["bg"])
        self._configure_schedule_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(
            card,
            text="Schedule Complete Testing",
            text_color=THEME["text"],
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(
            card,
            text="Enter one Citrix Desktop Name per line. The first desktop must be ready now; the runner will pause before each queued desktop so you can log in when needed.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            wraplength=560,
        ).pack(anchor=tk.W, padx=18, pady=(6, 0))

        preset_row = ctk.CTkFrame(card, fg_color="transparent")
        preset_row.pack(fill=tk.X, padx=18, pady=(12, 0))
        preset_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            preset_row,
            text="Preset",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        preset_names = list(self._schedule_presets())
        preset_values = preset_names or ["No saved presets"]
        preset_var = tk.StringVar(value=preset_values[0])
        preset_menu = ctk.CTkOptionMenu(
            preset_row,
            variable=preset_var,
            values=preset_values,
            height=30,
            corner_radius=8,
            fg_color=THEME["input"],
            button_color=THEME["primary"],
            button_hover_color=THEME["primary_hover"],
            text_color=THEME["text"],
            dropdown_fg_color=THEME["card"],
            dropdown_hover_color=THEME["card_hover"],
            dropdown_text_color=THEME["text"],
            font=("Segoe UI", 10, "bold"),
        )
        preset_menu.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        preset_name_entry = ctk.CTkEntry(
            preset_row,
            placeholder_text="Preset name",
            height=30,
            corner_radius=8,
            fg_color=THEME["input"],
            border_color=THEME["border"],
            border_width=1,
            text_color=THEME["text"],
            placeholder_text_color=THEME["muted"],
            font=("Segoe UI", 10),
        )
        preset_name_entry.grid(row=0, column=2, sticky="ew", padx=(0, 8))

        input_box = ctk.CTkTextbox(
            card,
            height=150,
            fg_color=THEME["input"],
            text_color=THEME["text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
            font=("Segoe UI", 11),
            wrap=tk.WORD,
        )
        input_box.pack(fill=tk.X, padx=18, pady=(14, 0))
        current_desktops = self._parse_scheduled_desktops(self._desktop_entry_value())
        if current_desktops:
            input_box.insert("1.0", "\n".join(current_desktops))

        notice = ctk.CTkLabel(
            card,
            text="Use new lines, commas, semicolons, or period-space separators. Duplicates are ignored.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))

        def refresh_preset_menu(selected_name: str | None = None) -> None:
            names = list(self._schedule_presets())
            values = names or ["No saved presets"]
            preset_menu.configure(values=values)
            preset_var.set(selected_name if selected_name in names else values[0])

        def load_preset(_selection: str | None = None) -> None:
            name = preset_var.get()
            presets = self._schedule_presets()
            desktops = presets.get(name)
            if not desktops:
                notice.configure(text="Select a saved preset to load.", text_color=THEME["muted"])
                return
            input_box.delete("1.0", tk.END)
            input_box.insert("1.0", "\n".join(desktops))
            preset_name_entry.delete(0, tk.END)
            preset_name_entry.insert(0, name)
            notice.configure(text=f"Preset loaded: {name}", text_color=THEME["teal"])

        def save_preset() -> None:
            desktops = self._parse_scheduled_desktops(input_box.get("1.0", tk.END))
            name = preset_name_entry.get().strip()
            if not name:
                notice.configure(text="Enter a preset name before saving.", text_color=THEME["danger"])
                return
            if not desktops:
                notice.configure(text="Add at least one desktop before saving a preset.", text_color=THEME["danger"])
                return
            presets = self._schedule_presets()
            presets[name] = desktops
            try:
                self._save_schedule_presets(presets)
            except OSError as exc:
                notice.configure(text=f"Could not save preset: {exc}", text_color=THEME["danger"])
                return
            refresh_preset_menu(name)
            notice.configure(text=f"Preset saved: {name}", text_color=THEME["teal"])

        def delete_preset() -> None:
            name = preset_var.get()
            presets = self._schedule_presets()
            if name not in presets:
                notice.configure(text="Select a saved preset to delete.", text_color=THEME["muted"])
                return
            presets.pop(name, None)
            try:
                self._save_schedule_presets(presets)
            except OSError as exc:
                notice.configure(text=f"Could not delete preset: {exc}", text_color=THEME["danger"])
                return
            refresh_preset_menu()
            notice.configure(text=f"Preset deleted: {name}", text_color=THEME["teal"])

        preset_menu.configure(command=load_preset)
        ModernButton(preset_row, text="Save", variant="secondary", command=save_preset, height=30, min_width=68, font=("Segoe UI", 9, "bold")).grid(row=0, column=3, sticky="e", padx=(0, 6))
        ModernButton(preset_row, text="Delete", variant="danger", command=delete_preset, height=30, min_width=72, font=("Segoe UI", 9, "bold")).grid(row=0, column=4, sticky="e")

        def start_schedule() -> None:
            desktops = self._parse_scheduled_desktops(input_box.get("1.0", tk.END))
            if not desktops:
                notice.configure(text="Add at least one Citrix Desktop Name before starting.", text_color=THEME["danger"])
                return
            if not self._confirm_scheduled_preflight(desktops, parent=modal, check_all_windows=False):
                notice.configure(text="Preflight failed. Open the first Citrix Desktop Viewer window, then retry.", text_color=THEME["danger"])
                return
            notice.configure(text="First desktop is ready. Starting attended scheduled Complete Testing...", text_color=THEME["teal"])
            modal.destroy()
            self.run_scheduled_complete_testing(desktops, preflight_checked=True)

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(actions, text="Preflight", variant="secondary", command=lambda: messagebox.showinfo("Schedule Preflight", self._scheduled_preflight_summary(self._parse_scheduled_desktops(input_box.get("1.0", tk.END)), check_all_windows=False)[1] if self._parse_scheduled_desktops(input_box.get("1.0", tk.END)) else "Add at least one Citrix Desktop Name.", parent=modal), height=34, min_width=110).pack(side=tk.LEFT, padx=(0, 8))
        ModernButton(actions, text="Run Schedule", variant="primary", command=start_schedule, height=34, min_width=140).pack(side=tk.LEFT)
        ModernButton(actions, text="Cancel", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)

        modal.update_idletasks()
        width = 760
        height = min(max(modal.winfo_reqheight(), 430), 600)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _parse_scheduled_desktops(self, text: str) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        normalized_text = re.sub(r"\s*(?:[,;]|\.(?=\s|$))\s*", "\n", text)
        for raw_part in normalized_text.splitlines():
            desktop_name = self._normalized_desktop_name(raw_part)
            key = desktop_name.casefold()
            if desktop_name and key not in seen:
                names.append(desktop_name)
                seen.add(key)
        return names

    def _scheduled_result_for_desktop(self, desktop_name: str) -> dict[str, object] | None:
        key = desktop_name.casefold()
        for item in self.scheduled_results:
            if str(item.get("desktop_name") or "").casefold() == key:
                return item
        return None

    def _show_schedule_status_panel(self) -> None:
        if self.schedule_status_card is not None:
            self.schedule_status_card.grid()

    def _hide_schedule_status_panel(self) -> None:
        if self.schedule_status_card is not None:
            self.schedule_status_card.grid_remove()

    def _update_schedule_status_panel(self) -> None:
        if self.schedule_status_card is None:
            return
        if not self.scheduled_desktops and not self.scheduled_results:
            self._hide_schedule_status_panel()
            return

        self._show_schedule_status_panel()
        completed = len(self.scheduled_results)
        total = len(self.scheduled_desktops) or completed
        failed = sum(1 for item in self.scheduled_results if str(item.get("status") or "") == "Fail")
        passed = sum(1 for item in self.scheduled_results if str(item.get("status") or "") == "Pass")
        skipped = sum(1 for item in self.scheduled_results if str(item.get("status") or "") == "Skipped")
        waiting = bool(self.scheduled_waiting_desktop)
        running = 1 if self.active_mode == "scheduled_complete" and completed < total and not waiting else 0
        active_slot = running or waiting
        queued = max(total - completed - int(bool(active_slot)), 0)
        current = "Finished"
        if self.scheduled_waiting_desktop:
            current = f"Waiting: {self.scheduled_waiting_desktop}"
        elif self.active_mode == "scheduled_complete" and 0 <= self.scheduled_index < len(self.scheduled_desktops):
            current = self.scheduled_desktops[self.scheduled_index]

        if self.schedule_status_current is not None:
            self.schedule_status_current.configure(text=f"Current: {current}")
        if self.schedule_status_counts is not None:
            self.schedule_status_counts.configure(
                text=f"Queued {queued} | Completed {completed}/{total} | Passed {passed} | Failed {failed} | Skipped {skipped}"
            )

        queue_frame = self.schedule_status_queue
        if queue_frame is None:
            return
        for child in queue_frame.winfo_children():
            child.destroy()

        for index, desktop_name in enumerate(self.scheduled_desktops):
            result = self._scheduled_result_for_desktop(desktop_name)
            if result is not None:
                status = str(result.get("status") or "Unknown")
            elif self.scheduled_waiting_desktop and desktop_name.casefold() == self.scheduled_waiting_desktop.casefold():
                status = "Paused"
            elif self.active_mode == "scheduled_complete" and index == self.scheduled_index:
                status = "Running"
            else:
                status = "Queued"

            row = ctk.CTkFrame(queue_frame, fg_color="transparent")
            row.pack(fill=tk.X, padx=8, pady=(5, 0))
            StatusBadge(row, text=status, width=72, height=20).pack(side=tk.LEFT)
            ctk.CTkLabel(
                row,
                text=f"{index + 1}. {desktop_name}",
                text_color=THEME["text"] if status != "Queued" else THEME["muted"],
                font=("Segoe UI", 10, "bold"),
            ).pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True, anchor=tk.W)

            if result is not None:
                screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
                ModernButton(
                    row,
                    text="Evidence",
                    variant="secondary",
                    command=lambda folder=screenshots_root: self._open_folder_path(folder, self.schedule_status_current, "Evidence folder opened."),
                    height=22,
                    min_width=74,
                    font=("Segoe UI", 8, "bold"),
                ).pack(side=tk.RIGHT, padx=(6, 0))
                report_text = str(result.get("report_path") or "")
                report_path = Path(report_text) if report_text else None
                report_button = ModernButton(
                    row,
                    text="Report",
                    variant="secondary",
                    command=lambda path=report_path: self._download_word_report(path, self.schedule_status_current),
                    height=22,
                    min_width=66,
                    font=("Segoe UI", 8, "bold"),
                )
                report_button.pack(side=tk.RIGHT, padx=(6, 0))
                if report_path is None or not report_path.exists():
                    report_button.configure(state=tk.DISABLED)

    def run_scheduled_complete_testing(self, desktop_names: list[str], preflight_checked: bool = False) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before scheduling multiple desktops.")
            return
        desktops = self._parse_scheduled_desktops("\n".join(desktop_names))
        if not desktops:
            messagebox.showerror("Citrix Desktop Name Required", "Please add at least one Citrix Desktop Name.")
            return
        if not preflight_checked and not self._confirm_scheduled_preflight(desktops, check_all_windows=False):
            return

        self.scheduled_desktops = desktops
        self.scheduled_results = []
        self.scheduled_index = 0
        self.scheduled_waiting_desktop = None
        stop_event = Event()
        pause_event = Event()
        skip_event = Event()
        self._update_schedule_status_panel()
        self._append_message(f"Starting scheduled Complete Testing for {len(desktops)} desktop(s)")
        self._start_complete_testing_for_desktop(
            desktops[0],
            scheduled=True,
            stop_event=stop_event,
            pause_event=pause_event,
            skip_event=skip_event,
        )

    def run_complete_testing(self) -> None:
        scheduled_candidates = self._parse_scheduled_desktops(self._desktop_entry_value())
        if len(scheduled_candidates) > 1:
            messagebox.showerror(
                "Use Schedule For Multiple Desktops",
                (
                    "Multiple Citrix Desktop Names were detected in the main input.\n\n"
                    "Use the Schedule button to run Complete Testing for multiple opened desktops."
                ),
            )
            return

        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return

        if not self._ensure_evidence_root_ready("Evidence Root"):
            return

        self.scheduled_desktops = []
        self.scheduled_results = []
        self.scheduled_index = 0
        self.scheduled_waiting_desktop = None
        self._hide_schedule_status_panel()
        self._start_complete_testing_for_desktop(desktop_name, scheduled=False)

    def _start_complete_testing_for_desktop(
        self,
        desktop_name: str,
        scheduled: bool = False,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        skip_event: Event | None = None,
    ) -> None:
        if scheduled:
            self._set_desktop_entry_value(desktop_name)
            self.scheduled_waiting_desktop = None

        self._set_complete_status("Running")
        self._set_master_status("Idle")
        self._set_shakedown_status("Idle")
        self.complete_completed_count = 0
        silo43_sequence_names = list(SILO43_TEST_CASE_ORDER) if is_silo43_desktop(desktop_name) else []
        self.complete_total_count = (
            len(mandatory_order_for_desktop(desktop_name))
            + len(SHAKEDOWN_TEST_CASE_ORDER)
            + len(silo43_sequence_names)
            + len(IAT_TEST_CASE_ORDER)
            + 1
        )
        self.complete_started_monotonic = time.monotonic()
        self.complete_current_phase = "Starting"
        self.complete_current_test = "Preparing"
        self._set_complete_progress(f"0 of {self.complete_total_count} completed")
        self._set_complete_runtime_summary()
        self._set_master_progress("Ready")
        self._set_shakedown_progress("Ready")
        complete_sequence_names = (
            mandatory_order_for_desktop(desktop_name)
            + list(SHAKEDOWN_TEST_CASE_ORDER)
            + silo43_sequence_names
            + list(IAT_TEST_CASE_ORDER)
        )
        complete_sequence_ids = self._test_ids_for_test_names(complete_sequence_names)
        complete_sequence_ids.append(POST_COMPLETE_ZSCALER_TEST_NAME)
        self._begin_run_tracking(complete_sequence_ids)
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(True)
        self._set_complete_pause_enabled(True)
        self._set_complete_skip_enabled(True)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = stop_event or Event()
        self.active_pause_event = pause_event or Event()
        self.active_skip_event = skip_event or Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = "scheduled_complete" if scheduled else "complete"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")
        if scheduled:
            self._append_message(
                f"Scheduled Complete Testing {self.scheduled_index + 1} of {len(self.scheduled_desktops)}"
            )
            self._update_schedule_status_panel()
        self._append_message("Starting Perform Complete Testing")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")
        if not self.complete_runtime_tick_active:
            self.complete_runtime_tick_active = True
            self.after(1000, self._tick_complete_runtime)

        thread = threading.Thread(
            target=self._run_complete_worker,
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def run_mandatory_testcases(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return
        if not self._ensure_evidence_root_ready("Evidence Root"):
            return

        self._set_master_status("Running")
        self.master_completed_count = 0
        self.master_total_count = len(mandatory_order_for_desktop(desktop_name))
        self.active_sequence_ids = self._test_ids_for_test_names(mandatory_order_for_desktop(desktop_name))
        self._begin_run_tracking(self.active_sequence_ids)
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_master_progress(self._sequence_progress_text(0, self.master_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(True)
        self._set_master_pause_enabled(True)
        self._set_master_skip_enabled(True)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_skip_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = "master"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")
        self._append_message("Starting Mandatory Testcases")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")
        self.after(1000, self._tick_sequence_runtime)

        thread = threading.Thread(
            target=self._run_mandatory_worker,
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def run_shakedown_testcases(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return
        if not self._ensure_evidence_root_ready("Evidence Root"):
            return

        self._set_shakedown_status("Running")
        self.shakedown_completed_count = 0
        self.shakedown_total_count = len(SHAKEDOWN_TEST_CASE_ORDER)
        self.active_sequence_ids = self._test_ids_for_test_names(SHAKEDOWN_TEST_CASE_ORDER)
        self._begin_run_tracking(self.active_sequence_ids)
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_shakedown_progress(self._sequence_progress_text(0, self.shakedown_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(True)
        self._set_shakedown_pause_enabled(True)
        self._set_shakedown_skip_enabled(True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_skip_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = "shakedown"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")
        self._append_message("Starting Shakedown Testcases")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")
        self.after(1000, self._tick_sequence_runtime)

        thread = threading.Thread(
            target=self._run_shakedown_worker,
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def run_selected_section(self, section_title: str | None) -> None:
        selected_ids = self._selected_test_case_ids(section_title)
        if not selected_ids:
            if section_title is None:
                messagebox.showinfo("No Testcases Selected", "Select one or more testcases from any section first.")
                return
            if section_title == "Mandatory Testcases":
                self.run_mandatory_testcases()
                return
            if section_title == "Shakedown Testcases":
                self.run_shakedown_testcases()
                return
            selected_ids = list(self.section_test_ids.get(section_title, []))

        selected_tests = self._test_cases_for_ids(selected_ids)
        if not selected_tests:
            messagebox.showinfo("No Testcases Selected", "No testcases are available to run in this section.")
            return

        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return
        if self._block_non_silo43_testcase_run(
            desktop_name,
            [test_case.name for test_case in selected_tests],
        ):
            return
        if not self._ensure_evidence_root_ready("Evidence Root"):
            return

        self.selected_completed_count = 0
        self.selected_total_count = len(selected_tests)
        display_title = section_title or "Selected Testcases"
        self.selected_section_title = display_title
        self.active_sequence_ids = list(selected_ids)
        self._begin_run_tracking(self.active_sequence_ids)
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(section_title == "Mandatory Testcases")
        self._set_shakedown_stop_enabled(section_title == "Shakedown Testcases")
        self._set_master_skip_enabled(section_title == "Mandatory Testcases")
        self._set_shakedown_skip_enabled(section_title == "Shakedown Testcases")
        if section_title is not None:
            self._set_section_stop_enabled(section_title, True)
            self._set_section_pause_enabled(section_title, True)
            self._set_section_skip_enabled(section_title, True)
        self._set_global_selected_controls_enabled(True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_skip_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = display_title
        self.active_mode = f"selected:{display_title}"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")

        if section_title == "Mandatory Testcases":
            self._set_master_status("Running")
            self._set_master_progress(self._sequence_progress_text(0, self.selected_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic, selected=True))
        elif section_title == "Shakedown Testcases":
            self._set_shakedown_status("Running")
            self._set_shakedown_progress(self._sequence_progress_text(0, self.selected_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic, selected=True))
        else:
            self._set_selected_progress()

        self._append_message(f"Starting selected run: {display_title}")
        self._append_message(f"Selected testcases: {', '.join(test_case.name for test_case in selected_tests)}")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")
        self.after(1000, self._tick_sequence_runtime)

        thread = threading.Thread(
            target=self._run_selected_worker,
            args=(display_title, selected_tests, desktop_name, self.active_stop_event, self.active_pause_event, self.active_skip_event),
            daemon=True,
        )
        thread.start()

    def request_stop(self, label: str) -> None:
        if self.active_stop_event is None or self.active_stop_event.is_set():
            return
        self.active_stop_event.set()
        if self.active_skip_event is not None:
            self.active_skip_event.clear()
        if self.active_pause_event is not None:
            self.active_pause_event.clear()
        self.active_paused = False
        self._append_message(f"Stop requested: {label}")
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)

    def request_skip(self, label: str) -> None:
        if (
            self.active_skip_event is None
            or self.active_stop_event is None
            or self.active_stop_event.is_set()
        ):
            return
        self.active_skip_event.set()
        if self.active_pause_event is not None and self.active_pause_event.is_set():
            self.active_pause_event.clear()
            self.active_paused = False
            self._set_active_pause_status(paused=False)
            self._refresh_pause_button_text()
        self._append_message(f"Skip requested: {label}")

    def request_pause_resume(self, label: str) -> None:
        if self.active_pause_event is None or self.active_stop_event is None or self.active_stop_event.is_set():
            return
        if self.active_pause_event.is_set():
            self.active_pause_event.clear()
            self.active_paused = False
            self._append_message(f"Resumed: {label}")
            self._set_active_pause_status(paused=False)
        else:
            self.active_pause_event.set()
            self.active_paused = True
            self._append_message(f"Paused: {label}")
            self._set_active_pause_status(paused=True)
        self._refresh_pause_button_text()

    def _desktop_requires_silo18_win_space(self, desktop_name: str) -> bool:
        short_name = desktop_name.replace(" - Desktop Viewer", "").strip().casefold()
        return short_name.startswith("silo18")

    def _silo18_win_space_key(self, desktop_name: str) -> str:
        return desktop_name.replace(" - Desktop Viewer", "").strip().casefold()

    def _ensure_silo18_win_space_once(
        self,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        if not self._desktop_requires_silo18_win_space(desktop_name):
            return

        desktop_key = self._silo18_win_space_key(desktop_name)
        if desktop_key in self.silo18_win_space_done_desktops:
            self.events.put(
                (
                    "message",
                    "SILO18 keyboard preflight already completed for this desktop; skipping Windows + Space.",
                )
            )
            return

        self.events.put(("message", "SILO18 keyboard preflight: hold Windows for 1 second, then press Space once before execution."))
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, _level="INFO": self.events.put(("message", message)),
            citrix_desktop_name=desktop_name,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        try:
            context.activate_window_by_title(desktop_name)
            context.click_screen_center(wait_after_sec=1.0)
            context.hold_key_then_press("win", "space", hold_before_press_sec=1.0, wait_after_sec=1.0)
        except StopRequested:
            self.events.put(("message", "SILO18 keyboard preflight interrupted before completion."))
            return
        except Exception as exc:
            self.events.put(("message", f"SILO18 keyboard preflight warning: {exc}"))
            return

        self.silo18_win_space_done_desktops.add(desktop_key)
        self.events.put(("message", "SILO18 keyboard preflight completed."))

    def _run_test_worker(
        self,
        test_case: TestCase,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
        runner = TestRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            stop_event=CombinedStopSkipEvent(stop_event, skip_event),
            pause_event=pause_event,
        )
        result = runner.run(test_case)
        if result.status == "Stopped" and not stop_event.is_set() and consume_skip_request(skip_event):
            self.events.put(("message", f"Skip requested for {test_case.name}; marking testcase as Skipped."))
            result = ExecutionResult(
                "Skipped",
                result.test_case_name,
                result.log_path,
                result.screenshot_path,
                result.evidence_paths,
            )
        self.events.put(("complete", test_case, result))

    def _run_mandatory_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
        runner = MasterRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            manual_confirmation_callback=lambda result: self.events.put(("manual_confirmation_pause", result)),
            stop_event=stop_event,
            pause_event=pause_event,
            skip_event=skip_event,
        )
        result = runner.run(test_cases)
        self.events.put(("master_complete", result))

    def _run_complete_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
        runner = CompleteTestingRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            phase_status_callback=lambda phase, status: self.events.put(("phase_status", phase, status)),
            manual_confirmation_callback=lambda result: self.events.put(("manual_confirmation_pause", result)),
            stop_event=stop_event,
            pause_event=pause_event,
            skip_event=skip_event,
        )
        result = runner.run(test_cases)
        self.events.put(("complete_testing_complete", desktop_name, result))

    def _run_post_complete_zscaler_worker(
        self,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        result: dict[str, object]
        try:
            self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
            runner = CompleteTestingRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                phase_status_callback=lambda phase, status: self.events.put(("phase_status", phase, status)),
                stop_event=stop_event,
                pause_event=pause_event,
                skip_event=skip_event,
            )
            result = runner.rerun_post_complete_zscaler_evidence()
        except StopRequested:
            result = {
                "test_case": POST_COMPLETE_ZSCALER_TEST_NAME,
                "status": "Stopped",
                "screenshots": [],
                "log_path": None,
                "error": "Stopped by user",
                "capture_timing": "Standalone post-complete ZScaler recovery",
            }
        except Exception as exc:
            result = {
                "test_case": POST_COMPLETE_ZSCALER_TEST_NAME,
                "status": "Fail",
                "screenshots": [],
                "log_path": None,
                "error": str(exc),
                "capture_timing": "Standalone post-complete ZScaler recovery",
            }

        try:
            log_path = self._write_post_complete_zscaler_rerun_log(desktop_name, result)
            result["log_path"] = str(log_path)
        except Exception as exc:
            self.events.put(("message", f"Post-complete ZScaler recovery log write failed: {exc}"))
        self.events.put(("post_complete_zscaler_complete", result))

    def _write_post_complete_zscaler_rerun_log(self, desktop_name: str, result: dict[str, object]) -> Path:
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), desktop_name)
        logs_dir.mkdir(parents=True, exist_ok=True)
        completed_at = datetime.now().replace(microsecond=0)
        timestamp = completed_at.strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"{POST_COMPLETE_ZSCALER_TEST_NAME}_{timestamp}.json"
        result_record = {
            "test_case": POST_COMPLETE_ZSCALER_TEST_NAME,
            "status": str(result.get("status") or "Fail"),
            "screenshots": list(result.get("screenshots") or []),
            "log_path": str(log_path),
            "error": result.get("error"),
            "end_time": completed_at.isoformat(),
            "capture_timing": result.get("capture_timing") or "Standalone post-complete ZScaler recovery",
        }
        payload = {
            "feature_name": "Post-complete ZScaler Recovery",
            "citrix_desktop_name": desktop_name,
            "start_time": completed_at.isoformat(),
            "end_time": completed_at.isoformat(),
            "overall_execution_result": result_record["status"],
            "individual_results": [result_record],
        }
        with log_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
        return log_path

    def _run_shakedown_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
        runner = ShakedownRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            stop_event=stop_event,
            pause_event=pause_event,
            skip_event=skip_event,
        )
        result = runner.run(test_cases)
        self.events.put(("shakedown_complete", result))

    def _run_selected_worker(
        self,
        section_title: str,
        selected_tests: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
        skip_event: Event,
    ) -> None:
        self._ensure_silo18_win_space_once(desktop_name, stop_event, pause_event)
        failed_count = 0
        stopped = False
        manual_check_message = None
        selected_names = [test_case.name for test_case in selected_tests]
        combine_edge_registry = (
            EDGE_WEBVIEW_TEST_NAME in selected_names
            and EDGE_BROWSER_TEST_NAME in selected_names
            and selected_names.index(EDGE_WEBVIEW_TEST_NAME) < selected_names.index(EDGE_BROWSER_TEST_NAME)
        )
        combined_edge_browser_result: ExecutionResult | None = None
        for index, test_case in enumerate(selected_tests):
            if stop_event.is_set():
                stopped = True
                break
            try:
                wait_if_paused(pause_event, stop_event)
            except StopRequested:
                stopped = True
                break
            if consume_skip_request(skip_event):
                self.events.put(("message", f"Skip requested before {test_case.name}; marking testcase as Skipped."))
                self.events.put(("test_status", test_case.id, "Skipped"))
                continue
            if test_case.name == EDGE_BROWSER_TEST_NAME and combined_edge_browser_result is not None:
                self.events.put(("test_status", test_case.id, "Running"))
                self.events.put(("message", f"Selected sequence running: {test_case.name}"))
                self.events.put(
                    (
                        "message",
                        "Edge browser evidence already captured in the combined Edge registry session; "
                        "skipping separate Command Prompt launch.",
                    )
                )
                self.events.put(("test_status", test_case.id, "Pass"))
                if index < len(selected_tests) - 1 and not stop_event.is_set():
                    delay = self._selected_between_tests_delay(section_title, test_case.name)
                    if delay > 0:
                        self.events.put(("message", f"Selected run delay before next test: {delay} second(s)"))
                        try:
                            interruptible_sleep(delay, stop_event, pause_event)
                        except StopRequested:
                            stopped = True
                            break
                continue
            self.events.put(("test_status", test_case.id, "Running"))
            self.events.put(("message", f"Selected sequence running: {test_case.name}"))
            runtime_metadata = {}
            if combine_edge_registry and test_case.name == EDGE_WEBVIEW_TEST_NAME:
                runtime_metadata["combine_edge_registry_evidence"] = True
            result = TestRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=CombinedStopSkipEvent(stop_event, skip_event),
                pause_event=pause_event,
                runtime_metadata=runtime_metadata,
            ).run(test_case)

            skipped_current = result.status == "Stopped" and not stop_event.is_set() and consume_skip_request(skip_event)
            if skipped_current:
                self.events.put(("message", f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing."))
                self.events.put(("test_status", test_case.id, "Skipped"))
            else:
                self.events.put(("test_status", test_case.id, result.status))
            if test_case.name == EDGE_WEBVIEW_TEST_NAME:
                if result.status == "Pass" and result.metadata.get("combined_edge_registry_evidence"):
                    combined_edge_browser_result = result
                else:
                    combined_edge_browser_result = None
            if not is_success_status(result.status):
                if not skipped_current:
                    failed_count += 1
            if result.manual_confirmation_required:
                pause_event.set()
                self.events.put(("manual_confirmation_pause", result))
                try:
                    wait_if_paused(pause_event, stop_event)
                except StopRequested:
                    stopped = True
                    break
                self.events.put(("message", "Selected run resumed after Hostname/IP evidence confirmation."))
            if result.requires_manual_check:
                manual_check_message = result.manual_check_message or result.error_message
                self.events.put(
                    (
                        "message",
                        "Selected run stopped because Hostname_and_IP_Evidence needs manual review.",
                    )
                )
                break
            if result.status == "Stopped" and not skipped_current:
                stopped = True
                break

            try:
                self._cleanup_after_selected_test(section_title, test_case.name, desktop_name, stop_event, pause_event)
            except StopRequested:
                stopped = True
                break
            except Exception as exc:
                self.events.put(("message", f"Selected run cleanup warning after {test_case.name}: {exc}"))

            if index < len(selected_tests) - 1 and not stop_event.is_set():
                if test_case.name == EDGE_WEBVIEW_TEST_NAME and combined_edge_browser_result is not None:
                    continue
                delay = self._selected_between_tests_delay(section_title, test_case.name)
                if delay > 0:
                    self.events.put(("message", f"Selected run delay before next test: {delay} second(s)"))
                    try:
                        interruptible_sleep(delay, stop_event, pause_event)
                    except StopRequested:
                        stopped = True
                        break

        status = "Stopped" if stopped or stop_event.is_set() else ("Pass" if failed_count == 0 else "Fail")
        self.events.put(("selected_complete", section_title, status, failed_count, manual_check_message))

    def _cleanup_after_selected_test(
        self,
        section_title: str,
        test_name: str,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        cleanup_section = self._section_title_for_test_name(test_name) if section_title == "Selected Testcases" else section_title
        if cleanup_section == "Mandatory Testcases":
            MasterRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_test(test_name)
        elif cleanup_section == "Silo 43 Testcases":
            MasterRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_test(test_name)
        elif cleanup_section == "Shakedown Testcases":
            ShakedownRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_test(test_name)
        elif cleanup_section == "IAT Testcase":
            CompleteTestingRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_iat(test_name)

    def _selected_between_tests_delay(self, section_title: str, test_name: str | None = None) -> float:
        delay_section = self._section_title_for_test_name(test_name) if section_title == "Selected Testcases" else section_title
        if delay_section == "Mandatory Testcases":
            return self.config.wait("mandatory_between_tests_wait_sec", 2.0)
        if delay_section == "Silo 43 Testcases":
            return self.config.wait("silo43_between_tests_wait_sec", 2.0)
        if delay_section == "Shakedown Testcases":
            return self.config.wait("shakedown_between_tests_wait_sec", 2.0)
        return 0.0

    def _process_events(self) -> None:
        processed = 0
        max_events_per_tick = 35
        while processed < max_events_per_tick:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            event_type = event[0]
            self._last_progress_monotonic = time.monotonic()
            if event_type == "message":
                self._append_message(event[1])
            elif event_type == "complete":
                _, test_case, result = event
                self._handle_result(test_case, result)
            elif event_type == "test_status":
                _, test_case_id, status = event
                self._track_test_status(test_case_id, status)
                if test_case_id in self.status_labels:
                    self._set_status(test_case_id, status)
                if self.active_mode == "master":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._disable_row_skip_buttons()
                        self._set_skip_button_enabled(test_case_id, True)
                        self._set_master_progress(self._sequence_progress_text(self.master_completed_count, self.master_total_count, test_case_id, self.active_sequence_ids, self.active_sequence_started_monotonic))
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.master_completed_count = min(
                            self.master_completed_count + 1,
                            self.master_total_count,
                        )
                        self._set_master_progress(self._sequence_progress_text(self.master_completed_count, self.master_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                        self._set_skip_button_enabled(test_case_id, False)
                elif self.active_mode == "shakedown":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._disable_row_skip_buttons()
                        self._set_skip_button_enabled(test_case_id, True)
                        self._set_shakedown_progress(self._sequence_progress_text(self.shakedown_completed_count, self.shakedown_total_count, test_case_id, self.active_sequence_ids, self.active_sequence_started_monotonic))
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.shakedown_completed_count = min(
                            self.shakedown_completed_count + 1,
                            self.shakedown_total_count,
                        )
                        self._set_shakedown_progress(self._sequence_progress_text(self.shakedown_completed_count, self.shakedown_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                        self._set_skip_button_enabled(test_case_id, False)
                elif self.active_mode in {"complete", "scheduled_complete"}:
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._disable_row_skip_buttons()
                        self._set_skip_button_enabled(test_case_id, True)
                        self.complete_current_test = self._test_name_for_id(test_case_id)
                        self._set_complete_runtime_summary()
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.complete_completed_count = min(
                            self.complete_completed_count + 1,
                            self.complete_total_count,
                        )
                        self._set_complete_progress(
                            f"{self.complete_completed_count} of {self.complete_total_count} completed"
                        )
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                        self._set_skip_button_enabled(test_case_id, False)
                        self._set_complete_runtime_summary()
                elif self.active_mode and self.active_mode.startswith("selected:"):
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._disable_row_skip_buttons()
                        self._set_skip_button_enabled(test_case_id, True)
                        self._set_selected_progress(test_case_id)
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.selected_completed_count = min(
                            self.selected_completed_count + 1,
                            self.selected_total_count,
                        )
                        self._set_selected_progress()
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                        self._set_skip_button_enabled(test_case_id, False)
            elif event_type == "phase_status":
                _, phase, status = event
                self._handle_phase_status(phase, status)
            elif event_type == "manual_confirmation_pause":
                _, result = event
                self._handle_manual_confirmation_pause(result)
            elif event_type == "master_complete":
                _, result = event
                self._handle_master_result(result)
            elif event_type == "shakedown_complete":
                _, result = event
                self._handle_shakedown_result(result)
            elif event_type == "complete_testing_complete":
                if len(event) >= 3:
                    _, desktop_name, result = event
                else:
                    _, result = event
                    desktop_name = self._normalized_desktop_name()
                self._handle_complete_testing_result(desktop_name, result)
            elif event_type == "post_complete_zscaler_complete":
                _, result = event
                self._handle_post_complete_zscaler_result(result)
            elif event_type == "selected_complete":
                _, section_title, status, failed_count, *manual_check = event
                manual_check_message = manual_check[0] if manual_check else None
                self._handle_selected_result(section_title, status, failed_count, manual_check_message)
            elif event_type == "report_refresh_complete":
                _, desktop_name, report_path = event
                self._handle_report_refresh_complete(desktop_name, report_path)
            elif event_type == "report_refresh_failed":
                _, error_message = event
                self._handle_report_refresh_failed(error_message)
            elif event_type == "build_doc_complete":
                _, desktop_name, report_path = event
                self._handle_build_doc_complete(desktop_name, report_path)
            elif event_type == "build_doc_failed":
                _, error_message = event
                self._handle_build_doc_failed(error_message)
            elif event_type == "manifest_refresh_complete":
                _, desktop_name, manifest_path = event
                self._handle_manifest_refresh_complete(desktop_name, manifest_path)
            elif event_type == "manifest_refresh_failed":
                _, error_message = event
                self._handle_manifest_refresh_failed(error_message)
            elif event_type == "preflight_complete":
                _, result = event
                self._handle_preflight_complete(result)
            elif event_type == "preflight_failed":
                _, error_message = event
                self._handle_preflight_failed(error_message)
            elif event_type == "evidence_audit_complete":
                _, result = event
                self._handle_evidence_audit_complete(result)
            elif event_type == "evidence_audit_failed":
                _, error_message = event
                self._handle_evidence_audit_failed(error_message)
            elif event_type == "support_bundle_complete":
                _, desktop_name, bundle_path = event
                self._handle_support_bundle_complete(desktop_name, bundle_path)
            elif event_type == "support_bundle_failed":
                _, error_message = event
                self._handle_support_bundle_failed(error_message)
            processed += 1

        delay_ms = 1 if not self.events.empty() else 150
        self.after(delay_ms, self._process_events)

    def _watchdog_tick(self) -> None:
        try:
            if self.active_mode is not None:
                threshold = float(self.config.raw.get("ui", {}).get("watchdog_warning_sec", 180))
                now = time.monotonic()
                idle_seconds = now - self._last_progress_monotonic
                if threshold > 0 and idle_seconds >= threshold and now - self._watchdog_last_warning_monotonic >= threshold:
                    self._watchdog_last_warning_monotonic = now
                    self._append_message(
                        "Watchdog: no automation progress update for "
                        f"{self._format_duration(idle_seconds)}. The UI is still responsive; "
                        "Citrix or validation may be waiting."
                    )
        finally:
            self.after(1000, self._watchdog_tick)

    def _handle_manual_confirmation_pause(self, result: ExecutionResult) -> None:
        screenshot_path = result.manual_confirmation_screenshot or result.screenshot_path
        message = (
            result.manual_confirmation_message
            or "Hostname/IP evidence needs manual confirmation before continuing."
        )
        self._append_message(message)
        if self.active_pause_event is not None and self.active_pause_event.is_set():
            self.active_paused = True
            if self.active_mode == "complete":
                self.complete_current_phase = "Manual hostname check"
                self.complete_current_test = "Hostname_and_IP_Evidence"
                self._set_complete_status("Paused")
                self._set_complete_runtime_summary()
            elif self.active_mode == "master":
                self._set_master_status("Paused")
            elif self.active_mode == "shakedown":
                self._set_shakedown_status("Paused")
            elif self.active_mode and self.active_mode.startswith("selected:"):
                if self.active_selected_section == "Mandatory Testcases":
                    self._set_master_status("Paused")
                elif self.active_selected_section == "Shakedown Testcases":
                    self._set_shakedown_status("Paused")
            self._refresh_pause_button_text()

        opened_target = None
        if screenshot_path is not None and screenshot_path.exists():
            folder = screenshot_path.parent
            opened_target = folder
            try:
                subprocess.Popen(["explorer.exe", str(folder)])
                self._append_message(f"Opened Hostname/IP evidence folder: {folder}")
            except OSError as exc:
                self._append_message(f"Unable to open Hostname/IP evidence folder: {exc}")
        elif screenshot_path is not None:
            folder = screenshot_path.parent
            opened_target = folder
            if folder.exists():
                try:
                    subprocess.Popen(["explorer.exe", str(folder)])
                    self._append_message(f"Opened Hostname/IP evidence folder: {folder}")
                except OSError as exc:
                    self._append_message(f"Unable to open Hostname/IP evidence folder: {exc}")

        if self.active_pause_event is not None and self.active_pause_event.is_set() and self.active_mode == "complete":
            pause_text = (
                "\n\nComplete Testing is paused. Verify the opened evidence screenshot, "
                "then return here and click Resume to continue from the next testcase."
            )
        elif self.active_pause_event is not None and self.active_pause_event.is_set():
            pause_text = "\n\nVerify the opened evidence screenshot, then click Resume to continue."
        else:
            pause_text = "\n\nVerify the opened evidence screenshot."
        target_text = f"\n\nEvidence:\n{opened_target}" if opened_target is not None else ""
        messagebox.showinfo(
            "Hostname Evidence Ready",
            f"{message}{target_text}{pause_text}",
        )

    def _handle_result(self, test_case: TestCase, result: ExecutionResult) -> None:
        duration_seconds = self._elapsed_seconds_from(self.active_sequence_started_monotonic)
        self._set_status(test_case.id, result.status)
        self._track_test_status(test_case.id, result.status)
        self._set_run_progress(
            title="Individual Testcase",
            completed=1 if result.status != "Stopped" else 0,
            total=1,
            status=result.status,
            current=test_case.name,
            next_item="None",
            remaining=0 if result.status != "Stopped" else 1,
            elapsed_seconds=0,
        )
        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{result.test_case_name}: {result.status}")
        self._append_message(f"Log: {result.log_path}")
        if result.screenshot_path:
            self._append_message(f"Screenshot: {result.screenshot_path}")
        desktop_name = self._normalized_desktop_name()
        self._refresh_run_manifest_async(desktop_name)
        if result.status == "Stopped":
            messagebox.showinfo("Test Stopped", f"{result.test_case_name} was stopped.")
        elif result.status == "Skipped":
            self._append_message(f"{result.test_case_name} skipped.")
            self._record_successful_desktop_name(desktop_name)
        elif result.manual_confirmation_required:
            self._handle_manual_confirmation_pause(result)
            self._record_successful_desktop_name(desktop_name)
        elif result.requires_manual_check:
            messagebox.showwarning(
                "Manual Check Required",
                (
                    result.manual_check_message
                    or f"{result.test_case_name} needs manual review before continuing."
                )
                + f"\n\nLog:\n{result.log_path}",
            )
        elif result.error_message:
            messagebox.showerror("Test Failed", f"{result.test_case_name} failed:\n\n{result.error_message}")
        else:
            self._record_successful_desktop_name(desktop_name)
            self._regenerate_latest_report_after_single_rerun(desktop_name)
            self._show_completion_notification(
                "Evidence Completed",
                f"{result.test_case_name} completed successfully.",
                desktop_name,
                evidence_category=evidence_category_for_test_name(result.test_case_name),
                passed_count=1,
                failed_count=0,
                duration_seconds=duration_seconds,
            )

    def _handle_master_result(self, result: MasterExecutionResult) -> None:
        self._set_master_status(result.status)
        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Mandatory Testcases: {result.status}")
        self._append_message(f"Master log: {result.log_path}")
        desktop_name = self._normalized_desktop_name()
        self._refresh_run_manifest_async(desktop_name)
        if result.status == "Stopped":
            self._set_master_progress(f"Stopped at {self.master_completed_count} of {self.master_total_count}")
            messagebox.showinfo("Mandatory Testcases Stopped", f"Mandatory Testcases were stopped.\n\nMaster log:\n{result.log_path}")
        elif result.manual_check_required:
            self._set_master_progress(f"{self.master_completed_count} of {self.master_total_count} completed")
            messagebox.showwarning(
                "Manual Check Required",
                (
                    result.manual_check_message
                    or "Hostname_and_IP_Evidence needs manual review before continuing."
                )
                + f"\n\nMandatory execution has stopped.\n\nMaster log:\n{result.log_path}",
            )
        elif result.status == "Pass":
            self._set_master_progress(f"{self.master_total_count} of {self.master_total_count} completed")
            self._record_successful_desktop_name(desktop_name)
            self._regenerate_latest_report_after_single_rerun(desktop_name)
            self._show_completion_notification(
                "Mandatory Evidence Completed",
                "Mandatory evidence execution completed successfully.",
                desktop_name,
                evidence_category=MANDATORY_EVIDENCE_FOLDER,
                passed_count=result.passed_count,
                failed_count=result.failed_count,
                duration_seconds=result.duration_seconds,
            )
        else:
            self._set_master_progress(f"{self.master_completed_count} of {self.master_total_count} completed")
            self._regenerate_latest_report_after_single_rerun(desktop_name)
            messagebox.showerror(
                "Mandatory Testcases Finished With Failures",
                (
                    f"Passed: {result.passed_count}\n"
                    f"Failed: {result.failed_count}\n"
                    f"Time taken: {self._format_duration(result.duration_seconds)}\n\n"
                    f"Master log:\n{result.log_path}"
                ),
            )

    def _handle_shakedown_result(self, result: MasterExecutionResult) -> None:
        self._set_shakedown_status(result.status)
        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Shakedown Testcases: {result.status}")
        self._append_message(f"Shakedown master log: {result.log_path}")
        desktop_name = self._normalized_desktop_name()
        self._refresh_run_manifest_async(desktop_name)
        if result.status == "Stopped":
            self._set_shakedown_progress(f"Stopped at {self.shakedown_completed_count} of {self.shakedown_total_count}")
            messagebox.showinfo("Shakedown Testcases Stopped", f"Shakedown Testcases were stopped.\n\nMaster log:\n{result.log_path}")
        else:
            self._set_shakedown_progress(f"{self.shakedown_completed_count} of {self.shakedown_total_count} completed")
            if result.status == "Pass":
                self._record_successful_desktop_name(desktop_name)
            self._regenerate_latest_report_after_single_rerun(desktop_name)
            self._show_completion_notification(
                "Shakedown Testcases Completed",
                (
                    "Shakedown testcases execution completed."
                    if result.status == "Pass"
                    else f"Shakedown testcases execution completed with {result.failed_count} failure(s)."
                ),
                desktop_name,
                evidence_category=SHAKEDOWN_EVIDENCE_FOLDER,
                passed_count=result.passed_count,
                failed_count=result.failed_count,
                duration_seconds=result.duration_seconds,
            )

    def _handle_complete_testing_result(self, desktop_name: str, result: CompleteExecutionResult) -> None:
        if self.active_mode == "scheduled_complete":
            self._handle_scheduled_complete_testing_result(desktop_name, result)
            return

        self._set_complete_status(result.status)
        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Perform Complete Testing: {result.status}")
        self._append_message(f"Complete Testing log: {result.log_path}")
        self.latest_report_path = result.report_path
        self._refresh_run_manifest_async(desktop_name)
        if result.status == "Stopped":
            self.complete_started_monotonic = None
            self.complete_current_phase = "Stopped"
            self.complete_current_test = "None"
            self._set_complete_runtime_summary()
            self._set_complete_progress(f"Stopped at {self.complete_completed_count} of {self.complete_total_count}")
            messagebox.showinfo("Complete Testing Stopped", f"Complete Testing was stopped.\n\nMaster log:\n{result.log_path}")
            return

        self._set_complete_progress(f"{self.complete_completed_count} of {self.complete_total_count} completed")
        self.complete_started_monotonic = None
        self.complete_current_phase = "Manual Check Required" if result.manual_check_required else result.status
        self.complete_current_test = "Hostname_and_IP_Evidence" if result.manual_check_required else "Finished"
        self._set_complete_runtime_summary()
        if result.manual_check_required:
            messagebox.showwarning(
                "Manual Check Required",
                (
                    result.manual_check_message
                    or "Hostname_and_IP_Evidence needs manual review before continuing."
                )
                + f"\n\nComplete Testing stopped before the next testcase.\n\nMaster log:\n{result.log_path}",
            )
            return
        if result.status == "Pass":
            self._record_successful_desktop_name(desktop_name)
        self._show_complete_testing_notification(desktop_name, result)

    def _handle_scheduled_complete_testing_result(self, desktop_name: str, result: CompleteExecutionResult) -> None:
        self._set_complete_status(result.status)
        self._append_message(
            f"Scheduled Complete Testing {self.scheduled_index + 1} of {len(self.scheduled_desktops)} completed: "
            f"{desktop_name}: {result.status}"
        )
        self._append_message(f"Complete Testing log: {result.log_path}")
        self.latest_report_path = result.report_path
        self._refresh_run_manifest_async(desktop_name)
        if result.status == "Pass":
            self._record_successful_desktop_name(desktop_name)

        self.scheduled_results.append(
            {
                "desktop_name": desktop_name,
                "status": result.status,
                "mandatory_status": result.mandatory_status,
                "shakedown_status": result.shakedown_status,
                "iat_status": result.iat_status,
                "silo43_status": getattr(result, "silo43_status", "Skipped"),
                "passed_count": result.passed_count,
                "failed_count": max(result.total_count - result.passed_count, 0),
                "total_count": result.total_count,
                "duration_seconds": result.duration_seconds,
                "report_path": str(result.report_path) if result.report_path else "",
                "log_path": str(result.log_path) if result.log_path else "",
                "manual_check_required": result.manual_check_required,
                "manual_check_message": result.manual_check_message,
            }
        )
        self._update_schedule_status_panel()

        stopped = result.status == "Stopped" or (self.active_stop_event is not None and self.active_stop_event.is_set())
        manual_check_required = bool(result.manual_check_required)
        has_more = self.scheduled_index + 1 < len(self.scheduled_desktops)
        if has_more and not stopped and not manual_check_required:
            self.scheduled_index += 1
            next_desktop = self.scheduled_desktops[self.scheduled_index]
            self.scheduled_waiting_desktop = next_desktop
            self._append_message(
                f"Scheduled Complete Testing paused before {self.scheduled_index + 1} of {len(self.scheduled_desktops)}: "
                f"{next_desktop}. Log in or scan QR, then continue."
            )
            self._set_complete_status("Paused")
            self.complete_current_phase = "Waiting for next desktop"
            self.complete_current_test = next_desktop
            self._set_complete_runtime_summary()
            self._set_complete_progress(f"{len(self.scheduled_results)} of {len(self.scheduled_desktops)} desktop(s) completed")
            self._set_complete_pause_enabled(False)
            self._set_complete_skip_enabled(False)
            self._update_schedule_status_panel()
            self.after(250, lambda: self._show_scheduled_next_desktop_prompt(next_desktop))
            return

        self._finish_scheduled_complete_testing(stopped=stopped, manual_check_required=manual_check_required)

    def _scheduled_skip_result(self, desktop_name: str) -> dict[str, object]:
        return {
            "desktop_name": desktop_name,
            "status": "Skipped",
            "mandatory_status": "Skipped",
            "shakedown_status": "Skipped",
            "iat_status": "Skipped",
            "silo43_status": "Skipped",
            "passed_count": 0,
            "failed_count": 0,
            "total_count": 0,
            "duration_seconds": 0.0,
            "report_path": "",
            "log_path": "",
            "manual_check_required": False,
            "manual_check_message": "Skipped from scheduled queue before execution.",
        }

    def _finish_scheduled_complete_testing(
        self,
        stopped: bool = False,
        manual_check_required: bool = False,
    ) -> None:
        if stopped:
            final_status = "Stopped"
        elif manual_check_required or any(str(item.get("status") or "") == "Fail" for item in self.scheduled_results):
            final_status = "Fail"
        elif self.scheduled_results and all(str(item.get("status") or "") == "Skipped" for item in self.scheduled_results):
            final_status = "Skipped"
        else:
            final_status = "Pass"
        self._set_complete_status(final_status)
        self.scheduled_waiting_desktop = None
        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self.complete_started_monotonic = None
        self.complete_current_phase = "Stopped" if stopped else "Manual Check Required" if manual_check_required else "Finished"
        self.complete_current_test = "None"
        self._set_complete_runtime_summary()
        self._set_complete_progress(f"{len(self.scheduled_results)} of {len(self.scheduled_desktops)} desktop(s) completed")
        self._update_schedule_status_panel()
        self._show_scheduled_complete_notification(
            self.scheduled_results,
            stopped=stopped,
            manual_check_required=manual_check_required,
        )

    def _show_scheduled_next_desktop_prompt(self, desktop_name: str) -> None:
        if not self.scheduled_desktops or self.scheduled_waiting_desktop != desktop_name:
            return

        modal = ctk.CTkToplevel(self)
        modal.title("Prepare Next Desktop")
        modal.configure(fg_color=THEME["bg"])
        self._configure_schedule_popup(modal)
        modal.resizable(False, False)

        def stop_from_close() -> None:
            self._stop_scheduled_queue_from_prompt(modal, desktop_name)

        modal.protocol("WM_DELETE_WINDOW", stop_from_close)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        ctk.CTkLabel(
            card,
            text="Prepare Next Desktop",
            text_color=THEME["text"],
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(
            card,
            text=(
                f"The runner is paused before desktop {self.scheduled_index + 1} of {len(self.scheduled_desktops)}.\n"
                f"Log in to this Citrix desktop, complete QR/password verification if prompted, and keep the Desktop Viewer open."
            ),
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=590,
        ).pack(anchor=tk.W, padx=18, pady=(8, 0))

        desktop_card = ctk.CTkFrame(card, fg_color=THEME["card_soft"], corner_radius=12, border_width=1, border_color=THEME["border"])
        desktop_card.pack(fill=tk.X, padx=18, pady=(14, 0))
        ctk.CTkLabel(
            desktop_card,
            text=desktop_name,
            text_color=THEME["text"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor=tk.W, padx=14, pady=(12, 2))
        ctk.CTkLabel(
            desktop_card,
            text="Click Continue only after this exact Desktop Viewer is visible and ready.",
            text_color=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, padx=14, pady=(0, 12))

        notice = ctk.CTkLabel(
            card,
            text="Waiting for your confirmation.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        notice.pack(anchor=tk.W, padx=18, pady=(12, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(16, 16))
        ModernButton(
            actions,
            text="Continue",
            variant="primary",
            command=lambda: self._continue_scheduled_desktop(modal, desktop_name, notice),
            height=34,
            min_width=120,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ModernButton(
            actions,
            text="Skip Desktop",
            variant="secondary",
            command=lambda: self._skip_scheduled_waiting_desktop(modal, desktop_name),
            height=34,
            min_width=120,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ModernButton(
            actions,
            text="Stop Schedule",
            variant="danger",
            command=lambda: self._stop_scheduled_queue_from_prompt(modal, desktop_name),
            height=34,
            min_width=125,
        ).pack(side=tk.RIGHT)

        modal.update_idletasks()
        width = 680
        height = min(max(modal.winfo_reqheight(), 310), 390)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _continue_scheduled_desktop(self, modal: ctk.CTkToplevel, desktop_name: str, notice: ctk.CTkLabel) -> None:
        if self.scheduled_waiting_desktop != desktop_name:
            notice.configure(text="This queued desktop is no longer waiting.", text_color=THEME["danger"])
            return
        notice.configure(text="Checking Desktop Viewer window...", text_color=THEME["muted"])
        modal.update_idletasks()
        if not self._confirm_scheduled_preflight([desktop_name], parent=modal, check_all_windows=True):
            notice.configure(
                text="Desktop is still not ready. Log in or activate it, then click Continue again.",
                text_color=THEME["danger"],
            )
            return
        if self.active_stop_event is not None and self.active_stop_event.is_set():
            modal.destroy()
            self._finish_scheduled_complete_testing(stopped=True)
            return

        stop_event = self.active_stop_event or Event()
        pause_event = self.active_pause_event or Event()
        skip_event = self.active_skip_event or Event()
        pause_event.clear()
        skip_event.clear()
        self.scheduled_waiting_desktop = None
        self._append_message(
            f"Scheduled Complete Testing continuing with {self.scheduled_index + 1} of {len(self.scheduled_desktops)}: "
            f"{desktop_name}"
        )
        self._update_schedule_status_panel()
        modal.destroy()
        self.after(
            250,
            lambda: self._start_complete_testing_for_desktop(
                desktop_name,
                scheduled=True,
                stop_event=stop_event,
                pause_event=pause_event,
                skip_event=skip_event,
            ),
        )

    def _skip_scheduled_waiting_desktop(self, modal: ctk.CTkToplevel, desktop_name: str) -> None:
        if self.scheduled_waiting_desktop != desktop_name:
            modal.destroy()
            return
        self.scheduled_results.append(self._scheduled_skip_result(desktop_name))
        self._append_message(f"Scheduled Complete Testing skipped desktop: {desktop_name}")

        if self.scheduled_index + 1 < len(self.scheduled_desktops):
            self.scheduled_index += 1
            next_desktop = self.scheduled_desktops[self.scheduled_index]
            self.scheduled_waiting_desktop = next_desktop
            self.complete_current_phase = "Waiting for next desktop"
            self.complete_current_test = next_desktop
            self._set_complete_runtime_summary()
            self._set_complete_progress(f"{len(self.scheduled_results)} of {len(self.scheduled_desktops)} desktop(s) completed")
            self._update_schedule_status_panel()
            modal.destroy()
            self.after(250, lambda: self._show_scheduled_next_desktop_prompt(next_desktop))
            return

        modal.destroy()
        self._finish_scheduled_complete_testing()

    def _stop_scheduled_queue_from_prompt(self, modal: ctk.CTkToplevel, desktop_name: str) -> None:
        if self.active_stop_event is not None:
            self.active_stop_event.set()
        self._append_message(f"Scheduled Complete Testing stopped before desktop: {desktop_name}")
        modal.destroy()
        self._finish_scheduled_complete_testing(stopped=True)

    def _handle_post_complete_zscaler_result(self, result: dict[str, object]) -> None:
        status = str(result.get("status") or "Fail")
        duration_seconds = self._elapsed_seconds_from(self.active_sequence_started_monotonic)
        self._track_test_status(POST_COMPLETE_ZSCALER_TEST_NAME, status)
        self._set_run_progress(
            title="Post-complete ZScaler",
            completed=1 if status != "Stopped" else 0,
            total=1,
            status=status,
            current=POST_COMPLETE_ZSCALER_TEST_NAME,
            next_item="None",
            remaining=0 if status != "Stopped" else 1,
            elapsed_seconds=duration_seconds,
        )
        self._finish_run_tracking()
        self._set_complete_status("Pass" if status == "Pass" and not self.last_failed_test_case_ids else status)
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{POST_COMPLETE_ZSCALER_TEST_NAME}: {status}")
        if result.get("log_path"):
            self._append_message(f"Post-complete ZScaler recovery log: {result.get('log_path')}")
        screenshots = result.get("screenshots")
        if isinstance(screenshots, list) and screenshots:
            self._append_message(f"Post-complete ZScaler screenshot: {screenshots[-1]}")

        desktop_name = self._normalized_desktop_name()
        self._refresh_run_manifest_async(desktop_name)
        if status == "Stopped":
            messagebox.showinfo("Post-complete ZScaler Stopped", "Post-complete ZScaler recovery was stopped.")
            return
        if status == "Pass":
            self._record_successful_desktop_name(desktop_name)
            self._regenerate_latest_report_after_single_rerun(desktop_name)
            self._show_completion_notification(
                "Post-complete ZScaler Recovered",
                "Post-complete ZScaler evidence completed successfully.",
                desktop_name,
                evidence_category=MANDATORY_EVIDENCE_FOLDER,
                passed_count=1,
                failed_count=0,
                duration_seconds=duration_seconds,
            )
            return

        self._regenerate_latest_report_after_single_rerun(desktop_name)
        messagebox.showerror(
            "Post-complete ZScaler Failed",
            f"Post-complete ZScaler evidence still failed after retry.\n\n{result.get('error') or ''}".strip(),
        )

    def _handle_selected_result(
        self,
        section_title: str,
        status: str,
        failed_count: int,
        manual_check_message: str | None = None,
    ) -> None:
        duration_seconds = self._elapsed_seconds_from(self.active_sequence_started_monotonic)
        passed_count = max(self.selected_completed_count - failed_count, 0)
        if section_title == "Mandatory Testcases":
            self._set_master_status(status)
            self._set_master_progress(
                f"{self.selected_completed_count} of {self.selected_total_count} selected completed"
            )
        elif section_title == "Shakedown Testcases":
            self._set_shakedown_status(status)
            self._set_shakedown_progress(
                f"{self.selected_completed_count} of {self.selected_total_count} selected completed"
            )
        else:
            self._set_selected_progress()
        self._set_sequence_progress_panel(
            section_title,
            self.selected_completed_count,
            self.selected_total_count,
            status,
            None,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )

        self._finish_run_tracking()
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{section_title} selected run: {status}")

        desktop_name = self._normalized_desktop_name()
        self._refresh_run_manifest_async(desktop_name)
        if status == "Stopped":
            messagebox.showinfo("Selected Run Stopped", f"{section_title} selected run was stopped.")
            return
        if manual_check_message:
            messagebox.showwarning(
                "Manual Check Required",
                f"{manual_check_message}\n\nSelected run stopped before the next testcase.",
            )
            return

        if status == "Pass":
            self._record_successful_desktop_name(desktop_name)
        self._regenerate_latest_report_after_single_rerun(desktop_name)

        evidence_category = None
        if section_title == "Mandatory Testcases":
            evidence_category = MANDATORY_EVIDENCE_FOLDER
        elif section_title == "Shakedown Testcases":
            evidence_category = SHAKEDOWN_EVIDENCE_FOLDER
        elif section_title == "IAT Testcase":
            evidence_category = IAT_EVIDENCE_FOLDER
        elif section_title == "Silo 43 Testcases":
            evidence_category = SILO43_EVIDENCE_FOLDER

        self._show_completion_notification(
            "Selected Testcases Completed",
            (
                f"{section_title} selected testcases completed successfully."
                if status == "Pass"
                else f"{section_title} selected run completed with {failed_count} failure(s)."
            ),
            desktop_name,
            evidence_category=evidence_category,
            passed_count=passed_count,
            failed_count=failed_count,
            duration_seconds=duration_seconds,
        )

    def _handle_phase_status(self, phase: str, status: str) -> None:
        if phase == "mandatory":
            self._set_master_status(status)
            if status == "Running":
                self._set_master_progress("Running")
                self.complete_current_phase = "Mandatory"
                self.complete_current_test = "Starting mandatory sequence"
            elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                self._set_master_progress(status)
                self.complete_current_phase = f"Mandatory {status}"
                self.complete_current_test = "Mandatory sequence finished"
        elif phase == "shakedown":
            self._set_shakedown_status(status)
            if status == "Running":
                self._set_shakedown_progress("Running")
                self.complete_current_phase = "Shakedown"
                self.complete_current_test = "Starting shakedown sequence"
            elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                self._set_shakedown_progress(status)
                self.complete_current_phase = f"Shakedown {status}"
                self.complete_current_test = "Shakedown sequence finished"
        elif phase == "silo43":
            self._append_message(f"Silo 43 phase: {status}")
            if status == "Running":
                self.complete_current_phase = "Silo 43"
                self.complete_current_test = "Starting Silo 43 sequence"
            elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                self.complete_current_phase = f"Silo 43 {status}"
                self.complete_current_test = "Silo 43 sequence finished"
        elif phase == "iat":
            self._append_message(f"IAT phase: {status}")
            if status == "Running":
                self.complete_current_phase = "IAT"
                self.complete_current_test = "Starting IAT sequence"
            elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                self.complete_current_phase = f"IAT {status}"
                self.complete_current_test = "IAT sequence finished"
        elif phase == "post_complete":
            self._append_message(f"Post-complete evidence phase: {status}")
            self._track_test_status(POST_COMPLETE_ZSCALER_TEST_NAME, status)
            self.complete_current_phase = "Post-complete Evidence" if status == "Running" else f"Post-complete Evidence {status}"
            self.complete_current_test = "ZScaler Services second screenshot"
            if status in {"Pass", "Fail", "Skipped", "Stopped"} and self.active_mode in {"complete", "scheduled_complete"}:
                self.complete_completed_count = min(self.complete_completed_count + 1, self.complete_total_count)
                self._set_complete_progress(f"{self.complete_completed_count} of {self.complete_total_count} completed")
        if self.active_mode in {"complete", "scheduled_complete"}:
            self._set_complete_runtime_summary()

    def _set_status(self, test_case_id: str, status: str) -> None:
        label = self.status_labels[test_case_id]
        label.configure(text=status)
        self._set_test_card_state(test_case_id, status)
    def _badge_text(self, badge: StatusBadge | None) -> str:
        return getattr(badge, "text", "Idle") if badge is not None else "Idle"

    def _set_progress_panel_idle(self) -> None:
        self._set_run_progress(
            title="Ready",
            completed=0,
            total=0,
            status="Idle",
            current="None",
            next_item="None",
            remaining=0,
            elapsed_seconds=0,
        )

    def _set_run_progress(
        self,
        title: str,
        completed: int,
        total: int,
        status: str,
        current: str = "None",
        next_item: str = "None",
        remaining: int = 0,
        elapsed_seconds: int = 0,
    ) -> None:
        if self.progress_panel is not None:
            self.progress_panel.set_progress(
                title=title,
                completed=completed,
                total=total,
                status=status,
                current=current,
                next_item=next_item,
                remaining=remaining,
                elapsed_seconds=elapsed_seconds,
            )

    def _set_sequence_progress_panel(
        self,
        title: str,
        completed: int,
        total: int,
        status: str,
        current_test_id: str | None,
        sequence_ids: list[str],
        started_at: float | None,
    ) -> None:
        current_text, next_text, remaining = self._sequence_progress_details(
            completed,
            total,
            current_test_id,
            sequence_ids,
        )
        elapsed = int(time.monotonic() - started_at) if started_at is not None else 0
        self._set_run_progress(
            title=title,
            completed=completed,
            total=total,
            status=status,
            current=current_text,
            next_item=next_text,
            remaining=remaining,
            elapsed_seconds=elapsed,
        )

    def _set_complete_progress_panel(self, status: str) -> None:
        elapsed = 0
        if self.complete_started_monotonic is not None:
            elapsed = int(time.monotonic() - self.complete_started_monotonic)
        self._set_run_progress(
            title="Complete Testing",
            completed=self.complete_completed_count,
            total=self.complete_total_count,
            status=status,
            current=self.complete_current_test,
            next_item=f"Phase: {self.complete_current_phase}",
            remaining=max(self.complete_total_count - self.complete_completed_count, 0),
            elapsed_seconds=elapsed,
        )

    def _set_master_status(self, status: str) -> None:
        if self.master_status_label is not None:
            self.master_status_label.configure(text=status)
        selected = self.active_mode is not None and self.active_mode.startswith("selected:") and self.selected_section_title == "Mandatory Testcases"
        self._set_sequence_progress_panel(
            "Selected Mandatory Testcases" if selected else "Mandatory Testcases",
            self.selected_completed_count if selected else self.master_completed_count,
            self.selected_total_count if selected else self.master_total_count,
            status,
            self.active_test_id if self.active_mode in {"master"} or selected else None,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )
        if self.master_card is not None:
            self._set_frame_tree_bg(self.master_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_master_progress(self, text: str) -> None:
        if self.master_progress_label is not None:
            self.master_progress_label.configure(text=text)
        selected = self.active_mode is not None and self.active_mode.startswith("selected:") and self.selected_section_title == "Mandatory Testcases"
        self._set_sequence_progress_panel(
            "Selected Mandatory Testcases" if selected else "Mandatory Testcases",
            self.selected_completed_count if selected else self.master_completed_count,
            self.selected_total_count if selected else self.master_total_count,
            self._badge_text(self.master_status_label),
            self.active_test_id if self.active_mode in {"master"} or selected else None,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )

    def _set_shakedown_status(self, status: str) -> None:
        if self.shakedown_status_label is not None:
            self.shakedown_status_label.configure(text=status)
        selected = self.active_mode is not None and self.active_mode.startswith("selected:") and self.selected_section_title == "Shakedown Testcases"
        self._set_sequence_progress_panel(
            "Selected Shakedown Testcases" if selected else "Shakedown Testcases",
            self.selected_completed_count if selected else self.shakedown_completed_count,
            self.selected_total_count if selected else self.shakedown_total_count,
            status,
            self.active_test_id if self.active_mode in {"shakedown"} or selected else None,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )
        if self.shakedown_card is not None:
            self._set_frame_tree_bg(self.shakedown_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_shakedown_progress(self, text: str) -> None:
        if self.shakedown_progress_label is not None:
            self.shakedown_progress_label.configure(text=text)
        selected = self.active_mode is not None and self.active_mode.startswith("selected:") and self.selected_section_title == "Shakedown Testcases"
        self._set_sequence_progress_panel(
            "Selected Shakedown Testcases" if selected else "Shakedown Testcases",
            self.selected_completed_count if selected else self.shakedown_completed_count,
            self.selected_total_count if selected else self.shakedown_total_count,
            self._badge_text(self.shakedown_status_label),
            self.active_test_id if self.active_mode in {"shakedown"} or selected else None,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )

    def _set_selected_progress(self, current_test_id: str | None = None) -> None:
        text = self._sequence_progress_text(
            self.selected_completed_count,
            self.selected_total_count,
            current_test_id,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
            selected=True,
        )
        if self.selected_section_title == "Mandatory Testcases":
            self._set_master_progress(text)
        elif self.selected_section_title == "Shakedown Testcases":
            self._set_shakedown_progress(text)
        if self.selected_progress_label is not None:
            self.selected_progress_label.configure(text=text)
        selected_status = "Paused" if self.active_paused else "Running"
        if not (self.active_mode and self.active_mode.startswith("selected:")):
            selected_status = "Idle" if self.selected_completed_count == 0 else "Pass"
        self._set_sequence_progress_panel(
            self.selected_section_title or "Selected Testcases",
            self.selected_completed_count,
            self.selected_total_count,
            selected_status,
            current_test_id,
            self.active_sequence_ids,
            self.active_sequence_started_monotonic,
        )

    def _set_complete_status(self, status: str) -> None:
        if self.complete_status_label is not None:
            self.complete_status_label.configure(text=status)
        self._set_complete_progress_panel(status)
        if self.complete_card is not None:
            self._set_frame_tree_bg(self.complete_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_complete_progress(self, text: str) -> None:
        if self.complete_progress_label is not None:
            self.complete_progress_label.configure(text=text)
        self._set_complete_progress_panel(self._badge_text(self.complete_status_label))

    def _tick_complete_runtime(self) -> None:
        if self.active_mode not in {"complete", "scheduled_complete"} or self.complete_started_monotonic is None:
            self.complete_runtime_tick_active = False
            return
        self._set_complete_runtime_summary()
        self.after(1000, self._tick_complete_runtime)

    def _set_complete_runtime_summary(self) -> None:
        elapsed = 0
        if self.complete_started_monotonic is not None:
            elapsed = int(time.monotonic() - self.complete_started_monotonic)
        remaining = max(self.complete_total_count - self.complete_completed_count, 0)
        if self.complete_runtime_label is not None:
            self.complete_runtime_label.configure(
                text=(
                    f"Elapsed {_format_elapsed(elapsed)} | "
                    f"Phase {self.complete_current_phase} | "
                    f"Current {self.complete_current_test} | "
                    f"Remaining {remaining}"
                )
            )
        self._set_complete_progress_panel(self._badge_text(self.complete_status_label))

    def _tick_sequence_runtime(self) -> None:
        if self.active_mode not in {"master", "shakedown"} and not (
            self.active_mode and self.active_mode.startswith("selected:")
        ):
            return
        if self.active_mode == "master":
            self._set_master_progress(
                self._sequence_progress_text(
                    self.master_completed_count,
                    self.master_total_count,
                    self.active_test_id,
                    self.active_sequence_ids,
                    self.active_sequence_started_monotonic,
                )
            )
        elif self.active_mode == "shakedown":
            self._set_shakedown_progress(
                self._sequence_progress_text(
                    self.shakedown_completed_count,
                    self.shakedown_total_count,
                    self.active_test_id,
                    self.active_sequence_ids,
                    self.active_sequence_started_monotonic,
                )
            )
        elif self.active_mode and self.active_mode.startswith("selected:"):
            self._set_selected_progress(self.active_test_id)
        self.after(1000, self._tick_sequence_runtime)

    def _sequence_progress_text(
        self,
        completed: int,
        total: int,
        current_test_id: str | None,
        sequence_ids: list[str],
        started_at: float | None,
        selected: bool = False,
    ) -> str:
        elapsed = int(time.monotonic() - started_at) if started_at is not None else 0
        current_text, next_text, remaining = self._sequence_progress_details(
            completed,
            total,
            current_test_id,
            sequence_ids,
        )
        scope = "selected " if selected else ""
        return (
            f"{completed} of {total} {scope}completed | "
            f"Elapsed {_format_elapsed(elapsed)} | "
            f"Current {current_text} | "
            f"Next {next_text} | "
            f"Remaining {remaining}"
        )

    def _sequence_progress_details(
        self,
        completed: int,
        total: int,
        current_test_id: str | None,
        sequence_ids: list[str],
    ) -> tuple[str, str, int]:
        current_text = "None"
        next_text = "None"
        if current_test_id:
            current_text = self._test_name_for_id(current_test_id)
            try:
                current_index = sequence_ids.index(current_test_id)
            except ValueError:
                current_index = completed
            if current_index + 1 < len(sequence_ids):
                next_text = self._test_name_for_id(sequence_ids[current_index + 1])
            remaining = max(len(sequence_ids) - current_index - 1, 0)
        else:
            remaining = max(total - completed, 0)
            if completed < len(sequence_ids):
                next_text = self._test_name_for_id(sequence_ids[completed])
        return current_text, next_text, remaining

    def _test_name_for_id(self, test_case_id: str) -> str:
        if test_case_id == POST_COMPLETE_ZSCALER_TEST_NAME:
            return POST_COMPLETE_ZSCALER_TEST_NAME
        for test_case in self.test_cases:
            if test_case.id == test_case_id:
                return test_case.name
        return test_case_id

    def _test_ids_for_test_names(self, test_names: list[str]) -> list[str]:
        ids_by_name = {test_case.name: test_case.id for test_case in self.test_cases}
        return [ids_by_name[name] for name in test_names if name in ids_by_name]

    def _section_title_for_test_name(self, test_name: str | None) -> str:
        if test_name in MANDATORY_TEST_CASE_ORDER:
            return "Mandatory Testcases"
        if test_name == POST_COMPLETE_ZSCALER_TEST_NAME:
            return "Post-complete Evidence"
        if test_name in SHAKEDOWN_TEST_CASE_ORDER:
            return "Shakedown Testcases"
        if test_name in SILO43_TEST_CASE_ORDER:
            return "Silo 43 Testcases"
        return "IAT Testcase"

    def _desktop_entry_value(self) -> str:
        entry = getattr(self, "desktop_name_entry", None)
        if entry is not None:
            try:
                return entry.get()
            except tk.TclError:
                pass
        return self.desktop_name_var.get()

    def _set_desktop_entry_value(self, value: str) -> None:
        self.desktop_name_var.set(value)
        entry = getattr(self, "desktop_name_entry", None)
        if entry is None:
            return
        try:
            entry.delete(0, tk.END)
            entry.insert(0, value)
        except tk.TclError:
            pass

    def _focus_desktop_name_entry(self) -> None:
        entry = getattr(self, "desktop_name_entry", None)
        if entry is None:
            return
        try:
            if entry.winfo_exists():
                entry.focus_set()
        except tk.TclError:
            pass

    def _desktop_short_name(self, desktop_name: str) -> str:
        cleaned = " ".join(desktop_name.strip().split())
        if cleaned.casefold().endswith(DESKTOP_VIEWER_SUFFIX.casefold()):
            cleaned = cleaned[: -len(DESKTOP_VIEWER_SUFFIX)].strip()
        for known_name in KNOWN_DESKTOP_NAMES:
            if cleaned.casefold() == known_name.casefold():
                return known_name
        return cleaned

    def _normalized_desktop_name(self, desktop_name: str | None = None) -> str:
        raw_value = self._desktop_entry_value() if desktop_name is None else desktop_name
        short_name = self._desktop_short_name(raw_value)
        if not short_name:
            return ""
        return f"{short_name}{DESKTOP_VIEWER_SUFFIX}"

    def _block_non_silo43_testcase_run(self, desktop_name: str, test_names: list[str]) -> bool:
        silo43_test_names = [name for name in test_names if name in SILO43_TEST_CASE_ORDER]
        if not silo43_test_names or is_silo43_desktop(desktop_name):
            return False

        testcase_text = "\n".join(f"- {name}" for name in silo43_test_names)
        messagebox.showerror(
            "Silo 43 Desktop Required",
            (
                "Silo 43-specific testcases can only be run against a Silo43 desktop.\n\n"
                f"Current Citrix Desktop Name:\n{desktop_name}\n\n"
                "Blocked testcase(s):\n"
                f"{testcase_text}\n\n"
                "Please select a Silo43 desktop or remove these testcase(s) from the run."
            ),
        )
        self._append_message(
            "Silo 43 testcase run blocked because the selected Citrix Desktop Name is not a Silo43 desktop."
        )
        return True

    def _desktop_dropdown_values(self, history: list[str] | None = None) -> list[str]:
        values: list[str] = []
        for item in (history if history is not None else self.desktop_history.load()) + KNOWN_DESKTOP_NAMES:
            short_name = self._desktop_short_name(item)
            if short_name and short_name.casefold() not in {value.casefold() for value in values}:
                values.append(short_name)
        return values

    def _refresh_desktop_history_values(self, values: list[str] | None = None) -> None:
        if not hasattr(self, "desktop_name_entry"):
            return
        items = self._desktop_dropdown_values(values)
        self.desktop_suggestion_values = items
        if self._desktop_suggestion_popup_exists():
            self._render_desktop_suggestion_list(items)
        self._refresh_desktop_shortcuts()

    def _on_desktop_name_keyrelease(self, event: tk.Event) -> None:
        if event.keysym in {
            "Alt_L",
            "Alt_R",
            "Control_L",
            "Control_R",
            "Down",
            "End",
            "Escape",
            "Home",
            "Left",
            "Prior",
            "Return",
            "Right",
            "Shift_L",
            "Shift_R",
            "Tab",
            "Up",
        }:
            return

        self.desktop_name_var.set(self._desktop_entry_value())
        typed = self._desktop_entry_value().strip()
        suggestions = self._desktop_dropdown_values()
        if not typed:
            self._refresh_desktop_history_values()
            self._update_desktop_input_state()
            return

        typed_lower = typed.casefold()
        starts_with = [item for item in suggestions if item.casefold().startswith(typed_lower)]
        contains = [
            item
            for item in suggestions
            if typed_lower in item.casefold() and item not in starts_with
        ]
        matches = starts_with + contains
        self._refresh_desktop_history_values(matches)
        self._update_desktop_input_state()
        if matches:
            self._show_desktop_name_suggestions()
        else:
            self._close_desktop_name_suggestions()

    def _on_desktop_name_keypress(self, event: tk.Event) -> str | None:
        if event.keysym == "Down":
            if not self._desktop_suggestion_popup_visible():
                self._show_desktop_name_suggestions()
                return "break"
            self._move_desktop_suggestion(1)
            return "break"
        if event.keysym == "Up":
            if not self._desktop_suggestion_popup_visible():
                self._show_desktop_name_suggestions()
                return "break"
            self._move_desktop_suggestion(-1)
            return "break"
        if event.keysym == "Next":
            if not self._desktop_suggestion_popup_visible():
                self._show_desktop_name_suggestions()
            self._move_desktop_suggestion(5)
            return "break"
        if event.keysym == "Prior":
            if not self._desktop_suggestion_popup_visible():
                self._show_desktop_name_suggestions()
            self._move_desktop_suggestion(-5)
            return "break"
        if event.keysym == "Home" and self._desktop_suggestion_popup_visible():
            self._set_desktop_suggestion_index(0)
            return "break"
        if event.keysym == "End" and self._desktop_suggestion_popup_visible():
            self._set_desktop_suggestion_index(len(self.desktop_suggestion_values) - 1)
            return "break"
        if event.keysym == "Return":
            if self._desktop_suggestion_popup_visible():
                self._apply_selected_desktop_suggestion()
                return "break"
            return None
        if event.keysym == "Escape":
            self._close_desktop_name_suggestions()
            return "break"
        return None

    def _record_successful_desktop_name(self, desktop_name: str) -> None:
        if desktop_name:
            self._refresh_desktop_history_values(self.desktop_history.add(self._desktop_short_name(desktop_name)))

    def _refresh_desktop_shortcuts(self) -> None:
        frame = getattr(self, "desktop_shortcuts_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        shortcuts = self._desktop_dropdown_values()[:5]
        if not shortcuts:
            return
        ctk.CTkLabel(
            frame,
            text="Recent:",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        for desktop_name in shortcuts:
            ModernButton(
                frame,
                text=desktop_name,
                variant="ghost",
                height=22,
                min_width=max(86, min(len(desktop_name) * 7 + 18, 150)),
                font=("Segoe UI", 10, "bold"),
                command=lambda selected=desktop_name: self._select_desktop_shortcut(selected),
            ).pack(side=tk.LEFT, padx=(0, 6))

    def _select_desktop_shortcut(self, desktop_name: str) -> None:
        self._set_desktop_entry_value(desktop_name)
        self._close_desktop_name_suggestions()
        self._update_desktop_input_state()

    def _configure_independent_popup(self, popup: ctk.CTkToplevel) -> None:
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        popup.attributes("-toolwindow", False)
        popup.lift()
        popup.focus_force()

    def _configure_schedule_popup(self, popup: ctk.CTkToplevel) -> None:
        self._configure_independent_popup(popup)
        popup.transient(self)
        popup.grab_set()
        popup.attributes("-topmost", True)

        def release_topmost() -> None:
            try:
                if popup.winfo_exists():
                    popup.attributes("-topmost", False)
                    popup.lift()
                    popup.focus_force()
            except tk.TclError:
                pass

        popup.after(250, release_topmost)

    def show_openai_key_dialog(self) -> None:
        if self.active_stop_event is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before updating the AI key.")
            return

        settings = self.config.raw.get("ai_validation", {})
        modal = ctk.CTkToplevel(self)
        modal.title("OpenAI API Key")
        modal.geometry("570x390")
        modal.minsize(540, 360)
        self._configure_schedule_popup(modal)

        card = ctk.CTkFrame(
            modal,
            fg_color=THEME["card"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=14,
        )
        card.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            card,
            text="OpenAI API Key",
            text_color=THEME["text"],
            font=("Segoe UI", 20, "bold"),
            anchor=tk.W,
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 6))
        ctk.CTkLabel(
            card,
            text=(
                "Used only for AI fallback validation. The saved value is masked, stored under your "
                "Windows profile, and never displayed back in this app."
            ),
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            anchor=tk.W,
            wraplength=500,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 12))

        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 12))
        status_row.grid_columnconfigure(1, weight=1)

        status_pill = ctk.CTkLabel(
            status_row,
            text="Missing",
            fg_color=STATUS_BADGES["Skipped"][0],
            text_color=STATUS_BADGES["Skipped"][1],
            corner_radius=12,
            width=104,
            height=26,
            font=("Segoe UI", 10, "bold"),
        )
        status_pill.grid(row=0, column=0, sticky="w", padx=(0, 10))

        source_label = ctk.CTkLabel(
            status_row,
            text="No key configured",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            anchor=tk.W,
        )
        source_label.grid(row=0, column=1, sticky="ew")

        key_entry = ctk.CTkEntry(
            card,
            placeholder_text="Paste new OpenAI API key to save or test",
            show="*",
            height=40,
            fg_color=THEME["input"],
            border_color=THEME["border"],
            text_color=THEME["text"],
            placeholder_text_color=THEME["muted"],
            font=("Segoe UI", 11),
        )
        key_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 8))

        status_label = ctk.CTkLabel(
            card,
            text="",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=500,
        )
        status_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14))

        def refresh_status(message: str = "") -> None:
            status = get_openai_key_status(settings)
            if status.configured:
                source_text = {
                    "environment": "Configured from environment variable. It takes priority over saved local keys.",
                    "local": "Configured from saved local settings.",
                    "config": "Configured from app config.",
                }.get(status.source, "Configured.")
                status_pill.configure(
                    text="Configured",
                    fg_color=STATUS_BADGES["Pass"][0],
                    text_color=STATUS_BADGES["Pass"][1],
                )
                source_label.configure(text=source_text, text_color=STATUS_BADGES["Pass"][1])
                default_text = "The API key is available. The value is not shown."
            else:
                status_pill.configure(
                    text="Missing",
                    fg_color=STATUS_BADGES["Skipped"][0],
                    text_color=STATUS_BADGES["Skipped"][1],
                )
                source_label.configure(text="No key configured", text_color=STATUS_BADGES["Skipped"][1])
                default_text = "Paste a key, then click Save Key. You can also test before saving."
            text = message or default_text
            status_label.configure(
                text=text,
                text_color=STATUS_BADGES["Pass"][1] if status.configured else STATUS_BADGES["Skipped"][1],
            )

        def save_key() -> None:
            try:
                path = save_openai_api_key(key_entry.get())
            except Exception as exc:
                status_label.configure(text=f"Could not save key: {exc}", text_color=THEME["danger"])
                return
            key_entry.delete(0, tk.END)
            refresh_status(f"AI key saved successfully. Local file: {path}")

        def clear_key() -> None:
            try:
                path = clear_saved_openai_api_key()
            except Exception as exc:
                status_label.configure(text=f"Could not clear saved key: {exc}", text_color=THEME["danger"])
                return
            key_entry.delete(0, tk.END)
            refresh_status(f"Saved local key cleared. Local file: {path}")

        def set_testing_state(is_testing: bool) -> None:
            state = tk.DISABLED if is_testing else tk.NORMAL
            save_button.configure(state=state)
            test_button.configure(state=state)
            clear_button.configure(state=state)

        def test_key() -> None:
            candidate = key_entry.get().strip()
            status_label.configure(
                text="Testing key with OpenAI...",
                text_color=THEME["muted"],
            )
            set_testing_state(True)

            def worker() -> None:
                result = test_openai_api_key(settings, candidate or None)

                def apply_result() -> None:
                    try:
                        if not modal.winfo_exists():
                            return
                    except tk.TclError:
                        return
                    set_testing_state(False)
                    color = STATUS_BADGES["Pass"][1] if result.ok else THEME["danger"]
                    status_label.configure(text=result.message, text_color=color)

                try:
                    modal.after(0, apply_result)
                except tk.TclError:
                    pass

            threading.Thread(target=worker, daemon=True).start()

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=2, sticky="e", padx=18, pady=(0, 18))
        save_button = ModernButton(actions, text="Save Key", variant="primary", command=save_key, height=34, min_width=104)
        save_button.pack(
            side=tk.LEFT,
            padx=(0, 8),
        )
        test_button = ModernButton(actions, text="Test Key", variant="secondary", command=test_key, height=34, min_width=104)
        test_button.pack(
            side=tk.LEFT,
            padx=(0, 8),
        )
        clear_button = ModernButton(actions, text="Clear Saved", variant="secondary", command=clear_key, height=34, min_width=108)
        clear_button.pack(
            side=tk.LEFT,
            padx=(0, 8),
        )
        close_button = ModernButton(actions, text="Close", variant="ghost", command=modal.destroy, height=34, min_width=84)
        close_button.pack(
            side=tk.LEFT
        )

        refresh_status()
        key_entry.focus_set()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.run_buttons.values():
            button.configure(state=state)
        for button in self.section_selected_buttons.values():
            button.configure(state=state)
        if self.global_selected_button is not None:
            self.global_selected_button.configure(state=state)
        self._set_rerun_failed_enabled(enabled and bool(self.last_failed_test_case_ids))
        if self.evidence_preview_button is not None:
            self.evidence_preview_button.configure(state=state)
        if self.failed_recovery_button is not None:
            self.failed_recovery_button.configure(state=state)
        for checkbox in self.selection_checkboxes.values():
            checkbox.configure(state=state)
        if self.master_button is not None:
            self.master_button.configure(state=state)
        if self.complete_button is not None:
            self.complete_button.configure(state=state)
        if self.dry_run_button is not None:
            self.dry_run_button.configure(state=state)
        if self.latest_report_button is not None:
            self.latest_report_button.configure(state=state)
        if self.build_doc_button is not None:
            self.build_doc_button.configure(state=state)
        if self.schedule_complete_button is not None:
            self.schedule_complete_button.configure(state=state)
        if self.evidence_root_button is not None:
            self.evidence_root_button.configure(state=state)
        if self.preflight_button is not None:
            self.preflight_button.configure(state=state)
        if self.evidence_audit_button is not None:
            self.evidence_audit_button.configure(state=state)
        if self.support_bundle_button is not None:
            self.support_bundle_button.configure(state=state)
        if self.shakedown_button is not None:
            self.shakedown_button.configure(state=state)
        if self.refresh_button is not None:
            self.refresh_button.configure(state=state)
        if self.theme_button is not None:
            self.theme_button.configure(state=state)
        if self.ai_key_button is not None:
            self.ai_key_button.configure(state=state)
        if self.runtime_mode_menu is not None:
            self.runtime_mode_menu.configure(state=state)
        if hasattr(self, "desktop_name_entry"):
            self.desktop_name_entry.configure(state="normal" if enabled else "disabled")
            if self.desktop_dropdown_button is not None:
                self.desktop_dropdown_button.configure(state=state)
            if not enabled:
                self._close_desktop_name_suggestions()
            self._update_desktop_input_state(disabled=not enabled)

    def _enable_stop_button(self, test_case_id: str) -> None:
        self._set_stop_button_enabled(test_case_id, True)

    def _set_stop_button_enabled(self, test_case_id: str, enabled: bool) -> None:
        button = self.stop_buttons.get(test_case_id)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_pause_button_enabled(self, test_case_id: str, enabled: bool) -> None:
        button = self.pause_buttons.get(test_case_id)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_skip_button_enabled(self, test_case_id: str, enabled: bool) -> None:
        button = self.skip_buttons.get(test_case_id)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _disable_row_pause_buttons(self) -> None:
        for button in self.pause_buttons.values():
            button.configure(state=tk.DISABLED)

    def _disable_row_skip_buttons(self) -> None:
        for button in self.skip_buttons.values():
            button.configure(state=tk.DISABLED)

    def _disable_all_stop_buttons(self) -> None:
        for button in self.stop_buttons.values():
            button.configure(state=tk.DISABLED)
        for button in self.section_stop_buttons.values():
            button.configure(state=tk.DISABLED)
        if self.global_selected_stop_button is not None:
            self.global_selected_stop_button.configure(state=tk.DISABLED)

    def _disable_all_skip_buttons(self) -> None:
        self._disable_row_skip_buttons()
        for button in self.section_skip_buttons.values():
            button.configure(state=tk.DISABLED)
        if self.global_selected_skip_button is not None:
            self.global_selected_skip_button.configure(state=tk.DISABLED)
        self._set_complete_skip_enabled(False)
        self._set_master_skip_enabled(False)
        self._set_shakedown_skip_enabled(False)

    def _set_master_stop_enabled(self, enabled: bool) -> None:
        if self.master_stop_button is not None:
            self.master_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_complete_stop_enabled(self, enabled: bool) -> None:
        if self.complete_stop_button is not None:
            self.complete_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_shakedown_stop_enabled(self, enabled: bool) -> None:
        if self.shakedown_stop_button is not None:
            self.shakedown_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_master_skip_enabled(self, enabled: bool) -> None:
        if self.master_skip_button is not None:
            self.master_skip_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_complete_skip_enabled(self, enabled: bool) -> None:
        if self.complete_skip_button is not None:
            self.complete_skip_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_shakedown_skip_enabled(self, enabled: bool) -> None:
        if self.shakedown_skip_button is not None:
            self.shakedown_skip_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _disable_all_pause_buttons(self) -> None:
        self._disable_row_pause_buttons()
        for button in self.section_pause_buttons.values():
            button.configure(state=tk.DISABLED)
        if self.global_selected_pause_button is not None:
            self.global_selected_pause_button.configure(state=tk.DISABLED)
        self._set_complete_pause_enabled(False)
        self._set_master_pause_enabled(False)
        self._set_shakedown_pause_enabled(False)
        self._refresh_pause_button_text()

    def _set_complete_pause_enabled(self, enabled: bool) -> None:
        if self.complete_pause_button is not None:
            self.complete_pause_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_master_pause_enabled(self, enabled: bool) -> None:
        if self.master_pause_button is not None:
            self.master_pause_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_shakedown_pause_enabled(self, enabled: bool) -> None:
        if self.shakedown_pause_button is not None:
            self.shakedown_pause_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_section_stop_enabled(self, section_title: str, enabled: bool) -> None:
        button = self.section_stop_buttons.get(section_title)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_section_pause_enabled(self, section_title: str, enabled: bool) -> None:
        button = self.section_pause_buttons.get(section_title)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_section_skip_enabled(self, section_title: str, enabled: bool) -> None:
        button = self.section_skip_buttons.get(section_title)
        if button is not None:
            button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_global_selected_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        if self.global_selected_stop_button is not None:
            self.global_selected_stop_button.configure(state=state)
        if self.global_selected_pause_button is not None:
            self.global_selected_pause_button.configure(state=state)
        if self.global_selected_skip_button is not None:
            self.global_selected_skip_button.configure(state=state)

    def _refresh_pause_button_text(self) -> None:
        text = "Resume" if self.active_paused else "Pause"
        for button in self.pause_buttons.values():
            button.configure(text=text)
        for button in self.section_pause_buttons.values():
            button.configure(text=text)
        if self.global_selected_pause_button is not None:
            self.global_selected_pause_button.configure(text=text)
        if self.complete_pause_button is not None:
            self.complete_pause_button.configure(text=text)
        if self.master_pause_button is not None:
            self.master_pause_button.configure(text=text)
        if self.shakedown_pause_button is not None:
            self.shakedown_pause_button.configure(text=text)

    def _set_active_pause_status(self, paused: bool) -> None:
        status = "Paused" if paused else "Running"
        if self.active_mode == "complete":
            self._set_complete_status(status)
            self.complete_current_phase = "Paused" if paused else self.complete_current_phase.replace("Paused", "Running")
            self._set_complete_runtime_summary()
        elif self.active_mode == "master":
            self._set_master_status(status)
        elif self.active_mode == "shakedown":
            self._set_shakedown_status(status)
        elif self.active_mode and self.active_mode.startswith("selected:"):
            if self.active_selected_section == "Mandatory Testcases":
                self._set_master_status(status)
            elif self.active_selected_section == "Shakedown Testcases":
                self._set_shakedown_status(status)
            else:
                self._set_selected_progress(self.active_test_id)
        elif self.active_mode == "single" and self.active_test_id is not None:
            self._set_run_progress(
                title="Individual Testcase",
                completed=0,
                total=1,
                status=status,
                current=self._test_name_for_id(self.active_test_id),
                next_item="None",
                remaining=1,
                elapsed_seconds=0,
            )
        if self.active_test_id in self.status_labels:
            self._set_status(self.active_test_id, status)

    def _clear_active_execution_controls(self) -> None:
        if self.active_pause_event is not None:
            self.active_pause_event.clear()
        if self.active_skip_event is not None:
            self.active_skip_event.clear()
        self.active_stop_event = None
        self.active_pause_event = None
        self.active_skip_event = None
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = None
        self.active_sequence_ids = []
        self.active_sequence_started_monotonic = None
        self._disable_all_stop_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self._disable_all_pause_buttons()
        self._disable_all_skip_buttons()
        self._set_complete_skip_enabled(False)
        self._set_master_skip_enabled(False)
        self._set_shakedown_skip_enabled(False)
        self._update_selection_cues()
        self._set_rerun_failed_enabled(bool(self.last_failed_test_case_ids))

    def _log_width_bounds(self) -> tuple[int, int]:
        min_width = 360
        max_width = 820
        if self.content_frame is not None:
            try:
                available_width = self.content_frame.winfo_width()
            except tk.TclError:
                available_width = 0
            if available_width > 1:
                max_width = min(max_width, max(min_width, available_width - 620))
        return min_width, max_width

    def _set_log_panel_width(self, value: float | str) -> None:
        try:
            width = int(float(value))
        except (TypeError, ValueError):
            width = self.log_panel_width
        min_width, max_width = self._log_width_bounds()
        self.log_panel_width = max(min_width, min(width, max_width))
        if self.content_frame is not None:
            self.content_frame.grid_columnconfigure(2, minsize=self.log_panel_width)
        if self.log_width_label is not None:
            self.log_width_label.configure(text=f"{self.log_panel_width}px")
        if hasattr(self, "message_box"):
            self.message_box.configure(width=max(self.log_panel_width - 28, 360))
        if self.progress_panel is not None:
            self.progress_panel.set_panel_width(self.log_panel_width, force=True)
            self.after_idle(self._settle_progress_panel_layout)

    def _set_log_splitter_active(self, active: bool) -> None:
        if self.log_splitter is None:
            return
        color = THEME["card_running_glow"] if active else THEME["bg"]
        try:
            self.log_splitter.configure(bg=color)
        except tk.TclError:
            pass

    def _begin_log_panel_resize(self, event: tk.Event) -> None:
        self._log_resize_start_x = int(event.x_root)
        self._log_resize_start_width = self.log_panel_width
        self._set_log_splitter_active(True)

    def _drag_log_panel_resize(self, event: tk.Event) -> None:
        delta = int(event.x_root) - self._log_resize_start_x
        self._set_log_panel_width(self._log_resize_start_width - delta)

    def _end_log_panel_resize(self, _event: tk.Event) -> None:
        self._set_log_splitter_active(False)

    def _bind_log_splitter(self, widget: tk.Widget) -> None:
        widget.configure(cursor="sb_h_double_arrow")
        widget.bind("<ButtonPress-1>", self._begin_log_panel_resize, add="+")
        widget.bind("<B1-Motion>", self._drag_log_panel_resize, add="+")
        widget.bind("<ButtonRelease-1>", self._end_log_panel_resize, add="+")

    def _settle_progress_panel_layout(self) -> None:
        if self.progress_panel is None:
            return
        self.progress_panel.settle_layout(self.log_panel_width)

    def _schedule_test_cases_card_resize(self, _event: tk.Event | None = None) -> None:
        if self._test_cases_resize_job is not None:
            try:
                self.after_cancel(self._test_cases_resize_job)
            except tk.TclError:
                pass
        self._test_cases_resize_job = self.after(80, self._resize_test_cases_card)

    def _resize_test_cases_card(self) -> None:
        self._test_cases_resize_job = None
        # Testcase rows now participate in the main scroll area, so the card should
        # keep its natural requested height instead of creating a nested scrollbar.
        return

    def _adjust_log_panel_width(self, delta: int) -> None:
        self._set_log_panel_width(self.log_panel_width + delta)

    def _scroll_frame_units(self, frame: ctk.CTkScrollableFrame | None, units: int) -> None:
        if frame is None:
            return
        canvas = getattr(frame, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_scroll(units, "units")

    def _start_scroll_hold(self, frame: ctk.CTkScrollableFrame | None, units: int) -> None:
        self._stop_scroll_hold()
        self._scroll_frame_units(frame, units)
        self._scroll_hold_job = self.after(180, lambda: self._continue_scroll_hold(frame, units))

    def _continue_scroll_hold(self, frame: ctk.CTkScrollableFrame | None, units: int) -> None:
        self._scroll_frame_units(frame, units)
        self._scroll_hold_job = self.after(38, lambda: self._continue_scroll_hold(frame, units))

    def _stop_scroll_hold(self) -> None:
        job = getattr(self, "_scroll_hold_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._scroll_hold_job = None

    def _make_scroll_button(
        self,
        parent: tk.Widget,
        text: str,
        frame_getter,
        units: int,
    ) -> ModernButton:
        button = ModernButton(
            parent,
            text=text,
            variant="ghost",
            command=lambda: self._scroll_frame_units(frame_getter(), units),
            height=24,
            min_width=24,
            font=("Segoe UI", 12, "bold"),
        )
        button.bind("<ButtonPress-1>", lambda _event: self._start_scroll_hold(frame_getter(), units), add="+")
        button.bind("<ButtonRelease-1>", lambda _event: self._stop_scroll_hold(), add="+")
        button.bind("<Leave>", lambda _event: self._stop_scroll_hold(), add="+")
        return button

    def _configure_styles(self) -> None:
        ctk.set_appearance_mode(self.theme_name)
        ctk.set_default_color_theme("blue")

    def _build_layout(self) -> None:
        self.configure(fg_color=THEME["bg"])
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, height=62, corner_radius=0, fg_color=THEME["header_bottom"])
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)
        header.grid_rowconfigure((0, 1), weight=1)
        header.grid_propagate(False)

        accent = ctk.CTkFrame(header, width=4, corner_radius=0, fg_color=THEME["teal"])
        accent.grid(row=0, column=0, rowspan=2, sticky="ns")

        icon = HeaderLogo(header)
        icon.grid(row=0, column=1, rowspan=2, sticky="w", padx=(20, 12), pady=12)

        ctk.CTkLabel(
            header,
            text="Citrix Test Automation Runner",
            text_color="#ffffff",
            font=("Segoe UI", 18, "bold"),
            anchor=tk.W,
        ).grid(row=0, column=2, sticky="sw", pady=(8, 0))
        ctk.CTkLabel(
            header,
            text="Run evidence checks, monitor progress, and keep desktop outputs organized.",
            text_color=THEME["header_subtitle"],
            font=("Segoe UI", 9),
            anchor=tk.W,
        ).grid(row=1, column=2, sticky="nw", pady=(0, 8))

        header_actions = ctk.CTkFrame(header, fg_color="transparent")
        header_actions.grid(row=0, column=3, rowspan=2, sticky="e", padx=(12, 18))
        action_row = ctk.CTkFrame(header_actions, fg_color="transparent")
        action_row.pack(anchor=tk.E)
        ctk.CTkLabel(
            action_row,
            text=f"Version: {self.app_version}",
            text_color=THEME["header_icon_text"],
            fg_color=THEME["header_icon"],
            corner_radius=10,
            width=116,
            height=24,
            font=("Segoe UI", 8, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        self.runtime_mode_menu = ctk.CTkOptionMenu(
            action_row,
            values=self._runtime_mode_labels(),
            variable=self.runtime_mode_var,
            command=self._set_runtime_mode_from_label,
            width=104,
            height=28,
            corner_radius=8,
            fg_color=THEME["header_icon"],
            button_color=THEME["primary"],
            button_hover_color=THEME["primary_hover"],
            text_color=THEME["header_icon_text"],
            dropdown_fg_color=THEME["card"],
            dropdown_hover_color=THEME["card_hover"],
            dropdown_text_color=THEME["text"],
            font=("Segoe UI", 9, "bold"),
            dropdown_font=("Segoe UI", 10),
        )
        self.runtime_mode_menu.pack(side=tk.LEFT, padx=(0, 8))
        self.ai_key_button = ModernButton(
            action_row,
            text="AI Key",
            variant="ghost",
            command=self.show_openai_key_dialog,
            height=28,
            min_width=68,
            radius=8,
            font=("Segoe UI", 10, "bold"),
        )
        self.ai_key_button.pack(side=tk.LEFT, padx=(0, 8))
        self.theme_button = ModernButton(
            action_row,
            text="Light" if self.theme_name == "dark" else "Dark",
            variant="ghost",
            command=self.toggle_theme,
            height=28,
            min_width=76,
            radius=8,
            font=("Segoe UI", 10, "bold"),
        )
        self.theme_button.pack(side=tk.LEFT, padx=(0, 8))
        self.refresh_button = ModernButton(
            action_row,
            text="Refresh",
            variant="ghost",
            command=self.refresh_tests,
            height=28,
            min_width=84,
            radius=8,
            font=("Segoe UI", 10, "bold"),
        )
        self.refresh_button.pack(side=tk.LEFT)

        content = ctk.CTkFrame(self, fg_color=THEME["bg"])
        self.content_frame = content
        content.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0, minsize=10)
        content.grid_columnconfigure(2, weight=0, minsize=self.log_panel_width)
        content.bind("<Configure>", self._schedule_test_cases_card_resize, add="+")

        left_shell = ctk.CTkFrame(content, fg_color="transparent")
        left_shell.grid(row=0, column=0, sticky="nsew")
        left_shell.grid_rowconfigure(0, weight=1)
        left_shell.grid_columnconfigure(0, weight=1)
        left_panel = ctk.CTkScrollableFrame(
            left_shell,
            fg_color="transparent",
            scrollbar_button_color=THEME["scrollbar"],
            scrollbar_button_hover_color=THEME["primary"],
        )
        left_panel.grid(row=0, column=0, sticky="nsew")
        left_panel.grid_rowconfigure(4, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)
        self.main_canvas = left_panel
        main_scroll_buttons = ctk.CTkFrame(left_shell, width=28, fg_color="transparent")
        main_scroll_buttons.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        main_scroll_buttons.grid_propagate(False)
        self._make_scroll_button(
            main_scroll_buttons,
            text="▲",
            frame_getter=lambda: self.main_canvas,
            units=-12,
        ).pack(side=tk.TOP, pady=(2, 4))
        self._make_scroll_button(
            main_scroll_buttons,
            text="▼",
            frame_getter=lambda: self.main_canvas,
            units=12,
        ).pack(side=tk.BOTTOM, pady=(4, 2))
        main_scroll_buttons.grid_remove()

        splitter = tk.Frame(content, width=10, bg=THEME["bg"], bd=0, highlightthickness=0)
        splitter.grid(row=0, column=1, sticky="ns", padx=(5, 7))
        splitter.grid_propagate(False)
        splitter_bar = tk.Frame(splitter, width=2, bg=THEME["divider"], bd=0, highlightthickness=0)
        splitter_bar.pack(fill=tk.Y, expand=True, padx=4, pady=16)
        self.log_splitter = splitter
        self._bind_log_splitter(splitter)
        self._bind_log_splitter(splitter_bar)

        log_panel = self._make_card(content, 18, 18)
        log_panel.grid(row=0, column=2, sticky="nsew")
        log_panel.grid_rowconfigure(3, weight=1)
        log_panel.grid_columnconfigure(0, weight=1)

        self.input_card = self._make_card(left_panel, 12, 12)
        self.input_card.configure(height=138)
        self.input_card.pack_propagate(False)
        self.input_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        input_header = ctk.CTkFrame(self.input_card, fg_color="transparent")
        input_header.pack(fill=tk.X, padx=12, pady=(10, 0))
        ctk.CTkLabel(
            input_header,
            text="Citrix Desktop Name",
            text_color=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT)
        self.desktop_state_label = ctk.CTkLabel(
            input_header,
            text="Required before execution",
            text_color=THEME["muted"],
            font=("Segoe UI", 11, "bold"),
        )
        self.desktop_state_label.pack(side=tk.RIGHT)
        self.input_shell = ctk.CTkFrame(
            self.input_card,
            height=42,
            fg_color="transparent",
            border_width=0,
            corner_radius=0,
        )
        self.input_shell.pack(fill=tk.X, padx=12, pady=(6, 0))
        self.input_shell.grid_propagate(False)
        self.input_shell.grid_columnconfigure(0, weight=1)
        self.desktop_suggestion_values = self._desktop_dropdown_values()
        self.desktop_name_entry = ctk.CTkEntry(
            self.input_shell,
            placeholder_text="Type silo name, e.g. SILO07-TEST-AP1",
            state="normal",
            height=42,
            corner_radius=10,
            fg_color=THEME["input"],
            border_color=THEME["border"],
            border_width=1,
            text_color=THEME["text"],
            placeholder_text_color=THEME["muted"],
            font=("Segoe UI", 12),
        )
        self.desktop_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=0)
        if self.desktop_name_var.get():
            self._set_desktop_entry_value(self.desktop_name_var.get())
        self.desktop_dropdown_separator = ctk.CTkFrame(self.input_shell, width=1, fg_color="transparent")
        self.desktop_dropdown_button = ModernButton(
            self.input_shell,
            text="▾",
            variant="secondary",
            command=self._toggle_desktop_name_suggestions,
            height=42,
            min_width=48,
            radius=10,
            font=("Segoe UI Symbol", 13, "bold"),
        )
        self.desktop_dropdown_button.grid(row=0, column=1, sticky="e", padx=0, pady=0)
        self.desktop_name_entry.bind("<FocusIn>", lambda _event: self._update_desktop_input_state(focused=True))
        self.desktop_name_entry.bind("<FocusOut>", self._on_desktop_name_focus_out)
        self.desktop_name_entry.bind("<KeyPress>", self._on_desktop_name_keypress)
        self.desktop_name_entry.bind("<KeyRelease>", self._on_desktop_name_keyrelease)
        self._update_desktop_input_state()
        evidence_row = ctk.CTkFrame(self.input_card, fg_color="transparent", height=24)
        evidence_row.pack(fill=tk.X, padx=12, pady=(5, 0))
        evidence_row.pack_propagate(False)
        ctk.CTkLabel(
            evidence_row,
            text="Evidence Root",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT)
        self.evidence_root_label = ctk.CTkLabel(
            evidence_row,
            text="",
            text_color=THEME["text"],
            font=("Segoe UI", 10),
        )
        self.evidence_root_label.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        self.schedule_complete_button = ModernButton(
            evidence_row,
            text="Schedule",
            variant="primary",
            command=self.show_complete_schedule_dialog,
            height=22,
            min_width=82,
            font=("Segoe UI", 9, "bold"),
        )
        self.schedule_complete_button.pack(side=tk.RIGHT)
        self.evidence_root_button = ModernButton(
            evidence_row,
            text="Change",
            variant="secondary",
            command=self.choose_evidence_root,
            height=22,
            min_width=72,
            font=("Segoe UI", 9, "bold"),
        )
        self.evidence_root_button.pack(side=tk.RIGHT, padx=(0, 8))
        self._refresh_evidence_root_label()
        ctk.CTkLabel(
            self.input_card,
            text="Example: SILO01-TEST. The app automatically targets the matching Citrix Desktop Viewer window.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, padx=12, pady=(3, 2))
        self.desktop_shortcuts_frame = ctk.CTkFrame(self.input_card, height=24, fg_color="transparent")
        self.desktop_shortcuts_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self._refresh_desktop_shortcuts()

        self.complete_card = self._make_card(left_panel, 12, 12)
        self.complete_card.configure(height=88)
        self.complete_card.pack_propagate(False)
        self.complete_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._build_complete_card()

        self.schedule_status_card = self._make_card(left_panel, 10, 10)
        self.schedule_status_card.configure(height=116)
        self.schedule_status_card.pack_propagate(False)
        self.schedule_status_card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._build_schedule_status_card()
        self.schedule_status_card.grid_remove()

        self.master_card = self._make_card(left_panel, 12, 12)
        self.master_card.configure(height=74)
        self.master_card.pack_propagate(False)
        self.master_card.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._build_sequence_card(
            self.master_card,
            title="Run All Mandatory Testcases",
            subtitle="Executes the mandatory evidence sequence in the configured order.",
            status_attr="master_status_label",
            progress_attr="master_progress_label",
            run_attr="master_button",
            pause_attr="master_pause_button",
            stop_attr="master_stop_button",
            skip_attr="master_skip_button",
            run_command=self.run_mandatory_testcases,
            pause_command=lambda: self.request_pause_resume("Mandatory Testcases"),
            stop_command=lambda: self.request_stop("Mandatory Testcases"),
            skip_command=lambda: self.request_skip("Mandatory Testcases"),
        )

        self.shakedown_card = self._make_card(left_panel, 12, 12)
        self.shakedown_card.configure(height=74)
        self.shakedown_card.pack_propagate(False)
        self.shakedown_card.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self._build_sequence_card(
            self.shakedown_card,
            title="Run All Shakedown Testcases",
            subtitle="Executes the shakedown validation sequence in the configured order.",
            status_attr="shakedown_status_label",
            progress_attr="shakedown_progress_label",
            run_attr="shakedown_button",
            pause_attr="shakedown_pause_button",
            stop_attr="shakedown_stop_button",
            skip_attr="shakedown_skip_button",
            run_command=self.run_shakedown_testcases,
            pause_command=lambda: self.request_pause_resume("Shakedown Testcases"),
            stop_command=lambda: self.request_stop("Shakedown Testcases"),
            skip_command=lambda: self.request_skip("Shakedown Testcases"),
        )

        list_card = self._make_card(left_panel, 10, 10)
        self.test_cases_card = list_card
        list_card.grid_propagate(True)
        list_card.grid(row=5, column=0, sticky="ew")
        list_card.grid_rowconfigure(2, weight=0)
        list_card.grid_columnconfigure(0, weight=1)
        list_header = ctk.CTkFrame(list_card, height=86, fg_color="transparent")
        list_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        list_header.grid_propagate(False)
        list_header.grid_columnconfigure(0, weight=1)
        title_text = ctk.CTkFrame(list_header, height=28, fg_color="transparent")
        title_text.grid(row=0, column=0, sticky="ew")
        title_text.grid_propagate(False)
        ctk.CTkLabel(
            title_text,
            text="Test Cases",
            text_color=THEME["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side=tk.LEFT)
        ctk.CTkLabel(
            title_text,
            text="Run, monitor, or stop individual automation checks.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT, padx=(10, 0))

        selected_actions = ctk.CTkFrame(list_header, fg_color="transparent")
        selected_actions.grid(row=1, column=0, sticky="e", pady=(8, 0))
        self.global_selected_stop_button = ModernButton(
            selected_actions,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Selected testcases"),
            height=28,
            min_width=66,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_stop_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_selected_stop_button.configure(state=tk.DISABLED)
        self.global_selected_skip_button = ModernButton(
            selected_actions,
            text="Skip",
            variant="secondary",
            command=lambda: self.request_skip("Selected testcases"),
            height=28,
            min_width=66,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_skip_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_selected_skip_button.configure(state=tk.DISABLED)
        self.global_selected_pause_button = ModernButton(
            selected_actions,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Selected testcases"),
            height=28,
            min_width=76,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_pause_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_selected_pause_button.configure(state=tk.DISABLED)
        self.global_selected_button = ModernButton(
            selected_actions,
            text="Run Selected",
            variant="primary",
            command=lambda: self.run_selected_section(None),
            height=30,
            min_width=136,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_button.pack(side=tk.RIGHT)
        self.global_rerun_failed_button = ModernButton(
            selected_actions,
            text="Rerun Failed",
            variant="secondary",
            command=self.rerun_failed_testcases,
            height=28,
            min_width=118,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_rerun_failed_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_rerun_failed_button.configure(state=tk.DISABLED)
        self.failed_recovery_button = ModernButton(
            selected_actions,
            text="Recovery",
            variant="secondary",
            command=self.show_failed_recovery_panel,
            height=28,
            min_width=92,
            font=("Segoe UI", 10, "bold"),
        )
        self.failed_recovery_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.evidence_preview_button = ModernButton(
            selected_actions,
            text="Preview Evidence",
            variant="secondary",
            command=self.show_evidence_preview,
            height=28,
            min_width=126,
            font=("Segoe UI", 10, "bold"),
        )
        self.evidence_preview_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.selected_progress_label = ctk.CTkLabel(
            list_card,
            text="Select testcases from any section, then run them together.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        self.selected_progress_label.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        list_shell = ctk.CTkFrame(list_card, fg_color="transparent")
        list_shell.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        list_shell.grid_rowconfigure(0, weight=0)
        list_shell.grid_columnconfigure(0, weight=1)
        self.list_frame = ctk.CTkFrame(list_shell, fg_color="transparent")
        self.list_frame.grid(row=0, column=0, sticky="ew")
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.list_canvas = self.list_frame
        list_scroll_buttons = ctk.CTkFrame(list_shell, width=28, fg_color="transparent")
        list_scroll_buttons.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        list_scroll_buttons.grid_propagate(False)
        self._make_scroll_button(
            list_scroll_buttons,
            text="▲",
            frame_getter=lambda: self.list_canvas,
            units=-10,
        ).pack(side=tk.TOP, pady=(2, 4))
        self._make_scroll_button(
            list_scroll_buttons,
            text="▼",
            frame_getter=lambda: self.list_canvas,
            units=10,
        ).pack(side=tk.BOTTOM, pady=(4, 2))
        list_scroll_buttons.grid_remove()

        self.progress_panel = ProgressRingPanel(log_panel)
        self.progress_panel.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.progress_panel.set_panel_width(self.log_panel_width, force=True)

        log_header = ctk.CTkFrame(log_panel, height=36, fg_color="transparent")
        log_header.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 0))
        log_header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            log_header,
            text="Execution Messages",
            text_color=THEME["text"],
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.log_width_label = None
        log_meta = ctk.CTkFrame(log_panel, height=32, fg_color="transparent")
        log_meta.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        log_meta.grid_columnconfigure(1, weight=1)
        log_meta.grid_propagate(False)
        ctk.CTkLabel(
            log_meta,
            text="Live automation output",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        ).grid(row=0, column=0, sticky="w")
        log_tools = ctk.CTkFrame(log_meta, fg_color="transparent")
        log_tools.grid(row=0, column=1, sticky="e")
        self.log_filter_button = ModernButton(
            log_tools,
            text="Errors",
            variant="secondary",
            command=self.toggle_error_log_filter,
            height=24,
            min_width=58,
            font=("Segoe UI", 9, "bold"),
        )
        self.log_filter_button.pack(side=tk.RIGHT, padx=(6, 0))
        ModernButton(
            log_tools,
            text="Save",
            variant="secondary",
            command=self.save_execution_messages,
            height=24,
            min_width=50,
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ModernButton(
            log_tools,
            text="Copy",
            variant="secondary",
            command=self.copy_execution_messages,
            height=24,
            min_width=50,
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ModernButton(
            log_tools,
            text="Clear",
            variant="secondary",
            command=self._clear_execution_messages,
            height=24,
            min_width=50,
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.RIGHT)
        self.message_box = ctk.CTkTextbox(
            log_panel,
            width=self.log_panel_width - 28,
            height=360,
            fg_color=THEME["console"],
            text_color=THEME["console_text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=14,
            font=("Cascadia Mono", 11),
            wrap=tk.WORD,
        )
        self.message_box.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.message_box.configure(state=tk.DISABLED)

    def _build_schedule_status_card(self) -> None:
        if self.schedule_status_card is None:
            return
        header = ctk.CTkFrame(self.schedule_status_card, fg_color="transparent", height=32)
        header.pack(fill=tk.X, padx=12, pady=(8, 0))
        header.pack_propagate(False)
        title_area = ctk.CTkFrame(header, fg_color="transparent")
        title_area.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.schedule_status_title = ctk.CTkLabel(
            title_area,
            text="Scheduled Batch",
            text_color=THEME["text"],
            font=("Segoe UI", 12, "bold"),
        )
        self.schedule_status_title.pack(side=tk.LEFT)
        self.schedule_status_current = ctk.CTkLabel(
            title_area,
            text="No scheduled run active",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        )
        self.schedule_status_current.pack(side=tk.LEFT, padx=(10, 0))
        self.schedule_status_counts = ctk.CTkLabel(
            header,
            text="Queued 0 | Completed 0/0 | Passed 0 | Failed 0 | Skipped 0",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        self.schedule_status_counts.pack(side=tk.RIGHT)

        self.schedule_status_queue = ctk.CTkFrame(
            self.schedule_status_card,
            fg_color=THEME["card_soft"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=9,
        )
        self.schedule_status_queue.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 10))

    def _build_complete_card(self) -> None:
        header = ctk.CTkFrame(self.complete_card, height=38, fg_color="transparent")
        header.pack(fill=tk.X, padx=12, pady=(8, 0))
        header.pack_propagate(False)
        text_block = ctk.CTkFrame(header, height=38, fg_color="transparent")
        text_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        text_block.pack_propagate(False)
        ctk.CTkLabel(
            text_block,
            text="Perform Complete Testing",
            text_color=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W)
        ctk.CTkLabel(
            text_block,
            text="Runs Mandatory, Shakedown, and IAT suites end-to-end.",
            text_color=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(1, 0))
        complete_visuals = ctk.CTkFrame(header, height=30, fg_color="transparent")
        complete_visuals.pack(side=tk.RIGHT, padx=(10, 0))
        self.complete_status_label = StatusBadge(complete_visuals, text="Idle")
        self.complete_status_label.pack(side=tk.LEFT, padx=(0, 8))
        self.complete_button = ModernButton(
            complete_visuals,
            text="Run All",
            variant="primary",
            command=self.run_complete_testing,
            height=26,
            min_width=82,
            font=("Segoe UI", 10, "bold"),
        )
        self.complete_button.pack(side=tk.LEFT)
        self.latest_report_button = ModernButton(
            complete_visuals,
            text="Open Evidence",
            variant="secondary",
            command=self.open_evidence_folder,
            height=24,
            min_width=104,
            font=("Segoe UI", 9, "bold"),
        )
        self.latest_report_button.pack(side=tk.LEFT, padx=(6, 0))
        self.build_doc_button = ModernButton(
            complete_visuals,
            text="Build Doc",
            variant="secondary",
            command=self.build_word_document,
            height=24,
            min_width=82,
            font=("Segoe UI", 9, "bold"),
        )
        self.build_doc_button.pack(side=tk.LEFT, padx=(6, 0))
        self.complete_pause_button = ModernButton(
            complete_visuals,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Complete Testing"),
            height=24,
            min_width=54,
            font=("Segoe UI", 9, "bold"),
        )
        self.complete_pause_button.pack(side=tk.LEFT, padx=(6, 0))
        self.complete_pause_button.configure(state=tk.DISABLED)
        self.complete_skip_button = ModernButton(
            complete_visuals,
            text="Skip",
            variant="secondary",
            command=lambda: self.request_skip("Complete Testing"),
            height=24,
            min_width=50,
            font=("Segoe UI", 9, "bold"),
        )
        self.complete_skip_button.pack(side=tk.LEFT, padx=(6, 0))
        self.complete_skip_button.configure(state=tk.DISABLED)
        self.complete_stop_button = ModernButton(
            complete_visuals,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Complete Testing"),
            height=24,
            min_width=50,
            font=("Segoe UI", 9, "bold"),
        )
        self.complete_stop_button.pack(side=tk.LEFT, padx=(6, 0))
        self.complete_stop_button.configure(state=tk.DISABLED)
        self.dry_run_button = None

        self.complete_progress_label = ctk.CTkLabel(
            self.complete_card,
            text="Ready",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        self.complete_progress_label.pack(anchor=tk.W, padx=12, pady=(5, 0))

        utility_row = ctk.CTkFrame(self.complete_card, height=28, fg_color="transparent")
        utility_row.pack(fill=tk.X, padx=12, pady=(6, 8))
        utility_row.pack_propagate(False)
        self.preflight_button = ModernButton(
            utility_row,
            text="Preflight",
            variant="secondary",
            command=self.run_preflight_check,
            height=24,
            min_width=86,
            font=("Segoe UI", 9, "bold"),
        )
        self.preflight_button.pack(side=tk.LEFT, padx=(0, 6))
        self.evidence_audit_button = ModernButton(
            utility_row,
            text="Audit Evidence",
            variant="secondary",
            command=self.audit_evidence,
            height=24,
            min_width=118,
            font=("Segoe UI", 9, "bold"),
        )
        self.evidence_audit_button.pack(side=tk.LEFT, padx=(0, 6))
        self.support_bundle_button = ModernButton(
            utility_row,
            text="Support Bundle",
            variant="secondary",
            command=self.create_support_bundle_action,
            height=24,
            min_width=124,
            font=("Segoe UI", 9, "bold"),
        )
        self.support_bundle_button.pack(side=tk.LEFT)

    def _build_sequence_card(
        self,
        card: ctk.CTkFrame,
        title: str,
        subtitle: str,
        status_attr: str,
        progress_attr: str,
        run_attr: str,
        pause_attr: str,
        stop_attr: str,
        skip_attr: str,
        run_command,
        pause_command,
        stop_command,
        skip_command,
    ) -> None:
        body = ctk.CTkFrame(card, height=52, fg_color="transparent")
        body.pack(fill=tk.X, padx=12, pady=8)
        body.pack_propagate(False)
        text_block = ctk.CTkFrame(body, height=50, fg_color="transparent")
        text_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        text_block.pack_propagate(False)
        ctk.CTkLabel(text_block, text=title, text_color=THEME["text"], font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        ctk.CTkLabel(text_block, text=subtitle, text_color=THEME["muted"], font=("Segoe UI", 9)).pack(anchor=tk.W)
        progress_label = ctk.CTkLabel(text_block, text="Ready", text_color=THEME["muted"], font=("Segoe UI", 9, "bold"))
        progress_label.pack(anchor=tk.W, pady=(3, 0))
        setattr(self, progress_attr, progress_label)

        actions = ctk.CTkFrame(body, height=28, fg_color="transparent")
        actions.pack(side=tk.RIGHT, padx=(12, 0))
        actions.pack_propagate(False)
        status_label = StatusBadge(actions, text="Idle")
        status_label.pack(side=tk.LEFT, padx=(0, 8))
        setattr(self, status_attr, status_label)
        run_button = ModernButton(actions, text="Run All", variant="primary", command=run_command, height=26, min_width=74, font=("Segoe UI", 10, "bold"))
        run_button.pack(side=tk.LEFT)
        setattr(self, run_attr, run_button)
        pause_button = ModernButton(actions, text="Pause", variant="secondary", command=pause_command, height=24, min_width=54, font=("Segoe UI", 9, "bold"))
        pause_button.pack(side=tk.LEFT, padx=(6, 0))
        pause_button.configure(state=tk.DISABLED)
        setattr(self, pause_attr, pause_button)
        skip_button = ModernButton(actions, text="Skip", variant="secondary", command=skip_command, height=24, min_width=50, font=("Segoe UI", 9, "bold"))
        skip_button.pack(side=tk.LEFT, padx=(6, 0))
        skip_button.configure(state=tk.DISABLED)
        setattr(self, skip_attr, skip_button)
        stop_button = ModernButton(actions, text="Stop", variant="danger", command=stop_command, height=24, min_width=50, font=("Segoe UI", 9, "bold"))
        stop_button.pack(side=tk.LEFT, padx=(6, 0))
        stop_button.configure(state=tk.DISABLED)
        setattr(self, stop_attr, stop_button)

    def _make_card(self, parent, padx: int, pady: int) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=THEME["card"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=12,
        )

    def _draw_header(self, event: tk.Event) -> None:
        return

    def _update_scroll_region(self, _event: tk.Event) -> None:
        return

    def _resize_scroll_window(self, event: tk.Event) -> None:
        return

    def _update_main_scroll_region(self, _event: tk.Event) -> None:
        return

    def _resize_main_scroll_window(self, event: tk.Event) -> None:
        return

    def _bind_main_mousewheel(self, _event: tk.Event) -> None:
        return

    def _unbind_main_mousewheel(self, _event: tk.Event) -> None:
        return

    def _on_main_mousewheel(self, event: tk.Event) -> None:
        return

    def _bind_list_mousewheel(self, _event: tk.Event) -> None:
        return

    def _unbind_list_mousewheel(self, _event: tk.Event) -> None:
        return

    def _on_list_mousewheel(self, event: tk.Event) -> None:
        return

    def toggle_theme(self) -> None:
        if self.active_stop_event is not None:
            messagebox.showinfo("Theme Locked", "Theme can be changed after the current run finishes.")
            return
        self._close_desktop_name_suggestions()
        old_theme = THEME.copy()
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        _activate_theme(self.theme_name)
        ctk.set_appearance_mode(self.theme_name)
        self._repaint_theme(old_theme)
        self._update_desktop_input_state()
        self._render_execution_messages()
        self._settle_progress_panel_layout()

    def _repaint_theme(self, old_theme: dict[str, str]) -> None:
        color_map = {value: THEME[key] for key, value in old_theme.items() if key in THEME}
        color_map.update(
            {
                old_theme.get("header_bottom", ""): THEME["header_bottom"],
                old_theme.get("header_icon", ""): THEME["header_icon"],
                old_theme.get("header_icon_text", ""): THEME["header_icon_text"],
            }
        )
        self.configure(fg_color=THEME["bg"])
        self._repaint_widget_tree(self, color_map)
        self._refresh_theme_surfaces()
        if self.theme_button is not None:
            self.theme_button.configure(text="Light" if self.theme_name == "dark" else "Dark", variant="ghost")
        for button in self._iter_widgets_of_type(ModernButton):
            try:
                state = button.cget("state")
            except tk.TclError:
                state = tk.NORMAL
            button.configure(variant=button.variant, state=state)
        for badge in self._iter_widgets_of_type(StatusBadge):
            badge.configure(text=badge.text)
        for test_case_id, status in list(self.test_card_states.items()):
            self._set_test_card_state(test_case_id, status)
        for checkbox in list(self.selection_checkboxes.values()) + list(self.section_select_checkboxes.values()):
            try:
                checkbox.configure(
                    fg_color=THEME["primary"],
                    hover_color=THEME["primary_hover"],
                    border_color=THEME["muted"],
                    checkmark_color="#ffffff",
                )
            except tk.TclError:
                pass
        if self.runtime_mode_menu is not None:
            try:
                self.runtime_mode_menu.configure(
                    fg_color=THEME["header_icon"],
                    button_color=THEME["primary"],
                    button_hover_color=THEME["primary_hover"],
                    text_color=THEME["header_icon_text"],
                    dropdown_fg_color=THEME["card"],
                    dropdown_hover_color=THEME["card_hover"],
                    dropdown_text_color=THEME["text"],
                )
            except tk.TclError:
                pass
        if self.progress_panel is not None:
            self.progress_panel._draw_chart()

    def _refresh_theme_surfaces(self) -> None:
        frame_theme = {
            "input_card": THEME["card"],
            "complete_card": THEME["card"],
            "schedule_status_card": THEME["card"],
            "master_card": THEME["card"],
            "shakedown_card": THEME["card"],
            "test_cases_card": THEME["card"],
            "content_frame": THEME["bg"],
            "list_frame": THEME["card"],
            "list_canvas": THEME["card"],
        }
        for attr_name, fg_color in frame_theme.items():
            widget = getattr(self, attr_name, None)
            if widget is None:
                continue
            try:
                widget.configure(fg_color=fg_color)
            except tk.TclError:
                pass

        if hasattr(self, "desktop_dropdown_separator"):
            try:
                self.desktop_dropdown_separator.configure(fg_color=THEME["border"])
            except tk.TclError:
                pass
        for button in self._iter_widgets_of_type(ModernButton):
            try:
                button.configure(variant=button.variant, state=button.cget("state"))
            except tk.TclError:
                pass

        if self.message_box is not None:
            try:
                self.message_box.configure(
                    fg_color=THEME["console"],
                    text_color=THEME["console_text"],
                    border_color=THEME["border"],
                )
            except tk.TclError:
                pass
        self._update_error_filter_button()
        if self.log_splitter is not None:
            try:
                self.log_splitter.configure(bg=THEME["divider"])
            except tk.TclError:
                pass
        if self.log_width_label is not None:
            try:
                self.log_width_label.configure(text_color=THEME["muted"])
            except tk.TclError:
                pass

        for test_case_id, status in list(self.test_card_states.items()):
            self._set_test_card_state(test_case_id, status)
        self._refresh_section_headers_theme()
        if self.progress_panel is not None:
            self.progress_panel.refresh_theme()
        if self.schedule_status_queue is not None:
            self.schedule_status_queue.configure(fg_color=THEME["card_soft"], border_color=THEME["border"])
        if self.schedule_status_title is not None:
            self.schedule_status_title.configure(text_color=THEME["text"])
        if self.schedule_status_current is not None:
            self.schedule_status_current.configure(text_color=THEME["muted"])
        if self.schedule_status_counts is not None:
            self.schedule_status_counts.configure(text_color=THEME["muted"])
        if self.scheduled_desktops or self.scheduled_results:
            self._update_schedule_status_panel()

    def _refresh_section_headers_theme(self) -> None:
        for checkbox in self.section_select_checkboxes.values():
            try:
                checkbox.configure(
                    text_color=THEME["text"],
                    fg_color=THEME["primary"],
                    hover_color=THEME["primary_hover"],
                    border_color=THEME["muted"],
                    checkmark_color="#ffffff",
                )
            except tk.TclError:
                pass

    def _iter_widgets_of_type(self, widget_type):
        stack = list(self.winfo_children())
        while stack:
            widget = stack.pop()
            if isinstance(widget, widget_type):
                yield widget
            try:
                stack.extend(widget.winfo_children())
            except tk.TclError:
                continue

    def _repaint_widget_tree(self, widget: tk.Widget, color_map: dict[str, str]) -> None:
        self._repaint_single_widget(widget, color_map)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._repaint_widget_tree(child, color_map)

    def _mapped_color(self, value, color_map: dict[str, str]):
        if isinstance(value, str):
            return color_map.get(value, value)
        if isinstance(value, (tuple, list)):
            mapped = [color_map.get(item, item) if isinstance(item, str) else item for item in value]
            return tuple(mapped) if isinstance(value, tuple) else mapped
        return value

    def _repaint_single_widget(self, widget: tk.Widget, color_map: dict[str, str]) -> None:
        if isinstance(widget, HeaderLogo):
            try:
                widget.configure(fg_color=THEME["header_icon"])
                widget.canvas.configure(bg=THEME["header_icon"])
                widget._draw_mark()
            except tk.TclError:
                pass
            return
        if isinstance(widget, ModernButton):
            return
        if isinstance(widget, StatusBadge):
            widget.configure(text=widget.text)
            return
        if isinstance(widget, tk.Canvas):
            try:
                widget.configure(bg=self._mapped_color(widget.cget("bg"), color_map))
            except tk.TclError:
                pass
            return
        if isinstance(widget, (tk.Frame, tk.Button, tk.Listbox, tk.Scrollbar)) and not widget.__class__.__module__.startswith("customtkinter"):
            updates = {}
            for option in ("bg", "fg", "activebackground", "activeforeground", "highlightbackground", "troughcolor"):
                try:
                    updates[option] = self._mapped_color(widget.cget(option), color_map)
                except (tk.TclError, ValueError):
                    continue
            if updates:
                try:
                    widget.configure(**updates)
                except (tk.TclError, ValueError):
                    pass
            return
        ctk_options = (
            "fg_color",
            "text_color",
            "border_color",
            "hover_color",
            "button_color",
            "button_hover_color",
            "dropdown_fg_color",
            "dropdown_hover_color",
            "dropdown_text_color",
            "scrollbar_button_color",
            "scrollbar_button_hover_color",
        )
        updates = {}
        for option in ctk_options:
            try:
                updates[option] = self._mapped_color(widget.cget(option), color_map)
            except (tk.TclError, ValueError):
                continue
        if updates:
            try:
                widget.configure(**updates)
            except tk.TclError:
                pass

    def refresh_tests(self) -> None:
        if self.active_stop_event is not None:
            messagebox.showinfo("Refresh Locked", "Refresh is available after the current run finishes.")
            return
        self._clear_execution_messages()
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.status_labels.clear()
        self.run_buttons.clear()
        self.stop_buttons.clear()
        self.pause_buttons.clear()
        self.skip_buttons.clear()
        self.test_cards.clear()
        self.test_card_accents.clear()
        self.test_card_states.clear()
        self.description_labels.clear()
        self.description_buttons.clear()
        self.description_expanded.clear()
        self.section_frames.clear()
        self.section_containers.clear()
        self.section_buttons.clear()
        self.section_selected_buttons.clear()
        self.section_pause_buttons.clear()
        self.section_stop_buttons.clear()
        self.section_skip_buttons.clear()
        self.section_selection_labels.clear()
        self.section_test_ids.clear()
        self.section_collapsed.clear()
        self.section_select_vars.clear()
        self.section_select_checkboxes.clear()
        self.selection_vars.clear()
        self.selection_checkboxes.clear()

        self.config = load_config(self.root_dir)
        self._apply_runtime_mode_to_config(self.runtime_mode_key)
        self.desktop_history = DesktopNameHistory(self.config)
        self._refresh_desktop_history_values()
        self.test_cases = discover_test_cases(self.config.path("test_cases_dir"))
        self._reset_dashboard_statuses()

        if not self.test_cases:
            ctk.CTkLabel(
                self.list_frame,
                text="No test cases found. Add Python scripts to the test_cases folder.",
                text_color=THEME["muted"],
                font=("Segoe UI", 11),
            ).pack(anchor=tk.W, pady=20)
            return

        tests_by_name = {test_case.name: test_case for test_case in self.test_cases}
        rendered_ids: set[str] = set()
        rendered_ids.update(
            self._add_test_case_section(
                "Mandatory Testcases",
                "Individual checks included in Run All Mandatory Testcases.",
                [tests_by_name[name] for name in MANDATORY_TEST_CASE_ORDER if name in tests_by_name],
            )
        )
        rendered_ids.update(
            self._add_test_case_section(
                "Shakedown Testcases",
                "Individual checks included in Run All Shakedown Testcases.",
                [tests_by_name[name] for name in SHAKEDOWN_TEST_CASE_ORDER if name in tests_by_name],
            )
        )
        rendered_ids.update(
            self._add_test_case_section(
                "Silo 43 Testcases",
                "Silo 43-only validation checks.",
                [tests_by_name[name] for name in SILO43_TEST_CASE_ORDER if name in tests_by_name],
            )
        )

        rendered_ids.update(
            self._add_test_case_section(
                "IAT Testcase",
                "Integrated acceptance testing checks.",
                [tests_by_name[name] for name in IAT_TEST_CASE_ORDER if name in tests_by_name],
            )
        )

        other_tests = [test_case for test_case in self.test_cases if test_case.id not in rendered_ids]
        if other_tests:
            self._add_test_case_section("Other Testcases", "Additional standalone checks.", other_tests)
        self._reset_dashboard_statuses()
        self._schedule_test_cases_card_resize()
        self.after_idle(self._settle_progress_panel_layout)
        self.after(250, self._settle_progress_panel_layout)

    def _clear_execution_messages(self, clear_history: bool = True) -> None:
        if clear_history:
            self.log_entries.clear()
        if not hasattr(self, "message_box"):
            return
        self._clear_log_textbox()
        self._update_error_filter_button()

    def _clear_log_textbox(self) -> None:
        try:
            self.message_box.configure(state=tk.NORMAL)
            self.message_box.delete("1.0", tk.END)
            self.message_box.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def copy_execution_messages(self) -> None:
        text = self._visible_log_text()
        if not text:
            messagebox.showinfo("No Logs", "There are no execution messages to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._append_message("Execution messages copied to clipboard.")

    def save_execution_messages(self) -> None:
        text = self._visible_log_text()
        if not text:
            messagebox.showinfo("No Logs", "There are no execution messages to save.")
            return
        desktop_name = self._normalized_desktop_name()
        logs_dir = (
            desktop_scoped_path(self.config.path("logs_dir"), desktop_name)
            if desktop_name
            else self.config.path("logs_dir")
        )
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            output_path = logs_dir / f"ui_execution_messages_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            output_path.write_text(text + "\n", encoding="utf-8")
            self._append_message(f"Execution messages saved: {output_path}")
        except OSError as exc:
            messagebox.showerror("Save Logs Failed", f"Could not save execution messages:\n\n{exc}")

    def toggle_error_log_filter(self) -> None:
        self.log_errors_only = not self.log_errors_only
        self._update_error_filter_button()
        self._render_execution_messages()

    def _update_error_filter_button(self) -> None:
        if self.log_filter_button is None:
            return
        if self.log_errors_only:
            self.log_filter_button.configure(text="All Logs", variant="secondary")
        else:
            self.log_filter_button.configure(text="Errors", variant="secondary")

    def _visible_log_text(self) -> str:
        messages = [
            message
            for message in self.log_entries
            if not self.log_errors_only or self._is_error_log_message(message)
        ]
        return "\n".join(messages).strip()

    def show_evidence_preview(self) -> None:
        self._show_evidence_review_panel(failed_only=False)

    def show_failed_recovery_panel(self) -> None:
        self._show_evidence_review_panel(failed_only=True)

    def _show_evidence_review_panel(self, failed_only: bool) -> None:
        if self.active_mode is not None:
            messagebox.showinfo("Automation Running", "Wait for the current execution to finish before reviewing evidence.")
            return
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror("Citrix Desktop Name Required", "Please enter Citrix Desktop Name.")
            self._focus_desktop_name_entry()
            return

        records, manifest_path = self._collect_evidence_records(desktop_name, failed_only=failed_only)
        if not records:
            title = "No Failed Evidence" if failed_only else "No Evidence Found"
            body = (
                "No failed testcases were found for the selected desktop."
                if failed_only
                else "No evidence screenshots or testcase logs were found for the selected desktop."
            )
            messagebox.showinfo(title, f"{body}\n\nDesktop:\n{desktop_name}")
            return

        modal_title = "Failed Test Recovery" if failed_only else "Evidence Preview"
        modal = ctk.CTkToplevel(self)
        modal.title(modal_title)
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.transient(self)
        modal.grab_set()
        modal.resizable(True, True)
        modal.minsize(920, 560)

        modal_state = {"normal_geometry": "", "maximized": False}

        def modal_work_area() -> tuple[int, int, int, int]:
            try:
                import ctypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_ulong),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", ctypes.c_ulong),
                    ]

                user32 = ctypes.windll.user32
                hwnd = modal.winfo_id()
                monitor = user32.MonitorFromWindow(hwnd, 2)
                monitor_info = MONITORINFO()
                monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                    rect = monitor_info.rcWork
                    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

                rect = RECT()
                if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
            except Exception:
                pass
            return 0, 0, modal.winfo_screenwidth(), max(modal.winfo_screenheight() - 80, 600)

        def restore_modal_grab(_event=None) -> None:
            try:
                if modal.winfo_exists() and modal.state() != "iconic":
                    modal.after(50, modal.grab_set)
            except tk.TclError:
                pass

        def minimize_modal() -> None:
            try:
                modal.grab_release()
                modal.iconify()
            except tk.TclError:
                pass

        def close_modal() -> None:
            try:
                modal.grab_release()
            except tk.TclError:
                pass
            modal.destroy()

        def toggle_modal_maximize() -> None:
            try:
                if not modal_state["maximized"]:
                    modal_state["normal_geometry"] = modal.geometry()
                    try:
                        modal.state("normal")
                    except tk.TclError:
                        pass
                    work_left, work_top, work_width, work_height = modal_work_area()
                    target_width = max(work_width - 18, 920)
                    target_height = max(work_height - 58, 560)
                    modal.geometry(f"{target_width}x{target_height}+{work_left + 4}+{work_top + 4}")
                    modal_state["maximized"] = True
                    maximize_button.configure(text="\ue923")
                else:
                    try:
                        modal.state("normal")
                    except tk.TclError:
                        pass
                    if modal_state["normal_geometry"]:
                        modal.geometry(modal_state["normal_geometry"])
                    modal_state["maximized"] = False
                    maximize_button.configure(text="\ue922")
                restore_modal_grab()
            except tk.TclError:
                pass

        def window_control_button(parent, text: str, command) -> ctk.CTkButton:
            return ctk.CTkButton(
                parent,
                text=text,
                command=command,
                width=34,
                height=28,
                corner_radius=6,
                fg_color="transparent",
                hover_color=THEME["card_hover"],
                text_color=THEME["muted"],
                border_width=0,
                font=("Segoe MDL2 Assets", 12),
            )

        modal.bind("<Map>", restore_modal_grab, add="+")
        modal.protocol("WM_DELETE_WINDOW", close_modal)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(1, weight=1)
        card.grid_rowconfigure(2, weight=0, minsize=48)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 10))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)
        header.grid_columnconfigure(2, weight=0)
        ctk.CTkLabel(
            header,
            text=modal_title,
            text_color=THEME["text"],
            font=("Segoe UI", 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        count_text = self._evidence_record_summary(records, failed_only)
        ctk.CTkLabel(
            header,
            text=count_text,
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))
        window_controls = ctk.CTkFrame(header, fg_color="transparent")
        window_controls.grid(row=0, column=2, sticky="e", padx=(14, 0))
        window_control_button(window_controls, "\ue921", minimize_modal).pack(side=tk.LEFT, padx=(0, 4))
        maximize_button = window_control_button(window_controls, "\ue922", toggle_modal_maximize)
        maximize_button.pack(side=tk.LEFT)
        manifest_text = f"Manifest: {self._short_path_text(manifest_path, 82)}" if manifest_path else "Manifest: not available"
        ctk.CTkLabel(
            header,
            text=f"{desktop_name} | {manifest_text}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            anchor=tk.W,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        list_panel = ctk.CTkFrame(card, width=360, fg_color=THEME["card_soft"], corner_radius=12)
        list_panel.grid(row=1, column=0, sticky="nsw", padx=(16, 10), pady=(0, 12))
        list_panel.grid_propagate(False)
        list_panel.grid_rowconfigure(0, weight=1)
        list_panel.grid_columnconfigure(0, weight=1)

        record_list = ctk.CTkScrollableFrame(
            list_panel,
            fg_color="transparent",
            scrollbar_button_color=THEME["scrollbar"],
            scrollbar_button_hover_color=THEME["primary"],
        )
        record_list.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        detail_panel = ctk.CTkFrame(card, fg_color="transparent")
        detail_panel.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(0, 12))
        detail_panel.grid_rowconfigure(1, weight=1)
        detail_panel.grid_columnconfigure(0, weight=1)

        preview_title = ctk.CTkLabel(
            detail_panel,
            text="Select evidence",
            text_color=THEME["text"],
            font=("Segoe UI", 14, "bold"),
            anchor=tk.W,
        )
        preview_title.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        preview_frame = ctk.CTkFrame(
            detail_panel,
            fg_color=THEME["console"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=12,
        )
        preview_frame.grid(row=1, column=0, sticky="nsew")
        preview_frame.grid_propagate(False)
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_label = ctk.CTkLabel(
            preview_frame,
            text="Preview will appear here.",
            text_color=THEME["console_text"],
            font=("Segoe UI", 11, "bold"),
        )
        preview_label.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        details_box = ctk.CTkTextbox(
            detail_panel,
            height=112,
            fg_color=THEME["card_soft"],
            text_color=THEME["text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=12,
            font=("Cascadia Mono", 10),
            wrap=tk.WORD,
        )
        details_box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        details_box.configure(state=tk.DISABLED)

        notice = ctk.CTkLabel(
            detail_panel,
            text="Choose an item to review screenshot and log details.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
            anchor=tk.W,
        )
        notice.grid(row=3, column=0, sticky="ew", pady=(8, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        actions.grid_propagate(False)
        actions.configure(height=42)
        actions.grid_columnconfigure(0, weight=1)

        selected_index = {"value": -1}
        row_buttons: list[ModernButton] = []
        image_ref = {"value": None}

        def selected_record() -> dict[str, object] | None:
            index = selected_index["value"]
            if index < 0 or index >= len(records):
                return None
            return records[index]

        def set_details(text: str) -> None:
            details_box.configure(state=tk.NORMAL)
            details_box.delete("1.0", tk.END)
            details_box.insert(tk.END, text)
            details_box.configure(state=tk.DISABLED)

        def select_record(index: int) -> None:
            selected_index["value"] = index
            record = records[index]
            for button in row_buttons:
                button.configure(variant="secondary")
            row_buttons[index].configure(variant="primary")

            test_case = str(record.get("test_case") or "Evidence")
            status = str(record.get("status") or "Unknown")
            preview_title.configure(text=f"{test_case} - {status}")
            screenshot_path = self._record_screenshot_path(record)
            if screenshot_path is None:
                image_ref["value"] = None
                preview_label.configure(image=None, text="No screenshot is linked to this result.")
            else:
                self._render_preview_image(preview_label, screenshot_path, image_ref)
            set_details(self._record_detail_text(record))
            notice.configure(text="Evidence details loaded.", text_color=THEME["teal"])

        for index, record in enumerate(records):
            row = ctk.CTkFrame(record_list, height=38, fg_color="transparent")
            row.pack(fill=tk.X, pady=(0, 6))
            row.pack_propagate(False)
            StatusBadge(row, text=str(record.get("status") or "Unknown"), width=66, height=24).pack(side=tk.LEFT, padx=(0, 6))
            label = self._short_record_label(str(record.get("test_case") or record.get("filename") or "Evidence"))
            button = ModernButton(
                row,
                text=label,
                variant="secondary",
                command=lambda selected=index: select_record(selected),
                height=28,
                min_width=248,
                font=("Segoe UI", 10, "bold"),
                anchor=tk.W,
            )
            button.pack(side=tk.LEFT, fill=tk.X, expand=True)
            row_buttons.append(button)

        def open_selected_screenshot() -> None:
            record = selected_record()
            path = self._record_screenshot_path(record) if record else None
            if path is None:
                notice.configure(text="No screenshot is available for the selected item.", text_color=THEME["danger"])
                return
            self._open_file_path(path, notice, "Screenshot opened.")

        def open_selected_folder() -> None:
            record = selected_record()
            folder = self._record_folder_path(record) if record else None
            if folder is None:
                notice.configure(text="No folder is available for the selected item.", text_color=THEME["danger"])
                return
            self._open_folder_path(folder, notice, "Evidence folder opened.")

        def open_selected_log() -> None:
            record = selected_record()
            log_path = self._record_log_path(record) if record else None
            if log_path is None:
                notice.configure(text="No log is linked to the selected item.", text_color=THEME["danger"])
                return
            self._open_file_path(log_path, notice, "Log opened.")

        ModernButton(actions, text="Open Screenshot", variant="primary", command=open_selected_screenshot, height=34, min_width=150).pack(side=tk.LEFT, padx=(0, 8))
        ModernButton(actions, text="Open Folder", variant="secondary", command=open_selected_folder, height=34, min_width=118).pack(side=tk.LEFT, padx=(0, 8))
        ModernButton(actions, text="Open Log", variant="secondary", command=open_selected_log, height=34, min_width=100).pack(side=tk.LEFT, padx=(0, 8))

        if failed_only:
            ModernButton(
                actions,
                text="Rerun Selected",
                variant="secondary",
                command=lambda: self._rerun_records_from_panel([selected_record()] if selected_record() else [], modal, notice),
                height=34,
                min_width=132,
            ).pack(side=tk.LEFT, padx=(0, 8))
            ModernButton(
                actions,
                text="Rerun Failed",
                variant="danger",
                command=lambda: self._rerun_records_from_panel(records, modal, notice),
                height=34,
                min_width=122,
            ).pack(side=tk.LEFT)

        modal.update_idletasks()
        work_left, work_top, work_width, work_height = modal_work_area()
        width = min(max(self.winfo_width() - 110, 940), 1180, max(work_width - 40, 920))
        height = min(max(self.winfo_height() - 120, 560), 720, max(work_height - 90, 560))
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        x = min(max(x, work_left + 10), work_left + max(work_width - width - 10, 10))
        y = min(max(y, work_top + 10), work_top + max(work_height - height - 10, 10))
        modal.geometry(f"{width}x{height}+{x}+{y}")
        select_record(0)

    def _collect_evidence_records(self, desktop_name: str, failed_only: bool = False) -> tuple[list[dict[str, object]], Path | None]:
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        logs_root = desktop_scoped_path(self.config.path("logs_dir"), desktop_name)
        evidence_root = screenshots_root.parent
        manifest_path = evidence_root / MANIFEST_FILENAME
        if evidence_root.exists():
            try:
                manifest_path = build_run_manifest(self.config.path("screenshots_dir"), self.config.path("logs_dir"), desktop_name)
                self.latest_manifest_path = manifest_path
                self._append_message(f"Evidence index refreshed for preview: {manifest_path}")
            except Exception as exc:
                self._append_message(f"Evidence index refresh skipped: {exc}")

        payload = self._read_manifest_payload(manifest_path)
        failed_names = {self._test_name_for_id(test_id) for test_id in self.last_failed_test_case_ids}
        records: list[dict[str, object]] = []
        seen_screenshots: set[str] = set()

        testcases_payload = payload.get("testcases") if isinstance(payload, dict) else None
        if isinstance(testcases_payload, dict):
            for test_case_name, raw_entry in testcases_payload.items():
                if not isinstance(raw_entry, dict):
                    continue
                record = self._manifest_entry_to_record(str(test_case_name), raw_entry, screenshots_root)
                if failed_only and not self._record_is_failed_like(record, failed_names):
                    continue
                records.append(record)
                for item in record.get("screenshot_items", []):
                    if isinstance(item, dict) and item.get("path"):
                        seen_screenshots.add(str(item["path"]))

        records.extend(
            self._fallback_screenshot_records(
                screenshots_root,
                logs_root,
                failed_only=failed_only,
                seen_screenshots=seen_screenshots,
            )
        )
        records.sort(key=self._evidence_record_sort_key)
        return records, manifest_path if manifest_path.exists() else None

    def _read_manifest_payload(self, manifest_path: Path) -> dict[str, object]:
        if not manifest_path.exists():
            return {}
        try:
            with manifest_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self._append_message(f"Could not read evidence manifest: {exc}")
            return {}
        return payload if isinstance(payload, dict) else {}

    def _manifest_entry_to_record(
        self,
        test_case_name: str,
        entry: dict[str, object],
        screenshots_root: Path,
    ) -> dict[str, object]:
        screenshots = entry.get("latest_screenshots")
        screenshot_items = screenshots if isinstance(screenshots, list) else []
        screenshot_path = self._first_existing_screenshot_path(screenshot_items)
        evidence_category = str(entry.get("evidence_category") or evidence_category_for_test_name(test_case_name))
        folder = screenshot_path.parent if screenshot_path is not None else screenshots_root / evidence_category
        log_path = self._existing_path(entry.get("latest_log_path") or entry.get("suite_log_path"))
        timestamp = self._record_timestamp(entry, screenshot_path, log_path)
        return {
            "test_case": test_case_name,
            "status": str(entry.get("status") or "Unknown"),
            "phase": str(entry.get("phase") or self._section_title_for_test_name(test_case_name) or "Unknown"),
            "folder": folder,
            "screenshot_path": screenshot_path,
            "screenshot_items": screenshot_items,
            "log_path": log_path,
            "suite_log_path": self._existing_path(entry.get("suite_log_path")),
            "error": entry.get("error"),
            "manual_check_message": entry.get("manual_check_message"),
            "requires_manual_check": bool(entry.get("requires_manual_check")),
            "validation": entry.get("validation") if isinstance(entry.get("validation"), dict) else {},
            "timestamp": timestamp,
        }

    def _fallback_screenshot_records(
        self,
        screenshots_root: Path,
        logs_root: Path,
        failed_only: bool,
        seen_screenshots: set[str],
    ) -> list[dict[str, object]]:
        if not screenshots_root.exists():
            return []
        records: list[dict[str, object]] = []
        images = sorted(screenshots_root.rglob("*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
        for image_path in images[:250]:
            if str(image_path) in seen_screenshots:
                continue
            status = self._status_from_screenshot_filename(image_path.name)
            if failed_only and status != "Fail":
                continue
            test_case_name = self._test_case_name_for_screenshot(image_path.name) or image_path.stem
            records.append(
                {
                    "test_case": test_case_name,
                    "status": status or "Unknown",
                    "phase": image_path.parent.name,
                    "folder": image_path.parent,
                    "screenshot_path": image_path,
                    "screenshot_items": [
                        {
                            "path": str(image_path),
                            "folder": image_path.parent.name,
                            "filename": image_path.name,
                            "status_from_filename": status,
                        }
                    ],
                    "log_path": self._latest_log_for_test(logs_root, test_case_name),
                    "suite_log_path": None,
                    "error": None,
                    "manual_check_message": None,
                    "requires_manual_check": False,
                    "validation": {},
                    "timestamp": image_path.stat().st_mtime,
                }
            )
        return records

    def _first_existing_screenshot_path(self, screenshot_items: list[object]) -> Path | None:
        fail_candidate = None
        first_candidate = None
        for item in screenshot_items:
            if not isinstance(item, dict):
                continue
            path = self._existing_path(item.get("path"))
            if path is None:
                continue
            if first_candidate is None:
                first_candidate = path
            if item.get("status_from_filename") == "Fail":
                fail_candidate = path
                break
        return fail_candidate or first_candidate

    def _existing_path(self, value: object) -> Path | None:
        if not value:
            return None
        path = Path(str(value))
        return path if path.exists() else None

    def _record_timestamp(self, entry: dict[str, object], screenshot_path: Path | None, log_path: Path | None) -> float:
        raw_timestamp = entry.get("seen_timestamp")
        if isinstance(raw_timestamp, (int, float)):
            return float(raw_timestamp)
        for raw_value in (entry.get("latest_seen_at"), entry.get("modified_at")):
            if isinstance(raw_value, str):
                try:
                    return time.mktime(time.strptime(raw_value[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    continue
        for path in (screenshot_path, log_path):
            if path is not None:
                try:
                    return path.stat().st_mtime
                except OSError:
                    continue
        return 0.0

    def _record_is_failed_like(self, record: dict[str, object], failed_names: set[str]) -> bool:
        validation = record.get("validation")
        return (
            str(record.get("status")) == "Fail"
            or bool(record.get("requires_manual_check"))
            or str(record.get("test_case")) in failed_names
            or (isinstance(validation, dict) and bool(validation.get("has_active_failed_validation", validation.get("has_failed_validation"))))
        )

    def _evidence_record_sort_key(self, record: dict[str, object]) -> tuple[int, float, str]:
        priority = 0 if str(record.get("status")) == "Fail" or bool(record.get("requires_manual_check")) else 1
        return (priority, -float(record.get("timestamp") or 0), str(record.get("test_case") or ""))

    def _status_from_screenshot_filename(self, filename: str) -> str | None:
        name = filename.casefold()
        if "_fail_" in name:
            return "Fail"
        if "_pass_" in name:
            return "Pass"
        return None

    def _test_case_name_for_screenshot(self, filename: str) -> str | None:
        name = filename.casefold()
        for test_case_name, prefixes in TESTCASE_SCREENSHOT_PREFIXES.items():
            if any(name.startswith(prefix.casefold()) for prefix in prefixes):
                return test_case_name
        return None

    def _latest_log_for_test(self, logs_root: Path, test_case_name: str) -> Path | None:
        if not logs_root.exists():
            return None
        candidates = sorted(
            logs_root.glob(f"{test_case_name}_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _record_screenshot_path(self, record: dict[str, object] | None) -> Path | None:
        if not record:
            return None
        value = record.get("screenshot_path")
        if isinstance(value, Path) and value.exists():
            return value
        return self._existing_path(value)

    def _record_folder_path(self, record: dict[str, object] | None) -> Path | None:
        if not record:
            return None
        screenshot_path = self._record_screenshot_path(record)
        if screenshot_path is not None:
            return screenshot_path.parent
        value = record.get("folder")
        if isinstance(value, Path) and value.exists():
            return value
        return self._existing_path(value)

    def _record_log_path(self, record: dict[str, object] | None) -> Path | None:
        if not record:
            return None
        value = record.get("log_path") or record.get("suite_log_path")
        if isinstance(value, Path) and value.exists():
            return value
        return self._existing_path(value)

    def _short_record_label(self, label: str, max_chars: int = 42) -> str:
        return label if len(label) <= max_chars else f"{label[: max_chars - 3]}..."

    def _evidence_record_summary(self, records: list[dict[str, object]], failed_only: bool) -> str:
        failed_count = sum(1 for record in records if str(record.get("status")) == "Fail")
        manual_count = sum(1 for record in records if bool(record.get("requires_manual_check")))
        if failed_only:
            return f"{len(records)} recovery item(s)"
        return f"{len(records)} item(s) | Failed {failed_count} | Manual {manual_count}"

    def _record_detail_text(self, record: dict[str, object]) -> str:
        lines = [
            f"Testcase: {record.get('test_case') or 'Unknown'}",
            f"Status: {record.get('status') or 'Unknown'}",
            f"Phase: {record.get('phase') or 'Unknown'}",
        ]
        screenshot_path = self._record_screenshot_path(record)
        log_path = self._record_log_path(record)
        if screenshot_path is not None:
            lines.append(f"Screenshot: {screenshot_path}")
        else:
            lines.append("Screenshot: Not linked")
        if log_path is not None:
            lines.append(f"Log: {log_path}")
        else:
            lines.append("Log: Not linked")
        if record.get("error"):
            lines.extend(["", f"Error: {record.get('error')}"])
        if record.get("manual_check_message"):
            lines.extend(["", f"Manual check: {record.get('manual_check_message')}"])
        validation = record.get("validation")
        if isinstance(validation, dict):
            latest = validation.get("latest_message")
            if isinstance(latest, dict) and latest.get("message"):
                lines.extend(["", f"Validation: {latest.get('message')}"])
        return "\n".join(lines)

    def _render_preview_image(self, label: ctk.CTkLabel, image_path: Path, image_ref: dict[str, object]) -> None:
        try:
            with Image.open(image_path) as image:
                preview = image.copy()
            preview.thumbnail((650, 360), getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
            ctk_image = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
        except Exception as exc:
            image_ref["value"] = None
            label.configure(image=None, text=f"Preview unavailable:\n{exc}")
            return
        image_ref["value"] = ctk_image
        label.configure(image=ctk_image, text="")

    def _open_file_path(self, file_path: Path, notice: ctk.CTkLabel, success_message: str = "File opened.") -> None:
        if not file_path.exists():
            notice.configure(text=f"File was not found:\n{file_path}", text_color=THEME["danger"])
            return
        try:
            os.startfile(str(file_path))
            notice.configure(text=success_message, text_color=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open file: {exc}", text_color=THEME["danger"])

    def _rerun_records_from_panel(
        self,
        records: list[dict[str, object] | None],
        modal: ctk.CTkToplevel,
        notice: ctk.CTkLabel,
    ) -> None:
        test_ids = []
        for record in records:
            if not record:
                continue
            test_id = self._test_id_for_name(str(record.get("test_case") or ""))
            if test_id is not None and test_id not in test_ids:
                test_ids.append(test_id)
        if not test_ids:
            notice.configure(text="No runnable testcase is linked to the selected recovery item.", text_color=THEME["danger"])
            return
        self.last_failed_test_case_ids = test_ids
        self._set_rerun_failed_enabled(True)
        modal.destroy()
        self.rerun_failed_testcases()

    def _test_id_for_name(self, test_case_name: str) -> str | None:
        if test_case_name == POST_COMPLETE_ZSCALER_TEST_NAME:
            return POST_COMPLETE_ZSCALER_TEST_NAME
        for test_case in self.test_cases:
            if test_case.name == test_case_name:
                return test_case.id
        return None

    def show_complete_testing_checklist(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror("Citrix Desktop Name Required", "Please enter Citrix Desktop Name to generate the checklist paths.")
            self._focus_desktop_name_entry()
            return

        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        evidence_root = screenshots_root.parent
        lines = [
            "DRY RUN / CHECKLIST MODE",
            "",
            "No Citrix actions will run from this checklist.",
            "",
            f"Citrix Desktop Name: {desktop_name}",
            f"Evidence Root: {evidence_root}",
            f"Word Report: {evidence_root / f'{desktop_name}_Testing_.docx'}",
            "",
            "EXECUTION ORDER",
            "",
            "Mandatory Testcases:",
        ]
        mandatory_order = mandatory_order_for_desktop(desktop_name)
        lines.extend(f"  {index}. {name}" for index, name in enumerate(mandatory_order, start=1))
        if len(mandatory_order) != len(MANDATORY_TEST_CASE_ORDER):
            lines.append("  Applist_Validation_Evidence skipped for Ring0 desktop.")
        lines.extend(["", f"Transition delay before Shakedown: {self.config.wait('complete_phase_transition_wait_sec', 2.0)} second(s)", "", "Shakedown Testcases:"])
        lines.extend(f"  {index}. {name}" for index, name in enumerate(SHAKEDOWN_TEST_CASE_ORDER, start=1))
        lines.extend(["", f"Transition delay before IAT: {self.config.wait('complete_phase_transition_wait_sec', 2.0)} second(s)", "", "IAT Testcase:"])
        lines.extend(f"  {index}. {name}" for index, name in enumerate(IAT_TEST_CASE_ORDER, start=1))
        lines.extend(["", "Silo 43 Testcases (included in Complete Testing only for Silo43 desktops):"])
        lines.extend(f"  {index}. {name}" for index, name in enumerate(SILO43_TEST_CASE_ORDER, start=1))
        lines.extend(
            [
                "",
                "Post-complete evidence:",
                "  1. Relaunch ZCCVDI and capture zscaler_services_2",
                "",
                "OUTPUT FOLDERS",
                "",
                f"Mandatory screenshots: {screenshots_root / MANDATORY_EVIDENCE_FOLDER}",
                f"Shakedown screenshots: {screenshots_root / SHAKEDOWN_EVIDENCE_FOLDER}",
                f"IAT screenshots: {screenshots_root / IAT_EVIDENCE_FOLDER}",
                f"Silo 43 screenshots: {screenshots_root / SILO43_EVIDENCE_FOLDER}",
                f"Logs: {desktop_scoped_path(self.config.path('logs_dir'), desktop_name)}",
                "",
                "WORD REPORT STRUCTURE",
                "",
            ]
        )
        for section_title, _payload_key, folder_name, subsections in REPORT_STRUCTURE:
            lines.append(section_title)
            lines.append(f"  Folder: {screenshots_root / folder_name}")
            for subsection_title, prefixes in subsections:
                lines.append(f"  - {subsection_title}: {', '.join(prefixes)}")
            lines.append("")

        modal = ctk.CTkToplevel(self)
        modal.title("Complete Testing Checklist")
        modal.configure(fg_color=THEME["bg"])
        modal.transient(self)
        modal.grab_set()
        modal.resizable(True, True)

        card = self._make_card(modal, 18, 18)
        card.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        ctk.CTkLabel(card, text="Dry Run / Checklist Mode", text_color=THEME["text"], font=("Segoe UI", 17, "bold")).pack(anchor=tk.W, padx=18, pady=(18, 0))
        ctk.CTkLabel(
            card,
            text="Review the complete run order, expected evidence names, and output folders.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, padx=18, pady=(4, 12))
        text = ctk.CTkTextbox(
            card,
            width=920,
            height=520,
            fg_color=THEME["console"],
            text_color=THEME["console_text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=14,
            font=("Cascadia Mono", 10),
            wrap=tk.WORD,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=18)
        text.insert(tk.END, "\n".join(lines))
        text.configure(state=tk.DISABLED)
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=18)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=38, min_width=92).pack(side=tk.RIGHT)
        modal.geometry("980x720")

    def _add_test_case_section(self, title: str, subtitle: str, test_cases: list[TestCase]) -> set[str]:
        if not test_cases:
            return set()

        self.section_test_ids[title] = [test_case.id for test_case in test_cases]
        show_section_select = title in {"Mandatory Testcases", "Shakedown Testcases"}

        section = ctk.CTkFrame(
            self.list_frame,
            height=1,
            fg_color=THEME["card"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
        )
        section.pack(fill=tk.X, pady=(0, 8))
        self.section_frames[title] = section
        header = ctk.CTkFrame(section, height=50, fg_color=THEME["card_soft"], corner_radius=9)
        header.pack(fill=tk.X, padx=8, pady=(8, 5))
        header.pack_propagate(False)
        header_text = ctk.CTkFrame(header, height=44, fg_color="transparent")
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        header_text.pack_propagate(False)
        title_row = ctk.CTkFrame(header_text, height=22, fg_color="transparent")
        title_row.pack(anchor=tk.W, fill=tk.X)
        title_row.pack_propagate(False)
        if show_section_select:
            section_select_var = tk.BooleanVar(value=False)
            section_select = ctk.CTkCheckBox(
                title_row,
                text="",
                variable=section_select_var,
                command=lambda selected=title: self._on_section_selection_changed(selected),
                width=20,
                height=18,
                checkbox_width=16,
                checkbox_height=16,
                fg_color=THEME["primary"],
                hover_color=THEME["primary_hover"],
                border_color=THEME["muted"],
                checkmark_color="#ffffff",
            )
            section_select.pack(side=tk.LEFT, padx=(0, 7), pady=(2, 0))
            self.section_select_vars[title] = section_select_var
            self.section_select_checkboxes[title] = section_select
        ctk.CTkLabel(title_row, text=title, text_color=THEME["text"], font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ctk.CTkLabel(header_text, text=subtitle, text_color=THEME["muted"], font=("Segoe UI", 9)).pack(anchor=tk.W, pady=(1, 0))
        selection_label = ctk.CTkLabel(header_text, text="", text_color=THEME["teal"], font=("Segoe UI", 9, "bold"))
        selection_label.pack(anchor=tk.W, pady=(3, 0))

        run_selected_button = ModernButton(
            header,
            text="Run (Selected)",
            variant="secondary",
            command=lambda selected=title: self.run_selected_section(selected),
            height=26,
            min_width=116,
            font=("Segoe UI", 10, "bold"),
        )
        run_selected_button.pack(side=tk.RIGHT, padx=(6, 0))
        section_pause_button = ModernButton(
            header,
            text="Pause",
            variant="secondary",
            command=lambda selected=title: self.request_pause_resume(f"{selected} selected run"),
            height=26,
            min_width=60,
            font=("Segoe UI", 10, "bold"),
        )
        section_pause_button.pack(side=tk.RIGHT, padx=(6, 0))
        section_pause_button.configure(state=tk.DISABLED)
        section_stop_button = ModernButton(
            header,
            text="Stop",
            variant="danger",
            command=lambda selected=title: self.request_stop(f"{selected} selected run"),
            height=26,
            min_width=56,
            font=("Segoe UI", 10, "bold"),
        )
        section_stop_button.pack(side=tk.RIGHT, padx=(6, 0))
        section_stop_button.configure(state=tk.DISABLED)
        section_skip_button = ModernButton(
            header,
            text="Skip",
            variant="secondary",
            command=lambda selected=title: self.request_skip(f"{selected} selected run"),
            height=26,
            min_width=56,
            font=("Segoe UI", 10, "bold"),
        )
        section_skip_button.pack(side=tk.RIGHT, padx=(6, 0))
        section_skip_button.configure(state=tk.DISABLED)
        collapse_button = ModernButton(
            header,
            text="Collapse",
            variant="ghost",
            command=lambda selected=title: self._toggle_test_section(selected),
            height=26,
            min_width=78,
            font=("Segoe UI", 10, "bold"),
        )
        collapse_button.pack(side=tk.RIGHT)

        content = ctk.CTkFrame(section, height=1, fg_color="transparent")
        content.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.section_containers[title] = content
        self.section_buttons[title] = collapse_button
        self.section_selected_buttons[title] = run_selected_button
        self.section_pause_buttons[title] = section_pause_button
        self.section_stop_buttons[title] = section_stop_button
        self.section_skip_buttons[title] = section_skip_button
        self.section_selection_labels[title] = selection_label
        self.section_collapsed[title] = False

        rendered = set()
        for test_case in test_cases:
            self._add_test_case_card(test_case, content)
            rendered.add(test_case.id)
        return rendered

    def _add_test_case_card(self, test_case: TestCase, parent: tk.Widget | None = None) -> None:
        parent = parent or self.list_frame
        card = ctk.CTkFrame(
            parent,
            height=48,
            fg_color=THEME["card_soft"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=8,
        )
        card.pack(fill=tk.X, pady=(0, 6))
        card.grid_propagate(False)
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(2, weight=1)
        self.test_cards[test_case.id] = card
        self.test_card_states[test_case.id] = "Idle"
        self.description_expanded[test_case.id] = False
        self._bind_card_hover(card, test_case.id)

        accent = ctk.CTkFrame(card, width=3, corner_radius=2, fg_color=THEME["border"])
        accent.grid(row=0, column=0, sticky="nsw", padx=(8, 8), pady=7)
        self.test_card_accents[test_case.id] = accent

        selected_var = tk.BooleanVar(value=False)
        checkbox = ctk.CTkCheckBox(
            card,
            text="",
            variable=selected_var,
            command=self._on_test_selection_changed,
            width=20,
            height=22,
            checkbox_width=16,
            checkbox_height=16,
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            border_color=THEME["muted"],
            checkmark_color="#ffffff",
        )
        checkbox.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=7)
        self.selection_vars[test_case.id] = selected_var
        self.selection_checkboxes[test_case.id] = checkbox
        self._bind_card_hover(checkbox, test_case.id)

        title = ctk.CTkLabel(
            card,
            text=test_case.name,
            text_color=THEME["text"],
            font=("Segoe UI", 11, "bold"),
            anchor=tk.W,
            height=24,
        )
        title.grid(row=0, column=2, sticky="ew", pady=7)
        self._bind_card_hover(title, test_case.id)

        details_button = ModernButton(
            card,
            text="Details",
            variant="ghost",
            command=lambda selected=test_case: self._toggle_description(selected.id),
            height=22,
            min_width=68,
            font=("Segoe UI", 9, "bold"),
        )
        details_button.grid(row=0, column=3, padx=(8, 0), pady=7)
        self.description_buttons[test_case.id] = details_button

        status = StatusBadge(card, text="Idle", width=74, height=22)
        status.grid(row=0, column=4, padx=(8, 0), pady=7)
        self.status_labels[test_case.id] = status

        description = ctk.CTkLabel(
            card,
            text=test_case.description or "Automation testcase",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=520,
        )
        self.description_labels[test_case.id] = description

        actions = ctk.CTkFrame(card, width=198, height=28, fg_color="transparent")
        actions.grid(row=0, column=5, sticky="e", padx=(8, 8), pady=6)
        actions.pack_propagate(False)
        run_button = ModernButton(
            actions,
            text="Run",
            variant="secondary",
            command=lambda selected=test_case: self.run_test(selected),
            height=22,
            min_width=44,
            font=("Segoe UI", 9, "bold"),
        )
        run_button.pack(side=tk.LEFT)
        self.run_buttons[test_case.id] = run_button
        pause_button = ModernButton(
            actions,
            text="Pause",
            variant="secondary",
            command=lambda selected=test_case: self.request_pause_resume(selected.name),
            height=22,
            min_width=48,
            font=("Segoe UI", 9, "bold"),
        )
        pause_button.pack(side=tk.LEFT, padx=(4, 0))
        pause_button.configure(state=tk.DISABLED)
        self.pause_buttons[test_case.id] = pause_button
        skip_button = ModernButton(
            actions,
            text="Skip",
            variant="secondary",
            command=lambda selected=test_case: self.request_skip(selected.name),
            height=22,
            min_width=42,
            font=("Segoe UI", 9, "bold"),
        )
        skip_button.pack(side=tk.LEFT, padx=(4, 0))
        skip_button.configure(state=tk.DISABLED)
        self.skip_buttons[test_case.id] = skip_button
        stop_button = ModernButton(
            actions,
            text="Stop",
            variant="danger",
            command=lambda selected=test_case: self.request_stop(selected.name),
            height=22,
            min_width=42,
            font=("Segoe UI", 9, "bold"),
        )
        stop_button.pack(side=tk.LEFT, padx=(4, 0))
        stop_button.configure(state=tk.DISABLED)
        self.stop_buttons[test_case.id] = stop_button

    def _toggle_description(self, test_case_id: str) -> None:
        description = self.description_labels.get(test_case_id)
        button = self.description_buttons.get(test_case_id)
        if description is None or button is None:
            return
        expanded = not self.description_expanded.get(test_case_id, False)
        self.description_expanded[test_case_id] = expanded
        if expanded:
            self.test_cards[test_case_id].configure(height=74)
            description.grid(row=1, column=2, columnspan=4, sticky="ew", pady=(0, 8))
            button.configure(text="Hide")
        else:
            description.grid_forget()
            self.test_cards[test_case_id].configure(height=48)
            button.configure(text="Details")

    def _toggle_test_section(self, title: str) -> None:
        container = self.section_containers.get(title)
        button = self.section_buttons.get(title)
        if container is None or button is None:
            return
        collapsed = not self.section_collapsed.get(title, False)
        self.section_collapsed[title] = collapsed
        if collapsed:
            container.pack_forget()
            button.configure(text="Expand")
        else:
            container.pack(fill=tk.X, padx=8, pady=(0, 8))
            button.configure(text="Collapse")

    def _bind_card_hover(self, widget: tk.Widget, test_case_id: str) -> None:
        widget.bind("<Enter>", lambda _event, selected=test_case_id: self._set_test_card_hover(selected, True), add="+")
        widget.bind("<Leave>", lambda _event, selected=test_case_id: self._set_test_card_hover(selected, False), add="+")

    def _set_test_card_hover(self, test_case_id: str, hovered: bool) -> None:
        if self.test_card_states.get(test_case_id) != "Idle":
            return
        card = self.test_cards.get(test_case_id)
        if card is None:
            return
        bg = THEME["card_hover"] if hovered else THEME["card_soft"]
        self._set_frame_tree_bg(card, bg)
        accent = self.test_card_accents.get(test_case_id)
        if accent is not None:
            accent.configure(fg_color=THEME["border"])

    def _update_desktop_input_state(self, focused: bool = False, disabled: bool = False) -> None:
        if not hasattr(self, "input_shell"):
            return
        has_value = bool(self._desktop_entry_value().strip())
        if disabled:
            border = THEME["border"]
            text = "Locked during execution"
            color = THEME["muted"]
        elif focused:
            border = THEME["border_focus"]
            text = "Editing desktop target" if has_value else "Enter desktop target"
            color = THEME["primary"]
        elif has_value:
            border = THEME["teal"]
            text = "Desktop name ready"
            color = THEME["teal"]
        else:
            border = THEME["border"]
            text = "Required before execution"
            color = THEME["muted"]
        self.input_shell.configure(fg_color="transparent")
        if hasattr(self, "desktop_name_entry"):
            self.desktop_name_entry.configure(border_color=border)
        if hasattr(self, "desktop_state_label"):
            self.desktop_state_label.configure(text=text, text_color=color)

    def _on_desktop_name_focus_out(self, _event: tk.Event) -> None:
        self._update_desktop_input_state(focused=False)
        self.after(150, self._close_desktop_suggestions_if_focus_left)

    def _show_desktop_name_suggestions(self) -> None:
        if not hasattr(self, "desktop_name_entry"):
            return
        try:
            if str(self.desktop_name_entry.cget("state")) == "disabled":
                return
        except tk.TclError:
            return
        values = self.desktop_suggestion_values or self._desktop_dropdown_values()
        if not values:
            return
        if not self._desktop_suggestion_popup_exists():
            self._build_desktop_suggestion_popup()
        self._render_desktop_suggestion_list(values)
        self._position_desktop_suggestion_popup()
        try:
            self.desktop_suggestion_popup.deiconify()
            self.desktop_suggestion_popup.lift()
        except tk.TclError:
            pass

    def _toggle_desktop_name_suggestions(self) -> None:
        if self._desktop_suggestion_popup_visible():
            self._close_desktop_name_suggestions()
        else:
            self._refresh_desktop_history_values()
            self._show_desktop_name_suggestions()
            self._focus_desktop_name_entry()

    def _desktop_suggestion_popup_exists(self) -> bool:
        popup = getattr(self, "desktop_suggestion_popup", None)
        if popup is None:
            return False
        try:
            return bool(popup.winfo_exists())
        except tk.TclError:
            return False

    def _desktop_suggestion_popup_visible(self) -> bool:
        if not self._desktop_suggestion_popup_exists():
            return False
        try:
            return bool(self.desktop_suggestion_popup.winfo_viewable())
        except tk.TclError:
            return False

    def _build_desktop_suggestion_popup(self) -> None:
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=THEME["border"])
        popup.attributes("-topmost", True)
        popup.bind("<Escape>", lambda _event: self._close_desktop_name_suggestions())
        popup.bind("<FocusOut>", lambda _event: self.after(150, self._close_desktop_suggestions_if_focus_left))

        container = tk.Frame(popup, bg=THEME["card_soft"], highlightthickness=1, highlightbackground=THEME["border"])
        container.pack(fill=tk.BOTH, expand=True)

        up_button = tk.Button(
            container,
            text="▲",
            command=lambda: self._move_desktop_suggestion(-1),
            relief=tk.FLAT,
            bd=0,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            activebackground=THEME["card_running"],
            activeforeground=THEME["primary"],
            font=("Segoe UI Symbol", 9, "bold"),
            cursor="hand2",
        )
        up_button.pack(fill=tk.X)
        self._bind_desktop_suggestion_scroll_button(up_button, -1)

        list_frame = tk.Frame(container, bg=THEME["card_soft"])
        list_frame.pack(fill=tk.BOTH, expand=True)
        listbox = tk.Listbox(
            list_frame,
            activestyle="none",
            bd=0,
            exportselection=False,
            highlightthickness=0,
            selectmode=tk.SINGLE,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            selectbackground=THEME["primary"],
            selectforeground="#ffffff",
            font=("Segoe UI", 12),
        )
        scrollbar = tk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=listbox.yview,
            width=12,
            bg=THEME["card_soft"],
            activebackground=THEME["card_running"],
            troughcolor=THEME["bg"],
        )
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        down_button = tk.Button(
            container,
            text="▼",
            command=lambda: self._move_desktop_suggestion(1),
            relief=tk.FLAT,
            bd=0,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            activebackground=THEME["card_running"],
            activeforeground=THEME["primary"],
            font=("Segoe UI Symbol", 9, "bold"),
            cursor="hand2",
        )
        down_button.pack(fill=tk.X)
        self._bind_desktop_suggestion_scroll_button(down_button, 1)

        listbox.bind("<ButtonRelease-1>", lambda _event: self._apply_selected_desktop_suggestion())
        listbox.bind("<Double-Button-1>", lambda _event: self._apply_selected_desktop_suggestion())
        listbox.bind("<Return>", lambda _event: self._apply_selected_desktop_suggestion())
        listbox.bind("<Up>", lambda _event: self._move_desktop_suggestion(-1) or "break")
        listbox.bind("<Down>", lambda _event: self._move_desktop_suggestion(1) or "break")
        listbox.bind("<MouseWheel>", self._on_desktop_suggestion_mousewheel)

        self.desktop_suggestion_popup = popup
        self.desktop_suggestion_listbox = listbox
        self.desktop_suggestion_index = -1

    def _render_desktop_suggestion_list(self, values: list[str]) -> None:
        listbox = getattr(self, "desktop_suggestion_listbox", None)
        if listbox is None:
            return
        listbox.delete(0, tk.END)
        for value in values:
            listbox.insert(tk.END, value)
        self.desktop_suggestion_values = values
        typed = self._desktop_entry_value().strip().casefold()
        selected_index = 0
        if typed:
            for index, value in enumerate(values):
                if value.casefold().startswith(typed):
                    selected_index = index
                    break
        self._set_desktop_suggestion_index(selected_index)

    def _position_desktop_suggestion_popup(self) -> None:
        if not self._desktop_suggestion_popup_exists():
            return
        self.update_idletasks()
        entry_x = self.input_shell.winfo_rootx()
        entry_y = self.input_shell.winfo_rooty() + self.input_shell.winfo_height() + 2
        width = max(self.input_shell.winfo_width(), 300)
        visible_rows = min(max(len(self.desktop_suggestion_values), 1), 10)
        height = 26 + (visible_rows * 34) + 26
        self.desktop_suggestion_popup.geometry(f"{width}x{height}+{entry_x}+{entry_y}")

    def _move_desktop_suggestion(self, delta: int) -> None:
        if not self._desktop_suggestion_popup_exists():
            self._show_desktop_name_suggestions()
        values = self.desktop_suggestion_values
        if not values:
            return
        current = self.desktop_suggestion_index
        if current < 0:
            current = 0 if delta >= 0 else len(values) - 1
        else:
            current = max(0, min(len(values) - 1, current + delta))
        self._set_desktop_suggestion_index(current)

    def _set_desktop_suggestion_index(self, index: int) -> None:
        listbox = getattr(self, "desktop_suggestion_listbox", None)
        values = self.desktop_suggestion_values
        if listbox is None or not values:
            self.desktop_suggestion_index = -1
            return
        index = max(0, min(len(values) - 1, index))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index)
        listbox.activate(index)
        listbox.see(index)
        self.desktop_suggestion_index = index

    def _bind_desktop_suggestion_scroll_button(self, button: tk.Button, delta: int) -> None:
        button.bind("<ButtonPress-1>", lambda _event: self._start_desktop_suggestion_scroll_hold(delta), add="+")
        button.bind("<ButtonRelease-1>", lambda _event: self._stop_desktop_suggestion_scroll_hold(), add="+")
        button.bind("<Leave>", lambda _event: self._stop_desktop_suggestion_scroll_hold(), add="+")

    def _start_desktop_suggestion_scroll_hold(self, delta: int) -> None:
        self._stop_desktop_suggestion_scroll_hold()
        self._desktop_suggestion_scroll_job = self.after(
            260,
            lambda: self._continue_desktop_suggestion_scroll_hold(delta),
        )

    def _continue_desktop_suggestion_scroll_hold(self, delta: int) -> None:
        if not self._desktop_suggestion_popup_visible():
            self._stop_desktop_suggestion_scroll_hold()
            return
        self._move_desktop_suggestion(delta)
        self._desktop_suggestion_scroll_job = self.after(
            65,
            lambda: self._continue_desktop_suggestion_scroll_hold(delta),
        )

    def _stop_desktop_suggestion_scroll_hold(self) -> None:
        job = getattr(self, "_desktop_suggestion_scroll_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._desktop_suggestion_scroll_job = None

    def _apply_selected_desktop_suggestion(self) -> None:
        values = self.desktop_suggestion_values
        if not values:
            return
        index = self.desktop_suggestion_index
        listbox = getattr(self, "desktop_suggestion_listbox", None)
        if listbox is not None:
            selected = listbox.curselection()
            if selected:
                index = int(selected[0])
        if index < 0 or index >= len(values):
            index = 0
        self._set_desktop_entry_value(values[index])
        self._close_desktop_name_suggestions()
        self._update_desktop_input_state(focused=True)
        entry = getattr(self, "desktop_name_entry", None)
        if entry is not None:
            try:
                if entry.winfo_exists():
                    entry.icursor(tk.END)
            except tk.TclError:
                pass
        self._focus_desktop_name_entry()

    def _on_desktop_suggestion_mousewheel(self, event: tk.Event) -> str:
        listbox = getattr(self, "desktop_suggestion_listbox", None)
        if listbox is not None:
            units = -1 if event.delta > 0 else 1
            listbox.yview_scroll(units, "units")
        return "break"

    def _close_desktop_name_suggestions(self) -> None:
        self._stop_desktop_suggestion_scroll_hold()
        if not self._desktop_suggestion_popup_exists():
            return
        try:
            self.desktop_suggestion_popup.withdraw()
        except tk.TclError:
            pass

    def _close_desktop_suggestions_if_focus_left(self) -> None:
        if not self._desktop_suggestion_popup_exists():
            return
        try:
            focus = self.focus_get()
        except tk.TclError:
            self._close_desktop_name_suggestions()
            return
        popup = self.desktop_suggestion_popup
        entry = getattr(self, "desktop_name_entry", None)
        if focus is entry:
            return
        if focus is not None and str(focus).startswith(str(popup)):
            return
        self._close_desktop_name_suggestions()

    def _short_path_text(self, path: Path | str | None, max_chars: int = 86) -> str:
        if path is None:
            return "Not generated"
        text = str(path)
        if len(text) <= max_chars:
            return text
        keep_left = max_chars // 2 - 2
        keep_right = max_chars - keep_left - 3
        return f"{text[:keep_left]}...{text[-keep_right:]}"

    def _elapsed_seconds_from(self, started_at: float | None) -> float:
        if started_at is None:
            return 0.0
        return max(time.monotonic() - started_at, 0.0)

    def _format_duration(self, seconds: float | int | None) -> str:
        total_seconds = max(int(round(seconds or 0)), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _show_completion_notification(
        self,
        title: str,
        message: str,
        desktop_name: str,
        evidence_category: str | None = None,
        open_button_text: str = "Open",
        open_button_width: int = 94,
        passed_count: int | None = None,
        failed_count: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        screenshots_dir = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        if evidence_category:
            screenshots_dir = screenshots_dir / evidence_category
        modal = ctk.CTkToplevel(self)
        modal.title(title)
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text=title, text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(card, text=message, text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, padx=18, pady=(6, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))
        summary_parts = []
        if duration_seconds is not None:
            summary_parts.append(f"Time taken: {self._format_duration(duration_seconds)}")
        if passed_count is not None or failed_count is not None:
            summary_parts.append(f"Passed: {passed_count or 0}   Failed: {failed_count or 0}")
        if summary_parts:
            ctk.CTkLabel(
                card,
                text=" | ".join(summary_parts),
                text_color=THEME["text"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor=tk.W, padx=18, pady=(8, 0))
        ctk.CTkLabel(
            card,
            text=f"Screenshots folder: {self._short_path_text(screenshots_dir)}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=560,
        ).pack(anchor=tk.W, padx=18, pady=(8, 0))
        notice = ctk.CTkLabel(card, text="Evidence is available for review.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(
            actions,
            text=open_button_text,
            variant="primary",
            command=lambda: self._open_screenshots_folder(screenshots_dir, notice),
            height=34,
            min_width=open_button_width,
        ).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 620
        height = min(max(modal.winfo_reqheight(), 275), 365)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_build_doc_notification(self, desktop_name: str, report_path: Path) -> None:
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        modal = ctk.CTkToplevel(self)
        modal.title("Word Report Built")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Word Report Built", text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(card, text="Word document rebuilt from available screenshots.", text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, padx=18, pady=(6, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))
        ctk.CTkLabel(
            card,
            text=f"Word report: {self._short_path_text(report_path, 92)}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=560,
        ).pack(anchor=tk.W, padx=18, pady=(8, 0))
        notice = ctk.CTkLabel(card, text="Word report is available for review.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(actions, text="Download Report", variant="primary", command=lambda: self._download_word_report(report_path, notice), height=34, min_width=150).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(actions, text="Open Screenshots", variant="secondary", command=lambda: self._open_screenshots_folder(screenshots_root, notice), height=34, min_width=160).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 650
        height = min(max(modal.winfo_reqheight(), 285), 370)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_preflight_notification(self, result: PreflightResult) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Preflight Complete")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        status = "Ready" if result.ok else "Needs Attention"
        ctk.CTkLabel(card, text=f"Preflight {status}", text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(
            card,
            text=f"Warnings: {result.warning_count}   Failures: {result.failed_count}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(6, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {result.desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))

        details = ctk.CTkTextbox(
            card,
            height=170,
            fg_color=THEME["card_soft"],
            text_color=THEME["text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
            font=("Cascadia Mono", 10),
            wrap=tk.WORD,
        )
        details.pack(fill=tk.BOTH, expand=True, padx=18, pady=(10, 0))
        for item in result.items:
            details.insert(tk.END, f"[{item.status}] {item.name}: {item.message}\n")
        details.configure(state=tk.DISABLED)

        notice = ctk.CTkLabel(card, text="Preflight results are available above.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(
            actions,
            text="Open Evidence",
            variant="primary",
            command=lambda: self._open_folder_path(Path(result.evidence_root), notice, "Evidence folder opened."),
            height=34,
            min_width=130,
        ).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 720
        height = min(max(modal.winfo_reqheight(), 390), 500)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_evidence_audit_notification(self, result: EvidenceAuditResult) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Evidence Audit Complete")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Evidence Audit Complete", text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(
            card,
            text=(
                f"Present: {result.present_count}   Missing: {result.missing_count}   "
                f"Failed: {result.failed_count}   Warnings: {result.warning_count}"
            ),
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(6, 0))
        if result.manifest_exists:
            ctk.CTkLabel(
                card,
                text=(
                    f"Validation Failures: {result.validation_failed_count}   "
                    f"Validation Warnings: {result.validation_warning_count}   "
                    f"Manual Checks: {result.manual_check_count}"
                ),
                text_color=THEME["muted"],
                font=("Segoe UI", 10),
            ).pack(anchor=tk.W, padx=18, pady=(4, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {result.desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))
        report_message = "Word report: "
        if result.report_path:
            report_message += self._short_path_text(result.report_path, 92)
            if result.report_stale:
                report_message += " (older than latest screenshots)"
        else:
            report_message += "Not found"
        ctk.CTkLabel(card, text=report_message, text_color=THEME["muted"], font=("Segoe UI", 10), justify=tk.LEFT, wraplength=660).pack(anchor=tk.W, padx=18, pady=(8, 0))

        details = ctk.CTkTextbox(
            card,
            height=170,
            fg_color=THEME["card_soft"],
            text_color=THEME["text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
            font=("Cascadia Mono", 10),
            wrap=tk.WORD,
        )
        details.pack(fill=tk.BOTH, expand=True, padx=18, pady=(10, 0))
        visible_items = result.items[:40]
        for item in visible_items:
            details.insert(tk.END, f"[{item.status}] {item.section_title} / {item.subsection_title}\n")
            for note in item.notes[:2]:
                details.insert(tk.END, f"  - {note}\n")
        if len(result.items) > len(visible_items):
            details.insert(tk.END, f"... {len(result.items) - len(visible_items)} more item(s). See audit JSON.\n")
        if result.manifest_path:
            details.insert(tk.END, f"\nRun manifest: {result.manifest_path}\n")
        if result.audit_path:
            details.insert(tk.END, f"\nAudit log: {result.audit_path}\n")
        details.configure(state=tk.DISABLED)

        notice = ctk.CTkLabel(card, text="Evidence audit log is available for review.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(
            actions,
            text="Open Evidence",
            variant="primary",
            command=lambda: self._open_folder_path(Path(result.evidence_root), notice, "Evidence folder opened."),
            height=34,
            min_width=130,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(
            actions,
            text="Build Doc",
            variant="secondary",
            command=self.build_word_document,
            height=34,
            min_width=110,
        ).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 760
        height = min(max(modal.winfo_reqheight(), 410), 540)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_support_bundle_notification(self, desktop_name: str, bundle_path: Path) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Support Bundle Created")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Support Bundle Created", text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(card, text="Logs, audit data, failed screenshots, and report context were collected.", text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, padx=18, pady=(6, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))
        ctk.CTkLabel(
            card,
            text=f"Bundle: {self._short_path_text(bundle_path, 92)}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=560,
        ).pack(anchor=tk.W, padx=18, pady=(8, 0))
        notice = ctk.CTkLabel(card, text="Support bundle is ready to share internally.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(
            actions,
            text="Open Folder",
            variant="primary",
            command=lambda: self._open_folder_path(bundle_path.parent, notice, "Support bundle folder opened."),
            height=34,
            min_width=130,
        ).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 650
        height = min(max(modal.winfo_reqheight(), 285), 370)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_scheduled_complete_notification(
        self,
        results: list[dict[str, object]],
        stopped: bool = False,
        manual_check_required: bool = False,
    ) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Scheduled Complete Testing Finished")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(True, True)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        completed_count = len(results)
        total_count = len(self.scheduled_desktops) or completed_count
        passed_count = sum(1 for item in results if str(item.get("status")) == "Pass")
        failed_count = sum(1 for item in results if str(item.get("status")) == "Fail")
        status_text = "Stopped" if stopped else "Manual check required" if manual_check_required else "Finished"
        ctk.CTkLabel(
            card,
            text=f"Scheduled Complete Testing {status_text}",
            text_color=THEME["text"],
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(
            card,
            text=f"Testing was performed for {completed_count} of {total_count} desktop(s). Passed: {passed_count}   Failed: {failed_count}",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(7, 0))

        notice = ctk.CTkLabel(
            card,
            text="Use the buttons below to navigate to each desktop's evidence.",
            text_color=THEME["teal"],
            font=("Segoe UI", 10, "bold"),
        )
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))

        rows = ctk.CTkScrollableFrame(
            card,
            height=min(300, max(150, len(results) * 52)),
            fg_color=THEME["card_soft"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
        )
        rows.pack(fill=tk.BOTH, expand=True, padx=18, pady=(12, 0))
        rows.grid_columnconfigure(1, weight=1)
        for row_index, item in enumerate(results):
            desktop_name = str(item.get("desktop_name") or "")
            status = str(item.get("status") or "Unknown")
            report_text = str(item.get("report_path") or "")
            report_path = Path(report_text) if report_text else None
            screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
            row = ctk.CTkFrame(rows, fg_color=THEME["input"], corner_radius=9)
            row.grid(row=row_index, column=0, sticky="ew", padx=6, pady=(6, 0))
            row.grid_columnconfigure(1, weight=1)
            StatusBadge(row, text=status).grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=8)
            ctk.CTkLabel(
                row,
                text=desktop_name,
                text_color=THEME["text"],
                font=("Segoe UI", 11, "bold"),
            ).grid(row=0, column=1, sticky="w", pady=(8, 0))
            ctk.CTkLabel(
                row,
                text=(
                    f"{item.get('passed_count', 0)} of {item.get('total_count', 0)} passed | "
                    f"Time {self._format_duration(float(item.get('duration_seconds') or 0))}"
                ),
                text_color=THEME["muted"],
                font=("Segoe UI", 9),
            ).grid(row=1, column=1, sticky="w", pady=(1, 8))
            ModernButton(
                row,
                text="Screenshots",
                variant="secondary",
                command=lambda folder=screenshots_root: self._open_screenshots_folder(folder, notice),
                height=28,
                min_width=108,
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=2, rowspan=2, padx=(8, 6), pady=8)
            download_button = ModernButton(
                row,
                text="Download Report",
                variant="primary",
                command=lambda path=report_path: self._download_word_report(path, notice),
                height=28,
                min_width=128,
                font=("Segoe UI", 9, "bold"),
            )
            download_button.grid(row=0, column=3, rowspan=2, padx=(0, 10), pady=8)
            if report_path is None or not report_path.exists():
                download_button.configure(state=tk.DISABLED)

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)

        modal.update_idletasks()
        width = 820
        height = min(max(modal.winfo_reqheight(), 380), 560)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_complete_testing_notification(self, desktop_name: str, result: CompleteExecutionResult) -> None:
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        modal = ctk.CTkToplevel(self)
        modal.title("Complete Testing Finished")
        modal.configure(fg_color=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, 18, 16)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Complete Testing Finished", text_color=THEME["text"], font=("Segoe UI", 16, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 0))
        ctk.CTkLabel(card, text="Complete Testing execution finished.", text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, padx=18, pady=(6, 0))
        ctk.CTkLabel(card, text=f"Citrix Desktop Name: {desktop_name}", text_color=THEME["text"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(12, 0))
        summary_parts = [
            f"Mandatory: {result.mandatory_status}",
            f"Shakedown: {result.shakedown_status}",
        ]
        if is_silo43_desktop(desktop_name):
            summary_parts.append(f"Silo43: {getattr(result, 'silo43_status', 'Skipped')}")
        summary_parts.append(f"IAT: {result.iat_status}")
        summary = "   ".join(summary_parts)
        ctk.CTkLabel(card, text=summary, text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, padx=18, pady=(7, 0))
        ctk.CTkLabel(
            card,
            text=(
                f"Time taken: {self._format_duration(result.duration_seconds)} | "
                f"Passed: {result.passed_count}   Failed: {max(result.total_count - result.passed_count, 0)}"
            ),
            text_color=THEME["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, padx=18, pady=(7, 0))
        ctk.CTkLabel(
            card,
            text=(
                f"Word report: {self._short_path_text(result.report_path, 92)}\n"
                f"Master log: {self._short_path_text(result.log_path, 92)}"
            ),
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=700,
        ).pack(anchor=tk.W, padx=18, pady=(8, 0))
        notice = ctk.CTkLabel(card, text="Word report and screenshots are available for review.", text_color=THEME["teal"], font=("Segoe UI", 10, "bold"))
        notice.pack(anchor=tk.W, padx=18, pady=(10, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill=tk.X, padx=18, pady=(14, 16))
        ModernButton(actions, text="Download Report", variant="primary", command=lambda: self._download_word_report(result.report_path, notice), height=34, min_width=160).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(actions, text="Open Screenshots Folder", variant="secondary", command=lambda: self._open_screenshots_folder(screenshots_root, notice), height=34, min_width=210).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 760
        height = min(max(modal.winfo_reqheight(), 315), 420)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _download_word_report(self, report_path: Path | None, notice: ctk.CTkLabel | None = None) -> None:
        if report_path is None:
            if notice is not None:
                notice.configure(text="Word report was not generated for this run.", text_color=THEME["danger"])
            else:
                messagebox.showinfo("No Report Found", "Word report was not generated for this run.")
            return
        if not report_path.exists():
            if notice is not None:
                notice.configure(text=f"Word report was not found:\n{report_path}", text_color=THEME["danger"])
            else:
                messagebox.showerror("Report Not Found", f"Word report was not found:\n\n{report_path}")
            return

        downloads_dir = Path.home() / "Downloads"
        initial_dir = downloads_dir if downloads_dir.exists() else report_path.parent
        target = filedialog.asksaveasfilename(
            title="Download Word Report",
            initialdir=str(initial_dir),
            initialfile=report_path.name,
            defaultextension=".docx",
            filetypes=[("Word documents", "*.docx"), ("All files", "*.*")],
        )
        if not target:
            return

        target_path = Path(target)
        try:
            if target_path.resolve() != report_path.resolve():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report_path, target_path)
            self.latest_report_path = report_path
            self._append_message(f"Word report downloaded: {target_path}")
            if notice is not None:
                notice.configure(text=f"Report downloaded to:\n{target_path}", text_color=THEME["teal"])
            else:
                messagebox.showinfo("Report Downloaded", f"Word report downloaded to:\n\n{target_path}")
        except OSError as exc:
            if notice is not None:
                notice.configure(text=f"Could not download Word report: {exc}", text_color=THEME["danger"])
            else:
                messagebox.showerror("Download Failed", f"Could not download Word report:\n\n{exc}")

    def _open_word_report(self, report_path: Path | None, notice: ctk.CTkLabel) -> None:
        self._download_word_report(report_path, notice)

    def _open_screenshots_folder(self, screenshots_dir: Path, notice: ctk.CTkLabel) -> None:
        if not screenshots_dir.exists():
            notice.configure(text=f"Screenshots folder was not found:\n{screenshots_dir}", text_color=THEME["danger"])
            return
        try:
            subprocess.Popen(["explorer", str(screenshots_dir)])
            notice.configure(text="Screenshots folder opened.", text_color=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open folder: {exc}", text_color=THEME["danger"])

    def _open_folder_path(self, folder_path: Path, notice: ctk.CTkLabel, success_message: str = "Folder opened.") -> None:
        if not folder_path.exists():
            notice.configure(text=f"Folder was not found:\n{folder_path}", text_color=THEME["danger"])
            return
        try:
            subprocess.Popen(["explorer", str(folder_path)])
            notice.configure(text=success_message, text_color=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open folder: {exc}", text_color=THEME["danger"])

    def _set_test_card_state(self, test_case_id: str, status: str) -> None:
        card = self.test_cards.get(test_case_id)
        if card is None:
            return
        self.test_card_states[test_case_id] = status
        bg = THEME["card_running"] if status in {"Running", "Paused"} else THEME["card_soft"]
        _, status_color = STATUS_BADGES.get(status, STATUS_BADGES["Idle"])
        border_color = THEME["card_running_glow"] if status in {"Running", "Paused"} else status_color if status in {"Pass", "Fail", "Skipped", "Stopped"} else THEME["border"]
        card.configure(fg_color=bg, border_color=border_color, border_width=2 if status in {"Running", "Paused"} else 1)
        self._set_frame_tree_bg(card, bg)
        accent = self.test_card_accents.get(test_case_id)
        if accent is not None:
            accent.configure(fg_color=THEME["primary"] if status in {"Running", "Paused"} else status_color if status in {"Pass", "Fail", "Skipped", "Stopped"} else THEME["border"])

    def _set_frame_tree_bg(self, widget: tk.Widget, bg: str) -> None:
        try:
            if isinstance(widget, ctk.CTkFrame):
                widget.configure(fg_color=bg)
            elif isinstance(widget, ctk.CTkLabel) and not isinstance(widget, StatusBadge):
                widget.configure(fg_color="transparent", text_color=THEME["text"])
            elif isinstance(widget, ctk.CTkCheckBox):
                widget.configure(
                    text_color=THEME["text"],
                    fg_color=THEME["primary"],
                    hover_color=THEME["primary_hover"],
                    border_color=THEME["muted"],
                    checkmark_color="#ffffff",
                )
        except tk.TclError:
            return
        for child in widget.winfo_children():
            if isinstance(child, (ModernButton, StatusBadge, ProgressRingPanel, ctk.CTkTextbox, ctk.CTkComboBox, ctk.CTkEntry, ctk.CTkProgressBar)):
                continue
            self._set_frame_tree_bg(child, bg)

    def _is_error_log_message(self, message: str) -> bool:
        lower_message = message.casefold()
        return "error" in lower_message or "failed" in lower_message or "fail:" in lower_message

    def _log_color(self, message: str) -> str:
        lower_message = message.casefold()
        if self._is_error_log_message(message):
            return THEME["console_error"]
        if "warning" in lower_message or "stopped" in lower_message:
            return THEME["console_warning"]
        return THEME["console_muted"]

    def _render_execution_messages(self) -> None:
        if not hasattr(self, "message_box"):
            return
        self._clear_log_textbox()
        for message in self.log_entries:
            if self.log_errors_only and not self._is_error_log_message(message):
                continue
            self._insert_log_message(message, settle=False)
        self._update_error_filter_button()

    def _append_message(self, message: str) -> None:
        self.log_entries.append(message)
        trimmed_and_rendered = self._trim_log_history_if_needed()
        if self.log_errors_only and not self._is_error_log_message(message):
            return
        if trimmed_and_rendered:
            return
        self._insert_log_message(message, settle=True)
        self._update_error_filter_button()

    def _trim_log_history_if_needed(self) -> bool:
        if self.max_log_lines <= 0 or len(self.log_entries) <= self.max_log_lines:
            return False
        trim_to = max(self.max_log_lines - 200, 0)
        remove_count = max(len(self.log_entries) - trim_to, 0)
        if remove_count <= 0:
            return False
        del self.log_entries[:remove_count]
        if hasattr(self, "message_box"):
            self._render_execution_messages()
            return True
        return False

    def _insert_log_message(self, message: str, settle: bool) -> None:
        self.message_box.configure(state=tk.NORMAL)
        try:
            tag_name = f"log_{self.message_box.index(tk.END).replace('.', '_')}"
        except tk.TclError:
            tag_name = f"log_{int(time.time() * 1000)}"
        self.message_box.insert(tk.END, f"{message}\n", tag_name)
        color = self._log_color(message)
        if settle and color == THEME["console_muted"]:
            color = THEME["console_text"]
        self._configure_textbox_tag(tag_name, color)
        self.message_box.see(tk.END)
        self.message_box.configure(state=tk.DISABLED)
        # Long runs can emit hundreds of log lines; avoid one delayed UI callback per line.

    def _configure_textbox_tag(self, tag_name: str, color: str) -> None:
        try:
            self.message_box.tag_config(tag_name, foreground=color)
        except (AttributeError, tk.TclError):
            try:
                self.message_box._textbox.tag_configure(tag_name, foreground=color)
            except (AttributeError, tk.TclError):
                pass

    def _settle_log_line(self, tag_name: str) -> None:
        try:
            self.message_box.configure(state=tk.NORMAL)
            self._configure_textbox_tag(tag_name, THEME["console_text"])
            self.message_box.configure(state=tk.DISABLED)
        except tk.TclError:
            pass


def _format_elapsed(seconds: int) -> str:
    minutes, secs = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def main(root_dir: Path) -> None:
    app = AutomationApp(root_dir)
    app.mainloop()
