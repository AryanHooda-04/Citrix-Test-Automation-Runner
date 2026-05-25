from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import customtkinter as ctk
import tkinter as tk
from pathlib import Path
from threading import Event
from tkinter import messagebox

from core.config import AppConfig, load_config
from core.desktop_history import DesktopNameHistory
from core.execution_log import desktop_scoped_path
from core.master_runner import (
    CompleteExecutionResult,
    CompleteTestingRunner,
    MasterExecutionResult,
    MasterRunner,
    ShakedownRunner,
)
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused
from core.runner import ExecutionResult, TestRunner
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    IAT_TEST_CASE_ORDER,
    MANDATORY_EVIDENCE_FOLDER,
    MANDATORY_TEST_CASE_ORDER,
    SHAKEDOWN_EVIDENCE_FOLDER,
    SHAKEDOWN_TEST_CASE_ORDER,
    evidence_category_for_test_name,
    is_success_status,
    mandatory_order_for_desktop,
)
from core.test_loader import TestCase, discover_test_cases
from core.word_report import REPORT_STRUCTURE, generate_complete_testing_report


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

        ctk.CTkLabel(
            self.info_frame,
            text="Run Progress",
            text_color=THEME["text"],
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=tk.W, pady=(2, 0))

        self.scope_label = ctk.CTkLabel(
            self.info_frame,
            text="Ready",
            text_color=THEME["muted"],
            font=("Segoe UI", 11, "bold"),
            anchor=tk.W,
        )
        self.scope_label.pack(anchor=tk.W, fill=tk.X, pady=(9, 0))

        status_row = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        status_row.pack(anchor=tk.W, pady=(9, 0))
        self.status_badge = StatusBadge(status_row, text="Idle")
        self.status_badge.pack(side=tk.LEFT)
        self.count_label = ctk.CTkLabel(
            status_row,
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
        ctk.CTkLabel(
            tile,
            text=label,
            text_color=THEME["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor=tk.W,
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
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
        return value_label

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
        self.desktop_history = DesktopNameHistory(self.config)
        self.test_cases: list[TestCase] = []
        self.status_labels: dict[str, StatusBadge] = {}
        self.run_buttons: dict[str, ModernButton] = {}
        self.stop_buttons: dict[str, ModernButton] = {}
        self.pause_buttons: dict[str, ModernButton] = {}
        self.test_cards: dict[str, ctk.CTkFrame] = {}
        self.test_card_accents: dict[str, ctk.CTkFrame] = {}
        self.test_card_states: dict[str, str] = {}
        self.description_labels: dict[str, ctk.CTkLabel] = {}
        self.description_buttons: dict[str, ModernButton] = {}
        self.description_expanded: dict[str, bool] = {}
        self.section_containers: dict[str, ctk.CTkFrame] = {}
        self.section_buttons: dict[str, ModernButton] = {}
        self.section_selected_buttons: dict[str, ModernButton] = {}
        self.section_pause_buttons: dict[str, ModernButton] = {}
        self.section_stop_buttons: dict[str, ModernButton] = {}
        self.section_selection_labels: dict[str, ctk.CTkLabel] = {}
        self.section_test_ids: dict[str, list[str]] = {}
        self.section_collapsed: dict[str, bool] = {}
        self.selection_vars: dict[str, tk.BooleanVar] = {}
        self.selection_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self.global_selected_button: ModernButton | None = None
        self.global_selected_pause_button: ModernButton | None = None
        self.global_selected_stop_button: ModernButton | None = None
        self.selected_progress_label: ctk.CTkLabel | None = None
        self.progress_panel: ProgressRingPanel | None = None
        self.events: queue.Queue = queue.Queue()
        self.desktop_name_var = tk.StringVar(value="")
        self.refresh_button: ModernButton | None = None
        self.theme_button: ModernButton | None = None
        self.complete_button: ModernButton | None = None
        self.complete_pause_button: ModernButton | None = None
        self.complete_stop_button: ModernButton | None = None
        self.complete_status_label: StatusBadge | None = None
        self.complete_progress_label: ctk.CTkLabel | None = None
        self.complete_runtime_label: ctk.CTkLabel | None = None
        self.complete_card: ctk.CTkFrame | None = None
        self.dry_run_button: ModernButton | None = None
        self.latest_report_button: ModernButton | None = None
        self.master_button: ModernButton | None = None
        self.master_pause_button: ModernButton | None = None
        self.master_stop_button: ModernButton | None = None
        self.master_status_label: StatusBadge | None = None
        self.master_progress_label: ctk.CTkLabel | None = None
        self.master_card: ctk.CTkFrame | None = None
        self.shakedown_button: ModernButton | None = None
        self.shakedown_pause_button: ModernButton | None = None
        self.shakedown_stop_button: ModernButton | None = None
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
        self.content_frame: ctk.CTkFrame | None = None
        self.log_width_label: ctk.CTkLabel | None = None
        self.log_filter_button: ModernButton | None = None
        self.log_entries: list[str] = []
        self.log_errors_only = False
        self.log_panel_width = 420
        self.log_splitter: tk.Frame | None = None
        self._log_resize_start_x = 0
        self._log_resize_start_width = self.log_panel_width
        self._test_cases_resize_job: str | None = None
        self.active_stop_event: Event | None = None
        self.active_pause_event: Event | None = None
        self.active_paused = False
        self.active_test_id: str | None = None
        self.active_selected_section: str | None = None
        self.active_mode: str | None = None
        self.theme_name = "light"
        self.master_completed_count = 0
        self.master_total_count = 0
        self.shakedown_completed_count = 0
        self.shakedown_total_count = 0
        self.complete_completed_count = 0
        self.complete_total_count = 0
        self.complete_started_monotonic: float | None = None
        self.complete_current_phase = "Idle"
        self.complete_current_test = "None"
        self.selected_completed_count = 0
        self.selected_total_count = 0
        self.selected_section_title = ""
        self.active_sequence_ids: list[str] = []
        self.active_sequence_started_monotonic: float | None = None
        self.latest_report_path: Path | None = None

        self.title("Citrix Test Automation Runner")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(fg_color=THEME["bg"])

        self._configure_styles()
        self._build_layout()
        self.refresh_tests()
        self.after_idle(self._settle_progress_panel_layout)
        self.after(250, self._settle_progress_panel_layout)
        self.after(150, self._process_events)

    def _read_app_version(self) -> str:
        version_path = self.root_dir / "version.txt"
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            version = ""
        return version or "dev"

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
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self.active_pause_event = None
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = None
        self.active_sequence_ids = []
        self.active_sequence_started_monotonic = None
        self._update_selection_cues()
        self._set_buttons_enabled(True)
        self._update_desktop_input_state()

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
        try:
            os.startfile(str(report_path))
            self.latest_report_path = report_path
            self._append_message(f"Latest Word report opened: {report_path}")
        except OSError as exc:
            messagebox.showerror("Open Report Failed", f"Could not open Word report:\n\n{exc}")

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
        log_path = self._find_latest_complete_testing_log_path(desktop_name)
        if log_path is None:
            return
        try:
            report_path = generate_complete_testing_report(
                log_path=log_path,
                screenshots_base_dir=self.config.path("screenshots_dir"),
                desktop_name=desktop_name,
            )
            self.latest_report_path = report_path
            self._append_message(f"Word report refreshed with latest rerun evidence: {report_path}")
        except Exception as exc:
            self._append_message(f"Word report refresh after rerun failed: {exc}")

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

    def _update_selection_cues(self) -> None:
        total_selected = len(self._selected_test_case_ids())
        for title, label in self.section_selection_labels.items():
            selected_count = len(self._selected_test_case_ids(title))
            if selected_count:
                label.configure(text=f"Custom selection mode active: {selected_count} selected")
            else:
                label.configure(text="")
        if self.selected_progress_label is not None and self.active_mode is None:
            if total_selected:
                self.selected_progress_label.configure(
                    text=f"{total_selected} testcase(s) selected across all sections."
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

    def run_test(self, test_case: TestCase) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return

        self._set_status(test_case.id, "Running")
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._enable_stop_button(test_case.id)
        self._set_pause_button_enabled(test_case.id, True)
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
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
            args=(test_case, desktop_name, self.active_stop_event, self.active_pause_event),
            daemon=True,
        )
        thread.start()

    def run_complete_testing(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self._focus_desktop_name_entry()
            return

        self._set_complete_status("Running")
        self._set_master_status("Idle")
        self._set_shakedown_status("Idle")
        self.complete_completed_count = 0
        self.complete_total_count = len(mandatory_order_for_desktop(desktop_name)) + len(SHAKEDOWN_TEST_CASE_ORDER) + len(IAT_TEST_CASE_ORDER) + 1
        self.complete_started_monotonic = time.monotonic()
        self.complete_current_phase = "Starting"
        self.complete_current_test = "Preparing"
        self._set_complete_progress(f"0 of {self.complete_total_count} completed")
        self._set_complete_runtime_summary()
        self._set_master_progress("Ready")
        self._set_shakedown_progress("Ready")
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(True)
        self._set_complete_pause_enabled(True)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = None
        self.active_mode = "complete"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")
        self._append_message("Starting Perform Complete Testing")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")
        self.after(1000, self._tick_complete_runtime)

        thread = threading.Thread(
            target=self._run_complete_worker,
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event),
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

        self._set_master_status("Running")
        self.master_completed_count = 0
        self.master_total_count = len(mandatory_order_for_desktop(desktop_name))
        self.active_sequence_ids = self._test_ids_for_test_names(mandatory_order_for_desktop(desktop_name))
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_master_progress(self._sequence_progress_text(0, self.master_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(True)
        self._set_master_pause_enabled(True)
        self._set_shakedown_stop_enabled(False)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
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
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event),
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

        self._set_shakedown_status("Running")
        self.shakedown_completed_count = 0
        self.shakedown_total_count = len(SHAKEDOWN_TEST_CASE_ORDER)
        self.active_sequence_ids = self._test_ids_for_test_names(SHAKEDOWN_TEST_CASE_ORDER)
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_shakedown_progress(self._sequence_progress_text(0, self.shakedown_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(True)
        self._set_shakedown_pause_enabled(True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
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
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event),
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

        self.selected_completed_count = 0
        self.selected_total_count = len(selected_tests)
        display_title = section_title or "Selected Testcases"
        self.selected_section_title = display_title
        self.active_sequence_ids = list(selected_ids)
        self.active_sequence_started_monotonic = time.monotonic()
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(section_title == "Mandatory Testcases")
        self._set_shakedown_stop_enabled(section_title == "Shakedown Testcases")
        if section_title is not None:
            self._set_section_stop_enabled(section_title, True)
            self._set_section_pause_enabled(section_title, True)
        self._set_global_selected_controls_enabled(True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
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
            args=(display_title, selected_tests, desktop_name, self.active_stop_event, self.active_pause_event),
            daemon=True,
        )
        thread.start()

    def request_stop(self, label: str) -> None:
        if self.active_stop_event is None or self.active_stop_event.is_set():
            return
        self.active_stop_event.set()
        if self.active_pause_event is not None:
            self.active_pause_event.clear()
        self.active_paused = False
        self._append_message(f"Stop requested: {label}")
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(False)
        self._set_shakedown_stop_enabled(False)

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

    def _run_test_worker(
        self,
        test_case: TestCase,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        runner = TestRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            stop_event=stop_event,
            pause_event=pause_event,
        )
        result = runner.run(test_case)
        self.events.put(("complete", test_case, result))

    def _run_mandatory_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        runner = MasterRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            manual_confirmation_callback=lambda result: self.events.put(("manual_confirmation_pause", result)),
            stop_event=stop_event,
            pause_event=pause_event,
        )
        result = runner.run(test_cases)
        self.events.put(("master_complete", result))

    def _run_complete_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        runner = CompleteTestingRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            phase_status_callback=lambda phase, status: self.events.put(("phase_status", phase, status)),
            manual_confirmation_callback=lambda result: self.events.put(("manual_confirmation_pause", result)),
            stop_event=stop_event,
            pause_event=pause_event,
        )
        result = runner.run(test_cases)
        self.events.put(("complete_testing_complete", result))

    def _run_shakedown_worker(
        self,
        test_cases: list[TestCase],
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        runner = ShakedownRunner(
            config=self.config,
            citrix_desktop_name=desktop_name,
            status_callback=lambda message: self.events.put(("message", message)),
            test_status_callback=lambda test_id, status: self.events.put(("test_status", test_id, status)),
            stop_event=stop_event,
            pause_event=pause_event,
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
    ) -> None:
        failed_count = 0
        stopped = False
        manual_check_message = None
        for index, test_case in enumerate(selected_tests):
            if stop_event.is_set():
                stopped = True
                break
            try:
                wait_if_paused(pause_event, stop_event)
            except StopRequested:
                stopped = True
                break
            self.events.put(("test_status", test_case.id, "Running"))
            self.events.put(("message", f"Selected sequence running: {test_case.name}"))
            result = TestRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            ).run(test_case)
            self.events.put(("test_status", test_case.id, result.status))
            if not is_success_status(result.status):
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
            if result.status == "Stopped":
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
            return self.config.wait("mandatory_between_tests_wait_sec", 30.0)
        if delay_section == "Shakedown Testcases":
            return self.config.wait("shakedown_between_tests_wait_sec", 10.0)
        return 0.0

    def _process_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            event_type = event[0]
            if event_type == "message":
                self._append_message(event[1])
            elif event_type == "complete":
                _, test_case, result = event
                self._handle_result(test_case, result)
            elif event_type == "test_status":
                _, test_case_id, status = event
                if test_case_id in self.status_labels:
                    self._set_status(test_case_id, status)
                if self.active_mode == "master":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._set_master_progress(self._sequence_progress_text(self.master_completed_count, self.master_total_count, test_case_id, self.active_sequence_ids, self.active_sequence_started_monotonic))
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.master_completed_count = min(
                            self.master_completed_count + 1,
                            self.master_total_count,
                        )
                        self._set_master_progress(self._sequence_progress_text(self.master_completed_count, self.master_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                elif self.active_mode == "shakedown":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._set_shakedown_progress(self._sequence_progress_text(self.shakedown_completed_count, self.shakedown_total_count, test_case_id, self.active_sequence_ids, self.active_sequence_started_monotonic))
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.shakedown_completed_count = min(
                            self.shakedown_completed_count + 1,
                            self.shakedown_total_count,
                        )
                        self._set_shakedown_progress(self._sequence_progress_text(self.shakedown_completed_count, self.shakedown_total_count, None, self.active_sequence_ids, self.active_sequence_started_monotonic))
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                elif self.active_mode == "complete":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
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
                        self._set_complete_runtime_summary()
                elif self.active_mode and self.active_mode.startswith("selected:"):
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                        self._set_selected_progress(test_case_id)
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.selected_completed_count = min(
                            self.selected_completed_count + 1,
                            self.selected_total_count,
                        )
                        self._set_selected_progress()
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
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
                _, result = event
                self._handle_complete_testing_result(result)
            elif event_type == "selected_complete":
                _, section_title, status, failed_count, *manual_check = event
                manual_check_message = manual_check[0] if manual_check else None
                self._handle_selected_result(section_title, status, failed_count, manual_check_message)

        self.after(150, self._process_events)

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
            opened_target = screenshot_path
            try:
                subprocess.Popen(["explorer.exe", f"/select,{screenshot_path}"])
                self._append_message(f"Opened Hostname/IP evidence screenshot: {screenshot_path}")
            except OSError as exc:
                self._append_message(f"Unable to open Hostname/IP evidence screenshot: {exc}")
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
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{result.test_case_name}: {result.status}")
        self._append_message(f"Log: {result.log_path}")
        if result.screenshot_path:
            self._append_message(f"Screenshot: {result.screenshot_path}")
        if result.status == "Stopped":
            messagebox.showinfo("Test Stopped", f"{result.test_case_name} was stopped.")
        elif result.status == "Skipped":
            desktop_name = self._normalized_desktop_name()
            self._append_message(f"{result.test_case_name} skipped for Ring0 desktop.")
            self._record_successful_desktop_name(desktop_name)
        elif result.manual_confirmation_required:
            self._handle_manual_confirmation_pause(result)
            desktop_name = self._normalized_desktop_name()
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
            desktop_name = self._normalized_desktop_name()
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
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Mandatory Testcases: {result.status}")
        self._append_message(f"Master log: {result.log_path}")
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
            desktop_name = self._normalized_desktop_name()
            self._record_successful_desktop_name(desktop_name)
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
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Shakedown Testcases: {result.status}")
        self._append_message(f"Shakedown master log: {result.log_path}")
        if result.status == "Stopped":
            self._set_shakedown_progress(f"Stopped at {self.shakedown_completed_count} of {self.shakedown_total_count}")
            messagebox.showinfo("Shakedown Testcases Stopped", f"Shakedown Testcases were stopped.\n\nMaster log:\n{result.log_path}")
        else:
            self._set_shakedown_progress(f"{self.shakedown_completed_count} of {self.shakedown_total_count} completed")
            desktop_name = self._normalized_desktop_name()
            if result.status == "Pass":
                self._record_successful_desktop_name(desktop_name)
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

    def _handle_complete_testing_result(self, result: CompleteExecutionResult) -> None:
        self._set_complete_status(result.status)
        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"Perform Complete Testing: {result.status}")
        self._append_message(f"Complete Testing log: {result.log_path}")
        self.latest_report_path = result.report_path
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
        desktop_name = self._normalized_desktop_name()
        if result.status == "Pass":
            self._record_successful_desktop_name(desktop_name)
        self._show_complete_testing_notification(desktop_name, result)

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

        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{section_title} selected run: {status}")

        desktop_name = self._normalized_desktop_name()
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
            self.complete_current_phase = "Post-complete Evidence" if status == "Running" else f"Post-complete Evidence {status}"
            self.complete_current_test = "ZScaler Services second screenshot"
            if status in {"Pass", "Fail", "Skipped", "Stopped"} and self.active_mode == "complete":
                self.complete_completed_count = min(self.complete_completed_count + 1, self.complete_total_count)
                self._set_complete_progress(f"{self.complete_completed_count} of {self.complete_total_count} completed")
        if self.active_mode == "complete":
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
        if self.active_mode != "complete" or self.complete_started_monotonic is None:
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
        if test_name in SHAKEDOWN_TEST_CASE_ORDER:
            return "Shakedown Testcases"
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
            self._show_desktop_name_suggestions()
            self._move_desktop_suggestion(1)
            return "break"
        if event.keysym == "Up":
            self._show_desktop_name_suggestions()
            self._move_desktop_suggestion(-1)
            return "break"
        if event.keysym == "Return":
            if self._desktop_suggestion_popup_exists():
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

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.run_buttons.values():
            button.configure(state=state)
        for button in self.section_selected_buttons.values():
            button.configure(state=state)
        if self.global_selected_button is not None:
            self.global_selected_button.configure(state=state)
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
        if self.shakedown_button is not None:
            self.shakedown_button.configure(state=state)
        if self.refresh_button is not None:
            self.refresh_button.configure(state=state)
        if self.theme_button is not None:
            self.theme_button.configure(state=state)
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

    def _disable_row_pause_buttons(self) -> None:
        for button in self.pause_buttons.values():
            button.configure(state=tk.DISABLED)

    def _disable_all_stop_buttons(self) -> None:
        for button in self.stop_buttons.values():
            button.configure(state=tk.DISABLED)
        for button in self.section_stop_buttons.values():
            button.configure(state=tk.DISABLED)
        if self.global_selected_stop_button is not None:
            self.global_selected_stop_button.configure(state=tk.DISABLED)

    def _set_master_stop_enabled(self, enabled: bool) -> None:
        if self.master_stop_button is not None:
            self.master_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_complete_stop_enabled(self, enabled: bool) -> None:
        if self.complete_stop_button is not None:
            self.complete_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_shakedown_stop_enabled(self, enabled: bool) -> None:
        if self.shakedown_stop_button is not None:
            self.shakedown_stop_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)

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

    def _set_global_selected_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        if self.global_selected_stop_button is not None:
            self.global_selected_stop_button.configure(state=state)
        if self.global_selected_pause_button is not None:
            self.global_selected_pause_button.configure(state=state)

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
        self.active_stop_event = None
        self.active_pause_event = None
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
        self._update_selection_cues()

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
        self.input_card.configure(height=116)
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
            fg_color=THEME["input"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=9,
        )
        self.input_shell.pack(fill=tk.X, padx=12, pady=(6, 0))
        self.input_shell.grid_columnconfigure(0, weight=1)
        self.desktop_suggestion_values = self._desktop_dropdown_values()
        self.desktop_name_entry = ctk.CTkEntry(
            self.input_shell,
            placeholder_text="Type silo name, e.g. SILO07-TEST-AP1",
            state="normal",
            height=32,
            corner_radius=8,
            fg_color=THEME["input"],
            border_width=0,
            text_color=THEME["text"],
            placeholder_text_color=THEME["muted"],
            font=("Segoe UI", 12),
        )
        self.desktop_name_entry.grid(row=0, column=0, sticky="ew", padx=(6, 4), pady=5)
        if self.desktop_name_var.get():
            self._set_desktop_entry_value(self.desktop_name_var.get())
        self.desktop_dropdown_button = ModernButton(
            self.input_shell,
            text="⌄",
            variant="primary",
            command=self._toggle_desktop_name_suggestions,
            height=30,
            min_width=34,
            radius=7,
            font=("Segoe UI", 14, "bold"),
        )
        self.desktop_dropdown_button.grid(row=0, column=1, sticky="e", padx=(0, 5), pady=5)
        self.desktop_name_entry.bind("<FocusIn>", lambda _event: self._update_desktop_input_state(focused=True))
        self.desktop_name_entry.bind("<FocusOut>", self._on_desktop_name_focus_out)
        self.desktop_name_entry.bind("<KeyPress>", self._on_desktop_name_keypress)
        self.desktop_name_entry.bind("<KeyRelease>", self._on_desktop_name_keyrelease)
        self._update_desktop_input_state()
        ctk.CTkLabel(
            self.input_card,
            text="Example: SILO01-TEST. The app automatically targets the matching Citrix Desktop Viewer window.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, padx=12, pady=(5, 2))
        self.desktop_shortcuts_frame = ctk.CTkFrame(self.input_card, height=24, fg_color="transparent")
        self.desktop_shortcuts_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self._refresh_desktop_shortcuts()

        self.complete_card = self._make_card(left_panel, 12, 12)
        self.complete_card.configure(height=88)
        self.complete_card.pack_propagate(False)
        self.complete_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._build_complete_card()

        self.master_card = self._make_card(left_panel, 12, 12)
        self.master_card.configure(height=74)
        self.master_card.pack_propagate(False)
        self.master_card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._build_sequence_card(
            self.master_card,
            title="Run All Mandatory Testcases",
            subtitle="Executes the mandatory evidence sequence in the configured order.",
            status_attr="master_status_label",
            progress_attr="master_progress_label",
            run_attr="master_button",
            pause_attr="master_pause_button",
            stop_attr="master_stop_button",
            run_command=self.run_mandatory_testcases,
            pause_command=lambda: self.request_pause_resume("Mandatory Testcases"),
            stop_command=lambda: self.request_stop("Mandatory Testcases"),
        )

        self.shakedown_card = self._make_card(left_panel, 12, 12)
        self.shakedown_card.configure(height=74)
        self.shakedown_card.pack_propagate(False)
        self.shakedown_card.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._build_sequence_card(
            self.shakedown_card,
            title="Run All Shakedown Testcases",
            subtitle="Executes the shakedown validation sequence in the configured order.",
            status_attr="shakedown_status_label",
            progress_attr="shakedown_progress_label",
            run_attr="shakedown_button",
            pause_attr="shakedown_pause_button",
            stop_attr="shakedown_stop_button",
            run_command=self.run_shakedown_testcases,
            pause_command=lambda: self.request_pause_resume("Shakedown Testcases"),
            stop_command=lambda: self.request_stop("Shakedown Testcases"),
        )

        list_card = self._make_card(left_panel, 10, 10)
        self.test_cases_card = list_card
        list_card.grid_propagate(True)
        list_card.grid(row=4, column=0, sticky="ew")
        list_card.grid_rowconfigure(2, weight=0)
        list_card.grid_columnconfigure(0, weight=1)
        list_header = ctk.CTkFrame(list_card, height=34, fg_color="transparent")
        list_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        list_header.grid_propagate(False)
        title_text = ctk.CTkFrame(list_header, height=30, fg_color="transparent")
        title_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_text.pack_propagate(False)
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
        self.global_selected_stop_button = ModernButton(
            list_header,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Selected testcases"),
            height=28,
            min_width=60,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_stop_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_selected_stop_button.configure(state=tk.DISABLED)
        self.global_selected_pause_button = ModernButton(
            list_header,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Selected testcases"),
            height=28,
            min_width=68,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_pause_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.global_selected_pause_button.configure(state=tk.DISABLED)
        self.global_selected_button = ModernButton(
            list_header,
            text="Run Selected",
            variant="primary",
            command=lambda: self.run_selected_section(None),
            height=28,
            min_width=108,
            font=("Segoe UI", 10, "bold"),
        )
        self.global_selected_button.pack(side=tk.RIGHT)
        self.selected_progress_label = ctk.CTkLabel(
            list_card,
            text="Select testcases from any section, then run them together.",
            text_color=THEME["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        self.selected_progress_label.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
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
        run_command,
        pause_command,
        stop_command,
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
        current_desktop_name = self._desktop_entry_value()
        self._close_desktop_name_suggestions()
        current_log = ""
        if hasattr(self, "message_box"):
            try:
                current_log = self.message_box.get("1.0", tk.END).strip()
            except tk.TclError:
                current_log = ""
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        _activate_theme(self.theme_name)
        ctk.set_appearance_mode(self.theme_name)
        for child in self.winfo_children():
            child.destroy()
        self._configure_styles()
        self._build_layout()
        self._set_desktop_entry_value(current_desktop_name)
        self._update_desktop_input_state()
        self.refresh_tests()
        if current_log:
            self.message_box.configure(state=tk.NORMAL)
            self.message_box.insert(tk.END, f"{current_log}\n")
            self.message_box.configure(state=tk.DISABLED)

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
        self.test_cards.clear()
        self.test_card_accents.clear()
        self.test_card_states.clear()
        self.description_labels.clear()
        self.description_buttons.clear()
        self.description_expanded.clear()
        self.section_containers.clear()
        self.section_buttons.clear()
        self.section_selected_buttons.clear()
        self.section_pause_buttons.clear()
        self.section_stop_buttons.clear()
        self.section_selection_labels.clear()
        self.section_test_ids.clear()
        self.section_collapsed.clear()
        self.selection_vars.clear()
        self.selection_checkboxes.clear()

        self.config = load_config(self.root_dir)
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

        other_tests = [test_case for test_case in self.test_cases if test_case.id not in rendered_ids]
        if other_tests:
            self._add_test_case_section("IAT Testcase", "Integrated acceptance testing checks.", other_tests)
        self._reset_dashboard_statuses()
        self._schedule_test_cases_card_resize()
        self.after_idle(self._settle_progress_panel_layout)
        self.after(250, self._settle_progress_panel_layout)

    def _clear_execution_messages(self, clear_history: bool = True) -> None:
        if clear_history:
            self.log_entries.clear()
        if not hasattr(self, "message_box"):
            return
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
        if self.log_filter_button is not None:
            self.log_filter_button.configure(text="All Logs" if self.log_errors_only else "Errors")
        self._render_execution_messages()

    def _visible_log_text(self) -> str:
        if not hasattr(self, "message_box"):
            return ""
        try:
            return self.message_box.get("1.0", tk.END).strip()
        except tk.TclError:
            return ""

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
        lines.extend(["", f"Transition delay before Shakedown: {self.config.wait('complete_phase_transition_wait_sec', 5.0)} second(s)", "", "Shakedown Testcases:"])
        lines.extend(f"  {index}. {name}" for index, name in enumerate(SHAKEDOWN_TEST_CASE_ORDER, start=1))
        lines.extend(["", f"Transition delay before IAT: {self.config.wait('complete_phase_transition_wait_sec', 5.0)} second(s)", "", "IAT Testcase:"])
        lines.extend(f"  {index}. {name}" for index, name in enumerate(IAT_TEST_CASE_ORDER, start=1))
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

        section = ctk.CTkFrame(
            self.list_frame,
            height=1,
            fg_color=THEME["card"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=10,
        )
        section.pack(fill=tk.X, pady=(0, 8))
        header = ctk.CTkFrame(section, height=48, fg_color="transparent")
        header.pack(fill=tk.X, padx=10, pady=(8, 5))
        header.pack_propagate(False)
        header_text = ctk.CTkFrame(header, height=44, fg_color="transparent")
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        header_text.pack_propagate(False)
        ctk.CTkLabel(header_text, text=title, text_color=THEME["text"], font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
        ctk.CTkLabel(header_text, text=subtitle, text_color=THEME["muted"], font=("Segoe UI", 10)).pack(anchor=tk.W, pady=(1, 0))
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
        content.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.section_containers[title] = content
        self.section_buttons[title] = collapse_button
        self.section_selected_buttons[title] = run_selected_button
        self.section_pause_buttons[title] = section_pause_button
        self.section_stop_buttons[title] = section_stop_button
        self.section_selection_labels[title] = selection_label
        self.section_test_ids[title] = [test_case.id for test_case in test_cases]
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
            height=38,
            fg_color=THEME["card_soft"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=8,
        )
        card.pack(fill=tk.X, pady=(0, 5))
        card.grid_propagate(False)
        card.grid_columnconfigure(2, weight=1)
        self.test_cards[test_case.id] = card
        self.test_card_states[test_case.id] = "Idle"
        self.description_expanded[test_case.id] = False
        self._bind_card_hover(card, test_case.id)

        accent = ctk.CTkFrame(card, width=3, height=24, corner_radius=2, fg_color=THEME["border"])
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
        checkbox.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=8)
        self.selection_vars[test_case.id] = selected_var
        self.selection_checkboxes[test_case.id] = checkbox
        self._bind_card_hover(checkbox, test_case.id)

        title_row = ctk.CTkFrame(card, height=28, fg_color="transparent")
        title_row.grid(row=0, column=2, sticky="ew", pady=5)
        title_row.pack_propagate(False)
        title = ctk.CTkLabel(
            title_row,
            text=test_case.name,
            text_color=THEME["text"],
            font=("Segoe UI", 12, "bold"),
            anchor=tk.W,
            height=24,
        )
        title.pack(side=tk.LEFT)
        self._bind_card_hover(title, test_case.id)
        details_button = ModernButton(
            title_row,
            text="Details",
            variant="ghost",
            command=lambda selected=test_case: self._toggle_description(selected.id),
            height=22,
            min_width=54,
            font=("Segoe UI", 10, "bold"),
        )
        details_button.pack(side=tk.LEFT, padx=(8, 0))
        self.description_buttons[test_case.id] = details_button

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

        actions = ctk.CTkFrame(card, width=246, height=28, fg_color="transparent")
        actions.grid(row=0, column=3, sticky="e", padx=(10, 8), pady=5)
        actions.pack_propagate(False)
        status = StatusBadge(actions, text="Idle")
        status.pack(side=tk.LEFT, padx=(0, 6))
        self.status_labels[test_case.id] = status
        run_button = ModernButton(
            actions,
            text="Run",
            variant="secondary",
            command=lambda selected=test_case: self.run_test(selected),
            height=24,
            min_width=50,
            font=("Segoe UI", 10, "bold"),
        )
        run_button.pack(side=tk.LEFT)
        self.run_buttons[test_case.id] = run_button
        pause_button = ModernButton(
            actions,
            text="Pause",
            variant="secondary",
            command=lambda selected=test_case: self.request_pause_resume(selected.name),
            height=24,
            min_width=56,
            font=("Segoe UI", 10, "bold"),
        )
        pause_button.pack(side=tk.LEFT, padx=(5, 0))
        pause_button.configure(state=tk.DISABLED)
        self.pause_buttons[test_case.id] = pause_button
        stop_button = ModernButton(
            actions,
            text="Stop",
            variant="danger",
            command=lambda selected=test_case: self.request_stop(selected.name),
            height=24,
            min_width=50,
            font=("Segoe UI", 10, "bold"),
        )
        stop_button.pack(side=tk.LEFT, padx=(5, 0))
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
            self.test_cards[test_case_id].configure(height=72)
            description.grid(row=1, column=2, sticky="ew", pady=(0, 8))
            button.configure(text="Hide")
        else:
            description.grid_forget()
            self.test_cards[test_case_id].configure(height=38)
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
            container.pack(fill=tk.X, padx=10, pady=(0, 8))
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
        self.input_shell.configure(border_color=border)
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
        if self._desktop_suggestion_popup_exists() and self.desktop_suggestion_popup.winfo_viewable():
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
            text="^",
            command=lambda: self._move_desktop_suggestion(-1),
            relief=tk.FLAT,
            bd=0,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            activebackground=THEME["card_running"],
            activeforeground=THEME["primary"],
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        up_button.pack(fill=tk.X)

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
            text="v",
            command=lambda: self._move_desktop_suggestion(1),
            relief=tk.FLAT,
            bd=0,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            activebackground=THEME["card_running"],
            activeforeground=THEME["primary"],
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        down_button.pack(fill=tk.X)

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
        summary = f"Mandatory: {result.mandatory_status}   Shakedown: {result.shakedown_status}   IAT: {result.iat_status}"
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
        ModernButton(actions, text="Open Word Report", variant="primary", command=lambda: self._open_word_report(result.report_path, notice), height=34, min_width=160).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(actions, text="Open Screenshots Folder", variant="secondary", command=lambda: self._open_screenshots_folder(screenshots_root, notice), height=34, min_width=210).pack(side=tk.LEFT)
        ModernButton(actions, text="Close", variant="secondary", command=modal.destroy, height=34, min_width=92).pack(side=tk.RIGHT)
        modal.update_idletasks()
        width = 760
        height = min(max(modal.winfo_reqheight(), 315), 420)
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _open_word_report(self, report_path: Path | None, notice: ctk.CTkLabel) -> None:
        if report_path is None:
            notice.configure(text="Word report was not generated for this run.", text_color=THEME["danger"])
            return
        if not report_path.exists():
            notice.configure(text=f"Word report was not found:\n{report_path}", text_color=THEME["danger"])
            return
        try:
            os.startfile(str(report_path))
            notice.configure(text="Word report opened.", text_color=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open Word report: {exc}", text_color=THEME["danger"])

    def _open_screenshots_folder(self, screenshots_dir: Path, notice: ctk.CTkLabel) -> None:
        if not screenshots_dir.exists():
            notice.configure(text=f"Screenshots folder was not found:\n{screenshots_dir}", text_color=THEME["danger"])
            return
        try:
            subprocess.Popen(["explorer", str(screenshots_dir)])
            notice.configure(text="Screenshots folder opened.", text_color=THEME["teal"])
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
                widget.configure(fg_color="transparent")
        except tk.TclError:
            return
        for child in widget.winfo_children():
            if isinstance(child, (ModernButton, StatusBadge, ProgressRingPanel, ctk.CTkTextbox, ctk.CTkComboBox, ctk.CTkEntry, ctk.CTkProgressBar, ctk.CTkCheckBox)):
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
        self._clear_execution_messages(clear_history=False)
        for message in self.log_entries:
            if self.log_errors_only and not self._is_error_log_message(message):
                continue
            self._insert_log_message(message, settle=False)

    def _append_message(self, message: str) -> None:
        self.log_entries.append(message)
        if self.log_errors_only and not self._is_error_log_message(message):
            return
        self._insert_log_message(message, settle=True)

    def _insert_log_message(self, message: str, settle: bool) -> None:
        self.message_box.configure(state=tk.NORMAL)
        try:
            tag_name = f"log_{self.message_box.index(tk.END).replace('.', '_')}"
        except tk.TclError:
            tag_name = f"log_{int(time.time() * 1000)}"
        self.message_box.insert(tk.END, f"{message}\n", tag_name)
        color = self._log_color(message)
        self._configure_textbox_tag(tag_name, color)
        self.message_box.see(tk.END)
        self.message_box.configure(state=tk.DISABLED)
        if settle and color == THEME["console_muted"]:
            self.after(120, lambda tag=tag_name: self._settle_log_line(tag))

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
