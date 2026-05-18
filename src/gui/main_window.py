from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from threading import Event
from tkinter import messagebox, ttk

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


def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> int:
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.lstrip("#")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


class ModernButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        command=None,
        variant: str = "secondary",
        height: int = 38,
        min_width: int = 92,
        radius: int = 9,
        font: tuple[str, int, str] = ("Segoe UI", 10, "bold"),
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            height=height,
            width=min_width,
            highlightthickness=0,
            bd=0,
            bg=kwargs.pop("bg", master.cget("bg")),
            cursor="hand2",
            **kwargs,
        )
        self.text = text
        self.command = command
        self.variant = variant
        self.button_height = height
        self.min_width = min_width
        self.radius = radius
        self.font = font
        self._state = tk.NORMAL
        self._hover = False
        self._pressed = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        options = {}
        if cnf:
            options.update(cnf)
        options.update(kwargs)
        if "state" in options:
            self._state = options.pop("state")
            super().configure(cursor="arrow" if self._state == tk.DISABLED else "hand2")
        if "text" in options:
            self.text = options.pop("text")
        if "command" in options:
            self.command = options.pop("command")
        result = super().configure(**options) if options else None
        self._draw()
        return result

    config = configure

    def _palette(self) -> dict[str, str | None]:
        return BUTTON_VARIANTS[self.variant]

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), self.min_width)
        height = max(self.winfo_height(), self.button_height)
        palette = self._palette()
        if self._state == tk.DISABLED:
            fill = palette["disabled"]
            text = palette["disabled_text"]
            outline = palette["outline"] or fill
        elif self._pressed:
            fill = palette["pressed"]
            text = palette["text"]
            outline = palette["outline"] or fill
        elif self._hover:
            fill = palette["hover"]
            text = palette["text"]
            outline = palette["outline"] or fill
        else:
            fill = palette["fill"]
            text = palette["text"]
            outline = palette["outline"] or fill

        if self._state != tk.DISABLED and (self._hover or self.variant == "primary"):
            shadow = THEME["card_running_glow"] if self.variant == "primary" and self._hover else THEME["border"]
            _round_rect(self, 3, 4, width - 1, height - 1, self.radius, fill=shadow, outline="")
        _round_rect(self, 1, 1, width - 3, height - 4, self.radius, fill=fill, outline=outline, width=1)
        y_offset = 1 if self._pressed and self._state != tk.DISABLED else 0
        self.create_text(
            width // 2,
            height // 2 - 1 + y_offset,
            text=self.text,
            fill=text,
            font=self.font,
        )

    def _on_enter(self, _event: tk.Event) -> None:
        if self._state != tk.DISABLED:
            self._hover = True
            self._draw()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event: tk.Event) -> None:
        if self._state != tk.DISABLED:
            self._pressed = True
            self._draw()

    def _on_release(self, _event: tk.Event) -> None:
        if self._state == tk.DISABLED:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self.command is not None:
            self.command()


class StatusBadge(tk.Canvas):
    DISPLAY_TEXT = {
        "Pass": "Passed",
        "Fail": "Failed",
    }

    def __init__(self, master, text: str = "Idle", width: int = 104, height: int = 28, **kwargs) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=kwargs.pop("bg", master.cget("bg")),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.text = text
        self.badge_width = width
        self.badge_height = height
        self._draw()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        options = {}
        if cnf:
            options.update(cnf)
        options.update(kwargs)
        if "text" in options:
            self.text = options.pop("text")
        options.pop("foreground", None)
        result = super().configure(**options) if options else None
        self._draw()
        return result

    config = configure

    def _draw(self) -> None:
        self.delete("all")
        fill, text = STATUS_BADGES.get(self.text, STATUS_BADGES["Idle"])
        display_text = self.DISPLAY_TEXT.get(self.text, self.text)
        _round_rect(self, 1, 2, self.badge_width - 2, self.badge_height - 3, 13, fill=fill, outline="")
        self.create_oval(15, 11, 21, 17, fill=text, outline="")
        self.create_text(
            self.badge_width // 2 + 8,
            self.badge_height // 2,
            text=display_text,
            fill=text,
            font=("Segoe UI", 9, "bold"),
        )


class AutomationApp(tk.Tk):
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
        self.test_cards: dict[str, tk.Frame] = {}
        self.test_card_accents: dict[str, tk.Frame] = {}
        self.test_card_states: dict[str, str] = {}
        self.description_labels: dict[str, tk.Label] = {}
        self.description_buttons: dict[str, ModernButton] = {}
        self.description_expanded: dict[str, bool] = {}
        self.section_containers: dict[str, tk.Frame] = {}
        self.section_buttons: dict[str, ModernButton] = {}
        self.section_selected_buttons: dict[str, ModernButton] = {}
        self.section_pause_buttons: dict[str, ModernButton] = {}
        self.section_stop_buttons: dict[str, ModernButton] = {}
        self.section_selection_labels: dict[str, tk.Label] = {}
        self.section_test_ids: dict[str, list[str]] = {}
        self.section_collapsed: dict[str, bool] = {}
        self.selection_vars: dict[str, tk.BooleanVar] = {}
        self.selection_checkboxes: dict[str, tk.Checkbutton] = {}
        self.events: queue.Queue = queue.Queue()
        self.desktop_name_var = tk.StringVar(value="")
        self.refresh_button: ModernButton | None = None
        self.theme_button: ModernButton | None = None
        self.complete_button: ModernButton | None = None
        self.complete_pause_button: ModernButton | None = None
        self.complete_stop_button: ModernButton | None = None
        self.complete_status_label: StatusBadge | None = None
        self.complete_progress_label: tk.Label | None = None
        self.complete_runtime_label: tk.Label | None = None
        self.complete_progress_bar: tk.Canvas | None = None
        self.complete_card: tk.Frame | None = None
        self.dry_run_button: ModernButton | None = None
        self.latest_report_button: ModernButton | None = None
        self.master_button: ModernButton | None = None
        self.master_pause_button: ModernButton | None = None
        self.master_stop_button: ModernButton | None = None
        self.master_status_label: StatusBadge | None = None
        self.master_progress_label: tk.Label | None = None
        self.master_card: tk.Frame | None = None
        self.shakedown_button: ModernButton | None = None
        self.shakedown_pause_button: ModernButton | None = None
        self.shakedown_stop_button: ModernButton | None = None
        self.shakedown_status_label: StatusBadge | None = None
        self.shakedown_progress_label: tk.Label | None = None
        self.shakedown_card: tk.Frame | None = None
        self.input_card: tk.Frame | None = None
        self.main_canvas: tk.Canvas | None = None
        self.main_window: int | None = None
        self.list_canvas: tk.Canvas | None = None
        self.list_window: int | None = None
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
        self.latest_report_path: Path | None = None

        self.title("Citrix Test Automation Runner")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(bg=THEME["bg"])

        self._configure_styles()
        self._build_layout()
        self.refresh_tests()
        self.after(150, self._process_events)

    def _read_app_version(self) -> str:
        version_path = self.root_dir / "version.txt"
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            version = ""
        return version or "dev"

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Vertical.TScrollbar",
            troughcolor=THEME["card"],
            background=THEME["scrollbar"],
            bordercolor=THEME["card"],
        )
        style.configure(
            "Desktop.TCombobox",
            fieldbackground=THEME["input"],
            background=THEME["input"],
            foreground=THEME["text"],
            selectforeground=THEME["text"],
            selectbackground=THEME["card_running"],
        )
        style.map(
            "Desktop.TCombobox",
            fieldbackground=[
                ("disabled", THEME["input_disabled"]),
                ("readonly", THEME["input"]),
                ("focus", THEME["input"]),
            ],
            foreground=[
                ("disabled", THEME["muted"]),
                ("readonly", THEME["text"]),
                ("focus", THEME["text"]),
            ],
        )

    def _build_layout(self) -> None:
        outer = tk.Frame(self, bg=THEME["bg"])
        outer.pack(fill=tk.BOTH, expand=True)

        self.header_canvas = tk.Canvas(outer, height=96, bg=THEME["primary"], highlightthickness=0, bd=0)
        self.header_canvas.pack(fill=tk.X)
        self.header_canvas.bind("<Configure>", self._draw_header)
        self.theme_button = ModernButton(
            self.header_canvas,
            text="Dark" if self.theme_name == "light" else "Light",
            variant="ghost",
            command=self.toggle_theme,
            height=34,
            min_width=88,
            bg=THEME["primary"],
        )
        self.header_theme_window = self.header_canvas.create_window(0, 0, window=self.theme_button, anchor=tk.NE)
        self.refresh_button = ModernButton(
            self.header_canvas,
            text="Refresh",
            variant="ghost",
            command=self.refresh_tests,
            height=34,
            min_width=102,
            bg=THEME["primary"],
        )
        self.header_refresh_window = self.header_canvas.create_window(0, 0, window=self.refresh_button, anchor=tk.NE)
        tk.Frame(outer, height=1, bg=THEME["divider"]).pack(fill=tk.X)

        content = tk.Frame(outer, bg=THEME["bg"])
        content.pack(fill=tk.BOTH, expand=True, padx=22, pady=(16, 22))

        self.input_card = self._make_card(content, padx=18, pady=15)
        self.input_card.pack(fill=tk.X)
        input_header = tk.Frame(self.input_card, bg=THEME["card"])
        input_header.pack(fill=tk.X)
        tk.Label(
            input_header,
            text="Citrix Desktop Name",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        self.desktop_state_label = tk.Label(
            input_header,
            text="Required before execution",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.desktop_state_label.pack(side=tk.RIGHT)

        self.input_shell = tk.Frame(self.input_card, bg=THEME["input"], highlightthickness=1, highlightbackground=THEME["border"])
        self.input_shell.pack(fill=tk.X, pady=(9, 0))
        self.desktop_name_entry = ttk.Combobox(
            self.input_shell,
            textvariable=self.desktop_name_var,
            values=self._desktop_dropdown_values(),
            state="normal",
            style="Desktop.TCombobox",
            font=("Segoe UI", 11),
        )
        self.desktop_name_entry.pack(fill=tk.X, padx=11, pady=8)
        self.desktop_name_entry.bind("<FocusIn>", lambda _event: self._update_desktop_input_state(focused=True))
        self.desktop_name_entry.bind("<FocusOut>", lambda _event: self._update_desktop_input_state(focused=False))
        self.desktop_name_entry.bind("<KeyRelease>", self._on_desktop_name_keyrelease)
        self._update_desktop_input_state()
        tk.Label(
            self.input_card,
            text="Example: SILO01-TEST. The app automatically targets the matching Citrix Desktop Viewer window.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(7, 0))

        body = tk.Frame(content, bg=THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

        left_shell = tk.Frame(body, bg=THEME["bg"])
        left_shell.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.main_canvas = tk.Canvas(left_shell, bg=THEME["bg"], highlightthickness=0, borderwidth=0)
        main_scrollbar = ttk.Scrollbar(left_shell, orient=tk.VERTICAL, command=self.main_canvas.yview)
        left_panel = tk.Frame(self.main_canvas, bg=THEME["bg"])
        self.main_window = self.main_canvas.create_window((0, 0), window=left_panel, anchor=tk.NW)
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)
        left_panel.bind("<Configure>", self._update_main_scroll_region)
        self.main_canvas.bind("<Configure>", self._resize_main_scroll_window)
        self.main_canvas.bind("<Enter>", self._bind_main_mousewheel)
        self.main_canvas.bind("<Leave>", self._unbind_main_mousewheel)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        main_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.complete_card = self._make_card(left_panel, padx=20, pady=18)
        self.complete_card.pack(fill=tk.X)
        complete_text = tk.Frame(self.complete_card, bg=THEME["card"])
        complete_text.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            complete_text,
            text="Perform Complete Testing",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            complete_text,
            text="Runs Mandatory, Shakedown, and IAT suites end-to-end.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(3, 0))
        self.complete_progress_label = tk.Label(
            complete_text,
            text="Ready",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.complete_progress_label.pack(anchor=tk.W, pady=(8, 0))
        self.complete_runtime_label = tk.Label(
            complete_text,
            text="Elapsed 00:00 | Phase Idle | Current None | Remaining 0",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        )
        self.complete_runtime_label.pack(anchor=tk.W, pady=(4, 0))
        self.complete_progress_bar = tk.Canvas(
            complete_text,
            height=8,
            bg=THEME["card"],
            highlightthickness=0,
            bd=0,
        )
        self.complete_progress_bar.pack(fill=tk.X, pady=(10, 0))
        self.complete_progress_bar.bind("<Configure>", lambda _event: self._draw_complete_progress_bar())

        complete_actions = tk.Frame(self.complete_card, bg=THEME["card"])
        complete_actions.pack(side=tk.TOP, anchor=tk.E, pady=(14, 0))
        self.complete_status_label = StatusBadge(complete_actions, text="Idle", bg=THEME["card"])
        self.complete_status_label.pack(side=tk.LEFT, padx=(0, 10))
        self.complete_button = ModernButton(
            complete_actions,
            text="Run All",
            variant="primary",
            command=self.run_complete_testing,
            height=44,
            min_width=132,
            bg=THEME["card"],
        )
        self.complete_button.pack(side=tk.LEFT)
        self.dry_run_button = ModernButton(
            complete_actions,
            text="Dry Run",
            variant="secondary",
            command=self.show_complete_testing_checklist,
            height=38,
            min_width=118,
            bg=THEME["card"],
        )
        self.dry_run_button.pack(side=tk.LEFT, padx=(10, 0))
        self.latest_report_button = ModernButton(
            complete_actions,
            text="Open Latest Report",
            variant="secondary",
            command=self.open_latest_report,
            height=38,
            min_width=190,
            bg=THEME["card"],
        )
        self.latest_report_button.pack(side=tk.LEFT, padx=(10, 0))
        self.complete_pause_button = ModernButton(
            complete_actions,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Complete Testing"),
            height=38,
            min_width=94,
            bg=THEME["card"],
        )
        self.complete_pause_button.pack(side=tk.LEFT, padx=(10, 0))
        self.complete_pause_button.configure(state=tk.DISABLED)
        self.complete_stop_button = ModernButton(
            complete_actions,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Complete Testing"),
            height=38,
            min_width=90,
            bg=THEME["card"],
        )
        self.complete_stop_button.pack(side=tk.LEFT, padx=(10, 0))
        self.complete_stop_button.configure(state=tk.DISABLED)

        self.master_card = self._make_card(left_panel, padx=20, pady=17)
        self.master_card.pack(fill=tk.X, pady=(16, 0))
        master_text = tk.Frame(self.master_card, bg=THEME["card"])
        master_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            master_text,
            text="Run All Mandatory Testcases",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            master_text,
            text="Executes the mandatory evidence sequence in the configured order.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(3, 0))
        self.master_progress_label = tk.Label(
            master_text,
            text="Ready",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.master_progress_label.pack(anchor=tk.W, pady=(8, 0))

        master_actions = tk.Frame(self.master_card, bg=THEME["card"])
        master_actions.pack(side=tk.RIGHT)
        self.master_status_label = StatusBadge(master_actions, text="Idle", bg=THEME["card"])
        self.master_status_label.pack(side=tk.LEFT, padx=(0, 10))
        self.master_button = ModernButton(
            master_actions,
            text="Run All",
            variant="primary",
            command=self.run_mandatory_testcases,
            height=44,
            min_width=124,
            bg=THEME["card"],
        )
        self.master_button.pack(side=tk.LEFT)
        self.master_pause_button = ModernButton(
            master_actions,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Mandatory Testcases"),
            height=38,
            min_width=86,
            bg=THEME["card"],
        )
        self.master_pause_button.pack(side=tk.LEFT, padx=(10, 0))
        self.master_pause_button.configure(state=tk.DISABLED)
        self.master_stop_button = ModernButton(
            master_actions,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Mandatory Testcases"),
            height=38,
            min_width=82,
            bg=THEME["card"],
        )
        self.master_stop_button.pack(side=tk.LEFT, padx=(10, 0))
        self.master_stop_button.configure(state=tk.DISABLED)

        self.shakedown_card = self._make_card(left_panel, padx=20, pady=17)
        self.shakedown_card.pack(fill=tk.X, pady=(16, 0))
        shakedown_text = tk.Frame(self.shakedown_card, bg=THEME["card"])
        shakedown_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            shakedown_text,
            text="Run All Shakedown Testcases",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            shakedown_text,
            text="Executes the shakedown validation sequence in the configured order.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(3, 0))
        self.shakedown_progress_label = tk.Label(
            shakedown_text,
            text="Ready",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.shakedown_progress_label.pack(anchor=tk.W, pady=(8, 0))

        shakedown_actions = tk.Frame(self.shakedown_card, bg=THEME["card"])
        shakedown_actions.pack(side=tk.RIGHT)
        self.shakedown_status_label = StatusBadge(shakedown_actions, text="Idle", bg=THEME["card"])
        self.shakedown_status_label.pack(side=tk.LEFT, padx=(0, 10))
        self.shakedown_button = ModernButton(
            shakedown_actions,
            text="Run All",
            variant="primary",
            command=self.run_shakedown_testcases,
            height=44,
            min_width=124,
            bg=THEME["card"],
        )
        self.shakedown_button.pack(side=tk.LEFT)
        self.shakedown_pause_button = ModernButton(
            shakedown_actions,
            text="Pause",
            variant="secondary",
            command=lambda: self.request_pause_resume("Shakedown Testcases"),
            height=38,
            min_width=86,
            bg=THEME["card"],
        )
        self.shakedown_pause_button.pack(side=tk.LEFT, padx=(10, 0))
        self.shakedown_pause_button.configure(state=tk.DISABLED)
        self.shakedown_stop_button = ModernButton(
            shakedown_actions,
            text="Stop",
            variant="danger",
            command=lambda: self.request_stop("Shakedown Testcases"),
            height=38,
            min_width=82,
            bg=THEME["card"],
        )
        self.shakedown_stop_button.pack(side=tk.LEFT, padx=(10, 0))
        self.shakedown_stop_button.configure(state=tk.DISABLED)

        list_card = self._make_card(left_panel, padx=16, pady=16)
        list_card.pack(fill=tk.BOTH, expand=True, pady=(16, 0))
        list_header = tk.Frame(list_card, bg=THEME["card"])
        list_header.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            list_header,
            text="Test Cases",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            list_header,
            text="Run, monitor, or stop individual automation checks.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.list_canvas = tk.Canvas(list_card, bg=THEME["card"], highlightthickness=0, borderwidth=0)
        list_scrollbar = ttk.Scrollbar(list_card, orient=tk.VERTICAL, command=self.list_canvas.yview)
        self.list_frame = tk.Frame(self.list_canvas, bg=THEME["card"])
        self.list_window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor=tk.NW)
        self.list_canvas.configure(yscrollcommand=list_scrollbar.set)

        self.list_frame.bind("<Configure>", self._update_scroll_region)
        self.list_canvas.bind("<Configure>", self._resize_scroll_window)
        self.list_canvas.bind("<Enter>", self._bind_list_mousewheel)
        self.list_canvas.bind("<Leave>", self._unbind_list_mousewheel)

        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        log_panel = self._make_card(body, padx=16, pady=16)
        log_panel.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(18, 0))
        tk.Label(
            log_panel,
            text="Execution Messages",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            log_panel,
            text="Live automation output",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(2, 10))
        console_frame = tk.Frame(log_panel, bg=THEME["console"], highlightthickness=1, highlightbackground=THEME["border"])
        console_frame.pack(fill=tk.BOTH, expand=True)
        self.message_box = tk.Text(
            console_frame,
            width=42,
            height=22,
            state=tk.DISABLED,
            bg=THEME["console"],
            fg=THEME["console_text"],
            insertbackground=THEME["console_text"],
            relief=tk.FLAT,
            borderwidth=0,
            font=("Cascadia Mono", 9),
            wrap=tk.WORD,
            padx=14,
            pady=14,
            spacing1=2,
            spacing3=2,
        )
        self.message_box.pack(fill=tk.BOTH, expand=True)

    def _make_card(self, parent, padx: int, pady: int) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=THEME["card"],
            padx=padx,
            pady=pady,
            highlightthickness=1,
            highlightbackground=THEME["border"],
            relief=tk.FLAT,
        )
        return card

    def _draw_header(self, event: tk.Event) -> None:
        canvas = self.header_canvas
        canvas.delete("header")
        width = event.width
        height = event.height
        top = _hex_to_rgb(THEME["header_top"])
        bottom = _hex_to_rgb(THEME["header_bottom"])
        for y in range(height):
            ratio = y / max(height - 1, 1)
            r = int(top[0] + (bottom[0] - top[0]) * ratio)
            g = int(top[1] + (bottom[1] - top[1]) * ratio)
            b = int(top[2] + (bottom[2] - top[2]) * ratio)
            canvas.create_line(0, y, width, y, fill=f"#{r:02x}{g:02x}{b:02x}", tags="header")
        canvas.create_line(0, height - 2, width, height - 2, fill="#ffffff", tags="header")
        canvas.create_line(0, height - 1, width, height - 1, fill=THEME["divider"], tags="header")
        canvas.create_oval(24, 24, 66, 66, fill=THEME["header_icon"], outline="", tags="header")
        canvas.create_text(45, 45, text="C", fill=THEME["header_icon_text"], font=("Segoe UI", 17, "bold"), tags="header")
        canvas.create_text(
            84,
            33,
            text="Citrix Test Automation Runner",
            fill="#ffffff",
            font=("Segoe UI", 18, "bold"),
            anchor=tk.W,
            tags="header",
        )
        canvas.create_text(
            86,
            58,
            text="Run evidence checks, monitor progress, and keep desktop outputs organized.",
            fill=THEME["header_subtitle"],
            font=("Segoe UI", 9),
            anchor=tk.W,
            tags="header",
        )
        canvas.create_text(
            width - 24,
            23,
            text=f"Version: {self.app_version}",
            fill=THEME["header_subtitle"],
            font=("Segoe UI", 8, "bold"),
            anchor=tk.E,
            tags="header",
        )
        canvas.coords(self.header_refresh_window, width - 24, 51)
        canvas.coords(self.header_theme_window, width - 138, 51)
        canvas.tag_lower("header")

    def _update_scroll_region(self, _event: tk.Event) -> None:
        if self.list_canvas is not None:
            self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))
        if self.main_canvas is not None:
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _resize_scroll_window(self, event: tk.Event) -> None:
        if self.list_canvas is not None and self.list_window is not None:
            self.list_canvas.itemconfigure(self.list_window, width=event.width)

    def _update_main_scroll_region(self, _event: tk.Event) -> None:
        if self.main_canvas is not None:
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _resize_main_scroll_window(self, event: tk.Event) -> None:
        if self.main_canvas is not None and self.main_window is not None:
            self.main_canvas.itemconfigure(self.main_window, width=event.width)

    def _bind_main_mousewheel(self, _event: tk.Event) -> None:
        self.bind_all("<MouseWheel>", self._on_main_mousewheel)

    def _unbind_main_mousewheel(self, _event: tk.Event) -> None:
        self.unbind_all("<MouseWheel>")

    def _on_main_mousewheel(self, event: tk.Event) -> None:
        if self.main_canvas is not None:
            self.main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_list_mousewheel(self, _event: tk.Event) -> None:
        self.bind_all("<MouseWheel>", self._on_list_mousewheel)

    def _unbind_list_mousewheel(self, _event: tk.Event) -> None:
        self.unbind_all("<MouseWheel>")

    def _on_list_mousewheel(self, event: tk.Event) -> None:
        if self.list_canvas is not None:
            self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def toggle_theme(self) -> None:
        if self.active_stop_event is not None:
            messagebox.showinfo("Theme Locked", "Theme can be changed after the current run finishes.")
            return
        current_log = ""
        if hasattr(self, "message_box"):
            try:
                current_log = self.message_box.get("1.0", tk.END).strip()
            except tk.TclError:
                current_log = ""
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        _activate_theme(self.theme_name)
        self.configure(bg=THEME["bg"])
        for child in self.winfo_children():
            child.destroy()
        self._configure_styles()
        self._build_layout()
        self.refresh_tests()
        if current_log:
            self.message_box.configure(state=tk.NORMAL)
            self.message_box.insert(tk.END, f"{current_log}\n")
            self.message_box.configure(state=tk.DISABLED)

    def refresh_tests(self) -> None:
        if self.active_stop_event is not None:
            messagebox.showinfo("Refresh Locked", "Refresh is available after the current run finishes.")
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        if self.list_canvas is not None:
            self.list_canvas.yview_moveto(0)
        if self.main_canvas is not None:
            self.main_canvas.yview_moveto(0)
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
            tk.Label(
                self.list_frame,
                text="No test cases found. Add Python scripts to the test_cases folder.",
                bg=THEME["card"],
                fg=THEME["muted"],
                font=("Segoe UI", 10),
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
            self._add_test_case_section(
                "IAT Testcase",
                "Integrated acceptance testing checks.",
                other_tests,
            )
        self._reset_dashboard_statuses()
        if self.main_canvas is not None:
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

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

        self._set_complete_status("Idle")
        self._set_complete_progress("Ready")
        self._set_complete_runtime_summary()
        self._set_master_status("Idle")
        self._set_master_progress("Ready")
        self._set_shakedown_status("Idle")
        self._set_shakedown_progress("Ready")

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
        self._set_buttons_enabled(True)
        self._update_desktop_input_state()

    def show_complete_testing_checklist(self) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name to generate the checklist paths.",
            )
            self.desktop_name_entry.focus_set()
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
        lines.extend(
            [
                "",
                f"Transition delay before Shakedown: {self.config.wait('complete_phase_transition_wait_sec', 5.0)} second(s)",
                "",
                "Shakedown Testcases:",
            ]
        )
        lines.extend(f"  {index}. {name}" for index, name in enumerate(SHAKEDOWN_TEST_CASE_ORDER, start=1))
        lines.extend(
            [
                "",
                f"Transition delay before IAT: {self.config.wait('complete_phase_transition_wait_sec', 5.0)} second(s)",
                "",
                "IAT Testcase:",
            ]
        )
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

        modal = tk.Toplevel(self)
        modal.title("Complete Testing Checklist")
        modal.configure(bg=THEME["bg"])
        modal.transient(self)
        modal.grab_set()
        modal.resizable(True, True)

        card = self._make_card(modal, padx=18, pady=18)
        card.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            card,
            text="Dry Run / Checklist Mode",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text="Review the complete run order, expected evidence names, and output folders.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(4, 12))

        text_frame = tk.Frame(card, bg=THEME["console"], highlightthickness=1, highlightbackground="#1e293b")
        text_frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(
            text_frame,
            width=98,
            height=28,
            bg=THEME["console"],
            fg=THEME["console_text"],
            insertbackground=THEME["console_text"],
            relief=tk.FLAT,
            borderwidth=0,
            font=("Cascadia Mono", 9),
            wrap=tk.WORD,
            padx=12,
            pady=12,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.insert(tk.END, "\n".join(lines))
        text.configure(state=tk.DISABLED)

        actions = tk.Frame(card, bg=THEME["card"])
        actions.pack(fill=tk.X, pady=(14, 0))
        ModernButton(
            actions,
            text="Close",
            variant="secondary",
            command=modal.destroy,
            height=40,
            min_width=94,
            bg=THEME["card"],
        ).pack(side=tk.RIGHT)

        modal.geometry("920x680")

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

    def _add_test_case_section(self, title: str, subtitle: str, test_cases: list[TestCase]) -> set[str]:
        if not test_cases:
            return set()

        section = tk.Frame(
            self.list_frame,
            bg=THEME["card"],
            padx=12,
            pady=12,
            highlightthickness=1,
            highlightbackground=THEME["border"],
            relief=tk.FLAT,
        )
        section.pack(fill=tk.X, pady=(8, 12))
        header = tk.Frame(section, bg=THEME["card"])
        header.pack(fill=tk.X)
        header_text = tk.Frame(header, bg=THEME["card"])
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            header_text,
            text=title,
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            header_text,
            text=subtitle,
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(2, 0))
        selection_label = tk.Label(
            header_text,
            text="",
            bg=THEME["card"],
            fg=THEME["teal"],
            font=("Segoe UI", 8, "bold"),
        )
        selection_label.pack(anchor=tk.W, pady=(6, 0))
        run_selected_button = ModernButton(
            header,
            text="Run (Selected)",
            variant="secondary",
            command=lambda selected=title: self.run_selected_section(selected),
            height=34,
            min_width=148,
            font=("Segoe UI", 8, "bold"),
            bg=THEME["card"],
        )
        run_selected_button.pack(side=tk.RIGHT, padx=(10, 0))
        section_pause_button = ModernButton(
            header,
            text="Pause",
            variant="secondary",
            command=lambda selected=title: self.request_pause_resume(f"{selected} selected run"),
            height=34,
            min_width=86,
            font=("Segoe UI", 8, "bold"),
            bg=THEME["card"],
        )
        section_pause_button.pack(side=tk.RIGHT, padx=(10, 0))
        section_pause_button.configure(state=tk.DISABLED)
        section_stop_button = ModernButton(
            header,
            text="Stop",
            variant="danger",
            command=lambda selected=title: self.request_stop(f"{selected} selected run"),
            height=34,
            min_width=76,
            font=("Segoe UI", 8, "bold"),
            bg=THEME["card"],
        )
        section_stop_button.pack(side=tk.RIGHT, padx=(10, 0))
        section_stop_button.configure(state=tk.DISABLED)
        collapse_button = ModernButton(
            header,
            text="Collapse",
            variant="ghost",
            command=lambda selected=title: self._toggle_test_section(selected),
            height=34,
            min_width=98,
            font=("Segoe UI", 8, "bold"),
            bg=THEME["card"],
        )
        collapse_button.pack(side=tk.RIGHT)
        content = tk.Frame(section, bg=THEME["card"])
        content.pack(fill=tk.X, pady=(12, 0))
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
        card = tk.Frame(
            parent,
            bg=THEME["card_soft"],
            padx=14,
            pady=14,
            highlightthickness=1,
            highlightbackground=THEME["border"],
            relief=tk.FLAT,
        )
        card.pack(fill=tk.X, pady=(0, 12))
        self.test_cards[test_case.id] = card
        self.test_card_states[test_case.id] = "Idle"
        self.description_expanded[test_case.id] = False
        self._bind_card_hover(card, test_case.id)

        accent = tk.Frame(card, bg=THEME["border"], width=3)
        accent.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 14))
        self.test_card_accents[test_case.id] = accent

        selected_var = tk.BooleanVar(value=False)
        checkbox = tk.Checkbutton(
            card,
            variable=selected_var,
            command=self._on_test_selection_changed,
            bg=THEME["card_soft"],
            activebackground=THEME["card_soft"],
            fg=THEME["text"],
            selectcolor=THEME["input"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        checkbox.pack(side=tk.LEFT, padx=(0, 14))
        self.selection_vars[test_case.id] = selected_var
        self.selection_checkboxes[test_case.id] = checkbox
        self._bind_card_hover(checkbox, test_case.id)

        text_block = tk.Frame(card, bg=THEME["card_soft"])
        text_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_row = tk.Frame(text_block, bg=THEME["card_soft"])
        title_row.pack(fill=tk.X)
        title = tk.Label(
            title_row,
            text=test_case.name,
            bg=THEME["card_soft"],
            fg=THEME["text"],
            font=("Segoe UI", 10, "bold"),
            anchor=tk.W,
        )
        title.pack(side=tk.LEFT, anchor=tk.W)
        self._bind_card_hover(title, test_case.id)
        details_button = ModernButton(
            title_row,
            text="Details",
            variant="ghost",
            command=lambda selected=test_case: self._toggle_description(selected.id),
            height=28,
            min_width=76,
            font=("Segoe UI", 8, "bold"),
            bg=THEME["card_soft"],
        )
        details_button.pack(side=tk.LEFT, padx=(10, 0))
        self.description_buttons[test_case.id] = details_button
        description = tk.Label(
            text_block,
            text=test_case.description or "Automation testcase",
            bg=THEME["card_soft"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
            anchor=tk.W,
            wraplength=520,
            justify=tk.LEFT,
        )
        self.description_labels[test_case.id] = description

        actions = tk.Frame(card, bg=THEME["card_soft"])
        actions.pack(side=tk.RIGHT, padx=(16, 0))
        status = StatusBadge(actions, text="Idle", bg=THEME["card_soft"])
        status.pack(side=tk.LEFT, padx=(0, 10))
        self.status_labels[test_case.id] = status

        run_button = ModernButton(
            actions,
            text="Run",
            variant="secondary",
            command=lambda selected=test_case: self.run_test(selected),
            height=34,
            min_width=72,
            font=("Segoe UI", 9, "bold"),
            bg=THEME["card_soft"],
        )
        run_button.pack(side=tk.LEFT)
        self.run_buttons[test_case.id] = run_button

        pause_button = ModernButton(
            actions,
            text="Pause",
            variant="secondary",
            command=lambda selected=test_case: self.request_pause_resume(selected.name),
            height=34,
            min_width=78,
            font=("Segoe UI", 9, "bold"),
            bg=THEME["card_soft"],
        )
        pause_button.pack(side=tk.LEFT, padx=(8, 0))
        pause_button.configure(state=tk.DISABLED)
        self.pause_buttons[test_case.id] = pause_button

        stop_button = ModernButton(
            actions,
            text="Stop",
            variant="danger",
            command=lambda selected=test_case: self.request_stop(selected.name),
            height=34,
            min_width=72,
            font=("Segoe UI", 9, "bold"),
            bg=THEME["card_soft"],
        )
        stop_button.pack(side=tk.LEFT, padx=(8, 0))
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
            description.pack(anchor=tk.W, pady=(7, 0))
            button.configure(text="Hide")
        else:
            description.pack_forget()
            button.configure(text="Details")
        if self.list_canvas is not None:
            self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

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
            container.pack(fill=tk.X, pady=(12, 0))
            button.configure(text="Collapse")
        if self.list_canvas is not None:
            self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_test_selection_changed(self) -> None:
        self._update_selection_cues()

    def _update_selection_cues(self) -> None:
        for title, label in self.section_selection_labels.items():
            selected_count = len(self._selected_test_case_ids(title))
            if selected_count:
                label.configure(text=f"Custom selection mode active: {selected_count} selected")
            else:
                label.configure(text="")

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

    def _bind_card_hover(self, widget: tk.Widget, test_case_id: str) -> None:
        widget.bind("<Enter>", lambda _event, selected=test_case_id: self._set_test_card_hover(selected, True), add="+")
        widget.bind("<Leave>", lambda _event, selected=test_case_id: self._set_test_card_hover(selected, False), add="+")

    def _set_test_card_hover(self, test_case_id: str, hovered: bool) -> None:
        if self.test_card_states.get(test_case_id) == "Running":
            return
        card = self.test_cards.get(test_case_id)
        if card is None:
            return
        bg = THEME["card_hover"] if hovered else THEME["card_soft"]
        self._set_frame_tree_bg(card, bg)

    def run_test(self, test_case: TestCase) -> None:
        desktop_name = self._normalized_desktop_name()
        if not desktop_name:
            messagebox.showerror(
                "Citrix Desktop Name Required",
                "Please enter Citrix Desktop Name.",
            )
            self.desktop_name_entry.focus_set()
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
            self.desktop_name_entry.focus_set()
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
            self.desktop_name_entry.focus_set()
            return

        self._set_master_status("Running")
        self.master_completed_count = 0
        self.master_total_count = len(mandatory_order_for_desktop(desktop_name))
        self._set_master_progress(f"0 of {self.master_total_count} completed")
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
            self.desktop_name_entry.focus_set()
            return

        self._set_shakedown_status("Running")
        self.shakedown_completed_count = 0
        self.shakedown_total_count = len(SHAKEDOWN_TEST_CASE_ORDER)
        self._set_shakedown_progress(f"0 of {self.shakedown_total_count} completed")
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

        thread = threading.Thread(
            target=self._run_shakedown_worker,
            args=(list(self.test_cases), desktop_name, self.active_stop_event, self.active_pause_event),
            daemon=True,
        )
        thread.start()

    def run_selected_section(self, section_title: str) -> None:
        selected_ids = self._selected_test_case_ids(section_title)
        if not selected_ids:
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
            self.desktop_name_entry.focus_set()
            return

        self.selected_completed_count = 0
        self.selected_total_count = len(selected_tests)
        self.selected_section_title = section_title
        self._set_buttons_enabled(False)
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()
        self._set_complete_stop_enabled(False)
        self._set_master_stop_enabled(section_title == "Mandatory Testcases")
        self._set_shakedown_stop_enabled(section_title == "Shakedown Testcases")
        self._set_section_stop_enabled(section_title, True)
        self._set_section_pause_enabled(section_title, True)
        self.active_stop_event = Event()
        self.active_pause_event = Event()
        self.active_paused = False
        self.active_test_id = None
        self.active_selected_section = section_title
        self.active_mode = f"selected:{section_title}"
        for test_case in self.test_cases:
            self._set_status(test_case.id, "Idle")

        if section_title == "Mandatory Testcases":
            self._set_master_status("Running")
            self._set_master_progress(f"0 of {self.selected_total_count} selected completed")
        elif section_title == "Shakedown Testcases":
            self._set_shakedown_status("Running")
            self._set_shakedown_progress(f"0 of {self.selected_total_count} selected completed")

        self._append_message(f"Starting selected run: {section_title}")
        self._append_message(f"Selected testcases: {', '.join(test_case.name for test_case in selected_tests)}")
        self._append_message(f"Citrix Desktop Name: {desktop_name}")

        thread = threading.Thread(
            target=self._run_selected_worker,
            args=(section_title, selected_tests, desktop_name, self.active_stop_event, self.active_pause_event),
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
                delay = self._selected_between_tests_delay(section_title)
                if delay > 0:
                    self.events.put(("message", f"Selected run delay before next test: {delay} second(s)"))
                    try:
                        interruptible_sleep(delay, stop_event, pause_event)
                    except StopRequested:
                        stopped = True
                        break

        status = "Stopped" if stopped or stop_event.is_set() else ("Pass" if failed_count == 0 else "Fail")
        self.events.put(("selected_complete", section_title, status, failed_count))

    def _cleanup_after_selected_test(
        self,
        section_title: str,
        test_name: str,
        desktop_name: str,
        stop_event: Event,
        pause_event: Event,
    ) -> None:
        if section_title == "Mandatory Testcases":
            MasterRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_test(test_name)
        elif section_title == "Shakedown Testcases":
            ShakedownRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_test(test_name)
        elif section_title == "IAT Testcase":
            CompleteTestingRunner(
                config=self.config,
                citrix_desktop_name=desktop_name,
                status_callback=lambda message: self.events.put(("message", message)),
                stop_event=stop_event,
                pause_event=pause_event,
            )._cleanup_after_iat(test_name)

    def _selected_between_tests_delay(self, section_title: str) -> float:
        if section_title == "Mandatory Testcases":
            return self.config.wait("mandatory_between_tests_wait_sec", 30.0)
        if section_title == "Shakedown Testcases":
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
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.master_completed_count = min(
                            self.master_completed_count + 1,
                            self.master_total_count,
                        )
                        self._set_master_progress(
                            f"{self.master_completed_count} of {self.master_total_count} completed"
                        )
                        self._set_stop_button_enabled(test_case_id, False)
                        self._set_pause_button_enabled(test_case_id, False)
                elif self.active_mode == "shakedown":
                    if status == "Running":
                        self.active_test_id = test_case_id
                        self._disable_all_stop_buttons()
                        self._enable_stop_button(test_case_id)
                        self._disable_row_pause_buttons()
                        self._set_pause_button_enabled(test_case_id, True)
                    elif status in {"Pass", "Fail", "Skipped", "Stopped"}:
                        self.shakedown_completed_count = min(
                            self.shakedown_completed_count + 1,
                            self.shakedown_total_count,
                        )
                        self._set_shakedown_progress(
                            f"{self.shakedown_completed_count} of {self.shakedown_total_count} completed"
                        )
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
                _, section_title, status, failed_count = event
                self._handle_selected_result(section_title, status, failed_count)

        self.after(150, self._process_events)

    def _handle_result(self, test_case: TestCase, result: ExecutionResult) -> None:
        self._set_status(test_case.id, result.status)
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
        elif result.status == "Pass":
            self._set_master_progress(f"{self.master_total_count} of {self.master_total_count} completed")
            desktop_name = self._normalized_desktop_name()
            self._record_successful_desktop_name(desktop_name)
            self._show_completion_notification(
                "Mandatory Evidence Completed",
                "Mandatory evidence execution completed successfully.",
                desktop_name,
                evidence_category=MANDATORY_EVIDENCE_FOLDER,
            )
        else:
            self._set_master_progress(f"{self.master_completed_count} of {self.master_total_count} completed")
            messagebox.showerror(
                "Mandatory Testcases Finished With Failures",
                f"{result.failed_count} mandatory test case(s) failed.\n\nMaster log:\n{result.log_path}",
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
        self.complete_current_phase = result.status
        self.complete_current_test = "Finished"
        self._set_complete_runtime_summary()
        desktop_name = self._normalized_desktop_name()
        if result.status == "Pass":
            self._record_successful_desktop_name(desktop_name)
        self._show_complete_testing_notification(desktop_name, result)

    def _handle_selected_result(self, section_title: str, status: str, failed_count: int) -> None:
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

        self._set_buttons_enabled(True)
        self._clear_active_execution_controls()
        self._append_message(f"{section_title} selected run: {status}")

        desktop_name = self._normalized_desktop_name()
        if status == "Stopped":
            messagebox.showinfo("Selected Run Stopped", f"{section_title} selected run was stopped.")
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

    def _set_master_status(self, status: str) -> None:
        if self.master_status_label is not None:
            self.master_status_label.configure(text=status)
        if self.master_card is not None:
            self._set_frame_tree_bg(self.master_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_master_progress(self, text: str) -> None:
        if self.master_progress_label is not None:
            self.master_progress_label.configure(text=text)

    def _set_shakedown_status(self, status: str) -> None:
        if self.shakedown_status_label is not None:
            self.shakedown_status_label.configure(text=status)
        if self.shakedown_card is not None:
            self._set_frame_tree_bg(self.shakedown_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_shakedown_progress(self, text: str) -> None:
        if self.shakedown_progress_label is not None:
            self.shakedown_progress_label.configure(text=text)

    def _set_selected_progress(self) -> None:
        text = f"{self.selected_completed_count} of {self.selected_total_count} selected completed"
        if self.selected_section_title == "Mandatory Testcases":
            self._set_master_progress(text)
        elif self.selected_section_title == "Shakedown Testcases":
            self._set_shakedown_progress(text)

    def _set_complete_status(self, status: str) -> None:
        if self.complete_status_label is not None:
            self.complete_status_label.configure(text=status)
        if self.complete_card is not None:
            self._set_frame_tree_bg(self.complete_card, THEME["card_running"] if status in {"Running", "Paused"} else THEME["card"])

    def _set_complete_progress(self, text: str) -> None:
        if self.complete_progress_label is not None:
            self.complete_progress_label.configure(text=text)
        self._draw_complete_progress_bar()

    def _tick_complete_runtime(self) -> None:
        if self.active_mode != "complete" or self.complete_started_monotonic is None:
            return
        self._set_complete_runtime_summary()
        self.after(1000, self._tick_complete_runtime)

    def _set_complete_runtime_summary(self) -> None:
        if self.complete_runtime_label is None:
            return
        elapsed = 0
        if self.complete_started_monotonic is not None:
            elapsed = int(time.monotonic() - self.complete_started_monotonic)
        remaining = max(self.complete_total_count - self.complete_completed_count, 0)
        self.complete_runtime_label.configure(
            text=(
                f"Elapsed {_format_elapsed(elapsed)} | "
                f"Phase {self.complete_current_phase} | "
                f"Current {self.complete_current_test} | "
                f"Remaining {remaining}"
            )
        )

    def _draw_complete_progress_bar(self) -> None:
        if self.complete_progress_bar is None:
            return
        self.complete_progress_bar.delete("all")
        width = max(self.complete_progress_bar.winfo_width(), 1)
        height = max(self.complete_progress_bar.winfo_height(), 8)
        _round_rect(self.complete_progress_bar, 0, 1, width, height - 1, 4, fill=THEME["border"], outline="")
        total = max(self.complete_total_count, 1)
        ratio = min(max(self.complete_completed_count / total, 0), 1)
        fill_width = int(width * ratio)
        if fill_width > 0:
            _round_rect(
                self.complete_progress_bar,
                0,
                1,
                fill_width,
                height - 1,
                4,
                fill=THEME["primary"],
                outline="",
            )

    def _test_name_for_id(self, test_case_id: str) -> str:
        for test_case in self.test_cases:
            if test_case.id == test_case_id:
                return test_case.name
        return test_case_id

    def _desktop_short_name(self, desktop_name: str) -> str:
        cleaned = " ".join(desktop_name.strip().split())
        if cleaned.casefold().endswith(DESKTOP_VIEWER_SUFFIX.casefold()):
            cleaned = cleaned[: -len(DESKTOP_VIEWER_SUFFIX)].strip()
        for known_name in KNOWN_DESKTOP_NAMES:
            if cleaned.casefold() == known_name.casefold():
                return known_name
        return cleaned

    def _normalized_desktop_name(self, desktop_name: str | None = None) -> str:
        raw_value = self.desktop_name_var.get() if desktop_name is None else desktop_name
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
        try:
            self.desktop_name_entry.configure(values=items)
        except tk.TclError:
            pass

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

        typed = self.desktop_name_var.get().strip()
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

    def _show_desktop_name_suggestions(self) -> None:
        try:
            cursor_position = self.desktop_name_entry.index(tk.INSERT)
            self.tk.call("ttk::combobox::Post", self.desktop_name_entry)
            self.desktop_name_entry.icursor(cursor_position)
        except tk.TclError:
            try:
                self.desktop_name_entry.event_generate("<Alt-Down>")
            except tk.TclError:
                pass

    def _record_successful_desktop_name(self, desktop_name: str) -> None:
        if desktop_name:
            self._refresh_desktop_history_values(self.desktop_history.add(self._desktop_short_name(desktop_name)))

    def _update_desktop_input_state(self, focused: bool = False, disabled: bool = False) -> None:
        if not hasattr(self, "input_shell"):
            return
        has_value = bool(self.desktop_name_var.get().strip())
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
        self.input_shell.configure(highlightbackground=border)
        if hasattr(self, "desktop_state_label"):
            self.desktop_state_label.configure(text=text, fg=color)

    def _show_completion_notification(
        self,
        title: str,
        message: str,
        desktop_name: str,
        evidence_category: str | None = None,
        open_button_text: str = "Open",
        open_button_width: int = 94,
    ) -> None:
        screenshots_dir = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)
        if evidence_category:
            screenshots_dir = screenshots_dir / evidence_category
        modal = tk.Toplevel(self)
        modal.title(title)
        modal.configure(bg=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, padx=24, pady=22)
        card.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(
            card,
            text=title,
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text=message,
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, pady=(8, 0))
        tk.Label(
            card,
            text=f"Citrix Desktop Name: {desktop_name}",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(14, 0))
        tk.Label(
            card,
            text=f"Screenshots folder:\n{screenshots_dir}",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
            justify=tk.LEFT,
            wraplength=520,
        ).pack(anchor=tk.W, pady=(8, 0))
        notice = tk.Label(
            card,
            text="Evidence is available for review.",
            bg=THEME["card"],
            fg=THEME["teal"],
            font=("Segoe UI", 9, "bold"),
        )
        notice.pack(anchor=tk.W, pady=(12, 0))

        actions = tk.Frame(card, bg=THEME["card"])
        actions.pack(fill=tk.X, pady=(18, 0))
        ModernButton(
            actions,
            text=open_button_text,
            variant="primary",
            command=lambda: self._open_screenshots_folder(screenshots_dir, notice),
            height=40,
            min_width=open_button_width,
            bg=THEME["card"],
        ).pack(side=tk.LEFT)
        ModernButton(
            actions,
            text="Close",
            variant="secondary",
            command=modal.destroy,
            height=40,
            min_width=94,
            bg=THEME["card"],
        ).pack(side=tk.RIGHT)

        modal.update_idletasks()
        width = 620
        height = modal.winfo_reqheight()
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _show_complete_testing_notification(self, desktop_name: str, result: CompleteExecutionResult) -> None:
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), desktop_name)

        modal = tk.Toplevel(self)
        modal.title("Complete Testing Finished")
        modal.configure(bg=THEME["bg"])
        self._configure_independent_popup(modal)
        modal.resizable(False, False)

        card = self._make_card(modal, padx=24, pady=22)
        card.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(
            card,
            text="Complete Testing Finished",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text="Complete Testing execution finished.",
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, pady=(8, 0))
        tk.Label(
            card,
            text=f"Citrix Desktop Name: {desktop_name}",
            bg=THEME["card"],
            fg=THEME["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(14, 0))
        summary = (
            f"Mandatory: {result.mandatory_status}   "
            f"Shakedown: {result.shakedown_status}   "
            f"IAT: {result.iat_status}"
        )
        tk.Label(
            card,
            text=summary,
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, pady=(8, 0))
        tk.Label(
            card,
            text=(
                f"Word report:\n{result.report_path or 'Report was not generated.'}\n\n"
                f"Master log:\n{result.log_path}"
            ),
            bg=THEME["card"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
            justify=tk.LEFT,
            wraplength=740,
        ).pack(anchor=tk.W, pady=(8, 0))
        notice = tk.Label(
            card,
            text="Word report and screenshots are available for review.",
            bg=THEME["card"],
            fg=THEME["teal"],
            font=("Segoe UI", 9, "bold"),
        )
        notice.pack(anchor=tk.W, pady=(12, 0))

        actions = tk.Frame(card, bg=THEME["card"])
        actions.pack(fill=tk.X, pady=(18, 0))
        ModernButton(
            actions,
            text="Open Word Report",
            variant="primary",
            command=lambda: self._open_word_report(result.report_path, notice),
            height=40,
            min_width=190,
            bg=THEME["card"],
        ).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(
            actions,
            text="Open Screenshots Folder",
            variant="secondary",
            command=lambda: self._open_screenshots_folder(screenshots_root, notice),
            height=40,
            min_width=250,
            bg=THEME["card"],
        ).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(
            actions,
            text="Close",
            variant="secondary",
            command=modal.destroy,
            height=40,
            min_width=94,
            bg=THEME["card"],
        ).pack(side=tk.RIGHT)

        modal.update_idletasks()
        width = 840
        height = modal.winfo_reqheight()
        x = self.winfo_rootx() + max((self.winfo_width() - width) // 2, 20)
        y = self.winfo_rooty() + max((self.winfo_height() - height) // 2, 20)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_independent_popup(self, popup: tk.Toplevel) -> None:
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        popup.attributes("-toolwindow", False)
        popup.lift()
        popup.focus_force()

    def _open_word_report(self, report_path: Path | None, notice: tk.Label) -> None:
        if report_path is None:
            notice.configure(text="Word report was not generated for this run.", fg=THEME["danger"])
            return
        if not report_path.exists():
            notice.configure(
                text=f"Word report was not found:\n{report_path}",
                fg=THEME["danger"],
            )
            return
        try:
            os.startfile(str(report_path))
            notice.configure(text="Word report opened.", fg=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open Word report: {exc}", fg=THEME["danger"])

    def _open_screenshots_folder(self, screenshots_dir: Path, notice: tk.Label) -> None:
        if not screenshots_dir.exists():
            notice.configure(
                text=f"Screenshots folder was not found:\n{screenshots_dir}",
                fg=THEME["danger"],
            )
            return
        try:
            subprocess.Popen(["explorer", str(screenshots_dir)])
            notice.configure(text="Screenshots folder opened.", fg=THEME["teal"])
        except OSError as exc:
            notice.configure(text=f"Could not open folder: {exc}", fg=THEME["danger"])

    def _set_test_card_state(self, test_case_id: str, status: str) -> None:
        card = self.test_cards.get(test_case_id)
        if card is None:
            return
        self.test_card_states[test_case_id] = status
        bg = THEME["card_running"] if status in {"Running", "Paused"} else THEME["card_soft"]
        self._set_frame_tree_bg(card, bg)
        _, status_color = STATUS_BADGES.get(status, STATUS_BADGES["Idle"])
        card.configure(
            highlightbackground=THEME["card_running_glow"] if status in {"Running", "Paused"} else status_color if status in {"Pass", "Fail", "Skipped", "Stopped"} else THEME["border"],
            highlightthickness=2 if status in {"Running", "Paused"} else 1,
        )
        accent = self.test_card_accents.get(test_case_id)
        if accent is not None:
            accent.configure(bg=THEME["primary"] if status in {"Running", "Paused"} else status_color if status in {"Pass", "Fail", "Skipped", "Stopped"} else THEME["border"])

    def _set_frame_tree_bg(self, widget: tk.Widget, bg: str) -> None:
        try:
            widget.configure(bg=bg)
        except tk.TclError:
            return
        for child in widget.winfo_children():
            if isinstance(child, ModernButton):
                child.configure(bg=bg)
            elif isinstance(child, StatusBadge):
                child.configure(bg=bg)
            elif isinstance(child, tk.Canvas):
                child.configure(bg=bg)
            elif isinstance(child, tk.Checkbutton):
                child.configure(bg=bg, activebackground=bg, fg=THEME["text"], selectcolor=THEME["input"])
            elif isinstance(child, (tk.Frame, tk.Label)):
                self._set_frame_tree_bg(child, bg)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.run_buttons.values():
            button.configure(state=state)
        for button in self.section_selected_buttons.values():
            button.configure(state=state)
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

    def _refresh_pause_button_text(self) -> None:
        text = "Resume" if self.active_paused else "Pause"
        for button in self.pause_buttons.values():
            button.configure(text=text)
        for button in self.section_pause_buttons.values():
            button.configure(text=text)
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
        self._disable_all_stop_buttons()
        self._disable_all_pause_buttons()

    def _append_message(self, message: str) -> None:
        self.message_box.configure(state=tk.NORMAL)
        tag_name = f"log_{self.message_box.index(tk.END).replace('.', '_')}"
        self.message_box.insert(tk.END, f"{message}\n", tag_name)
        lower_message = message.casefold()
        if "error" in lower_message or "failed" in lower_message or "fail:" in lower_message:
            color = THEME["console_error"]
        elif "warning" in lower_message or "stopped" in lower_message:
            color = THEME["console_warning"]
        else:
            color = THEME["console_muted"]
        self.message_box.tag_configure(tag_name, foreground=color, spacing1=2, spacing3=2)
        self.message_box.see(tk.END)
        self.message_box.configure(state=tk.DISABLED)
        if color == THEME["console_muted"]:
            self.after(120, lambda tag=tag_name: self._settle_log_line(tag))

    def _settle_log_line(self, tag_name: str) -> None:
        try:
            self.message_box.configure(state=tk.NORMAL)
            self.message_box.tag_configure(tag_name, foreground=THEME["console_text"])
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
