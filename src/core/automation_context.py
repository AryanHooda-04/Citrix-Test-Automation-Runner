from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Callable

from PIL import Image, ImageGrab

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import pygetwindow
except ImportError:
    pygetwindow = None

from core.config import AppConfig
from core.clipboard import get_clipboard_text
from core.execution_log import desktop_scoped_path
from core.screenshot import ScreenshotManager
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused


@dataclass
class AutomationContext:
    config: AppConfig
    log_step: Callable[[str, str], None]
    citrix_desktop_name: str | None = None
    evidence_category: str | None = None
    stop_event: Event | None = None
    pause_event: Event | None = None
    evidence_paths: list = field(default_factory=list)
    citrix_window_rect: tuple[int, int, int, int] | None = None
    citrix_window_title: str | None = None

    def __post_init__(self) -> None:
        if pyautogui is None:
            raise RuntimeError("PyAutoGUI is required. Install dependencies with: pip install -r requirements.txt")
        settings = self.config.pyautogui_settings
        pyautogui.PAUSE = float(settings.get("pause_sec", 0.1))
        pyautogui.FAILSAFE = bool(settings.get("failsafe", True))

    def step(self, message: str) -> None:
        self.check_stop()
        self.log_step(message, "INFO")

    def check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()
        wait_if_paused(self.pause_event, self.stop_event)
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()

    def wait(self, seconds: float | None = None) -> None:
        self.check_stop()
        delay = self.config.wait("default_action_wait_sec") if seconds is None else seconds
        self.log_step(f"Wait {delay} second(s)", "INFO")
        self._sleep_interruptibly(delay)

    def wait_for_citrix(self) -> None:
        self.check_stop()
        delay = self.config.wait("citrix_screen_settle_sec", 2.0)
        self.log_step(f"Wait for Citrix screen to settle: {delay} second(s)", "INFO")
        self._sleep_interruptibly(delay)

    def _sleep_interruptibly(self, seconds: float) -> None:
        interruptible_sleep(seconds, self.stop_event, self.pause_event)
        self.check_stop()

    def activate_window_by_title(
        self,
        title: str,
        exact: bool = False,
        wait_after_sec: float | None = None,
    ) -> None:
        self.check_stop()
        if pygetwindow is None:
            raise RuntimeError("PyGetWindow is required. Install dependencies with: pip install -r requirements.txt")

        entered_title = title.strip()
        self.log_step(f"Find local window title: {entered_title}", "INFO")
        windows = pygetwindow.getAllWindows()
        if exact:
            matches = [
                window
                for window in windows
                if window.title.strip().casefold() == entered_title.casefold()
            ]
        else:
            matches = [
                window
                for window in windows
                if entered_title.casefold() in window.title.casefold()
            ]

        if not matches:
            message = f"Citrix desktop not found: {entered_title}"
            self.log_step(message, "ERROR")
            raise RuntimeError(message)

        window = matches[0]
        self.log_step(f"Matching Citrix window found: {window.title}", "INFO")
        self.log_step(f"Activate local window: {window.title}", "INFO")
        if window.isMinimized:
            window.restore()
        window.activate()
        self._sleep_interruptibly(0.5)
        self.citrix_window_rect = (
            int(window.left),
            int(window.top),
            int(window.width),
            int(window.height),
        )
        self.citrix_window_title = window.title
        self.log_step(
            "Citrix window bounds: "
            f"left={self.citrix_window_rect[0]}, top={self.citrix_window_rect[1]}, "
            f"width={self.citrix_window_rect[2]}, height={self.citrix_window_rect[3]}",
            "INFO",
        )

        center_x = int(window.left) + max(int(window.width) // 2, 1)
        center_y = int(window.top) + max(int(window.height) // 2, 1)
        self.check_stop()
        pyautogui.click(x=center_x, y=center_y, button="left")

        delay = self.config.wait("citrix_activation_wait_sec", 4.0) if wait_after_sec is None else wait_after_sec
        self.log_step(f"Wait after window activation: {delay} second(s)", "INFO")
        self._sleep_interruptibly(delay)

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        wait_after_sec: float | None = None,
    ) -> None:
        self.check_stop()
        target = "current mouse position" if x is None or y is None else f"({x}, {y})"
        self.log_step(f"Click {button} at {target}", "INFO")
        if x is None or y is None:
            pyautogui.click(button=button)
        else:
            x, y = self._to_virtual_point(x, y)
            pyautogui.moveTo(x=x, y=y, duration=0.25)
            self.check_stop()
            pyautogui.click(x=x, y=y, button=button)
        delay = self.config.wait("after_click_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def click_screen_center(self, wait_after_sec: float | None = None) -> None:
        self.check_stop()
        left, top, width, height = self._target_rect()
        x = left + (width // 2)
        y = top + (height // 2)
        self.log_step(f"Click screen center at ({x}, {y})", "INFO")
        pyautogui.click(x=x, y=y, button="left")
        delay = self.config.wait("after_click_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def click_relative(self, x_ratio: float, y_ratio: float, wait_after_sec: float | None = None) -> None:
        self.check_stop()
        left, top, width, height = self._target_rect()
        x = left + int(width * x_ratio)
        y = top + int(height * y_ratio)
        self.log_step(f"Click relative screen position ({x_ratio:.3f}, {y_ratio:.3f}) -> ({x}, {y})", "INFO")
        pyautogui.moveTo(x=x, y=y, duration=0.25)
        self.check_stop()
        pyautogui.click(x=x, y=y, button="left")
        delay = self.config.wait("after_click_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def double_click(self, x: int, y: int, wait_after_sec: float | None = None) -> None:
        self.check_stop()
        self.log_step(f"Double-click at ({x}, {y})", "INFO")
        x, y = self._to_virtual_point(x, y)
        pyautogui.moveTo(x=x, y=y, duration=0.25)
        self.check_stop()
        pyautogui.doubleClick(x=x, y=y, button="left", interval=0.15)
        delay = self.config.wait("after_click_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def maximize_active_window(self) -> None:
        self.log_step("Maximize active window", "INFO")
        self.hotkey("alt", "space")
        self.wait(0.5)
        self.press("x")
        self.wait(1.0)

    def maximize_active_window_with_win_up(self) -> None:
        self.log_step("Maximize active window with Windows + Up", "INFO")
        self.hotkey("winleft", "up")
        self.wait(0.5)
        self.hotkey("winleft", "up")
        self.wait(1.0)

    def move_to(self, x: int, y: int, duration: float = 0.0) -> None:
        self.check_stop()
        self.log_step(f"Move mouse to ({x}, {y})", "INFO")
        x, y = self._to_virtual_point(x, y)
        pyautogui.moveTo(x=x, y=y, duration=duration)
        self.wait()

    def screenshot_region(self, region: tuple[int, int, int, int]):
        self.check_stop()
        x, y, width, height = region
        virtual_x, virtual_y, virtual_width, virtual_height = self._to_virtual_rect(
            int(x),
            int(y),
            int(width),
            int(height),
        )
        self.log_step(
            f"Capture visual detection region ({x}, {y}, {width}, {height}) "
            f"as virtual ({virtual_x}, {virtual_y}, {virtual_width}, {virtual_height})",
            "INFO",
        )
        image = self._grab_virtual_region(virtual_x, virtual_y, virtual_width, virtual_height)
        logical_size = (max(int(width), 1), max(int(height), 1))
        if image.size != logical_size:
            resampling = getattr(Image, "Resampling", Image).BILINEAR
            image = image.resize(logical_size, resampling)
        return image

    def capture_region(self) -> tuple[int, int, int, int] | None:
        if self.citrix_window_rect is None:
            return None
        left, top, width, height = self.citrix_window_rect
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height

    def _target_rect(self) -> tuple[int, int, int, int]:
        if self.citrix_window_rect is not None:
            return self.citrix_window_rect
        width, height = pyautogui.size()
        return 0, 0, width, height

    def _to_virtual_point(self, x: int, y: int) -> tuple[int, int]:
        if self.citrix_window_rect is None:
            return x, y
        left, top, width, height = self.citrix_window_rect
        scale_x, scale_y = self._coordinate_scale(width, height)
        return left + int(round(x * scale_x)), top + int(round(y * scale_y))

    def _to_virtual_rect(self, x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        if self.citrix_window_rect is None:
            return x, y, max(width, 1), max(height, 1)
        left, top, viewport_width, viewport_height = self.citrix_window_rect
        scale_x, scale_y = self._coordinate_scale(viewport_width, viewport_height)
        virtual_x = left + int(round(x * scale_x))
        virtual_y = top + int(round(y * scale_y))
        virtual_width = max(int(round(width * scale_x)), 1)
        virtual_height = max(int(round(height * scale_y)), 1)
        return virtual_x, virtual_y, virtual_width, virtual_height

    def _coordinate_scale(self, width: int, height: int) -> tuple[float, float]:
        settings = self.config.raw.get("citrix_viewport", {})
        if not bool(settings.get("scale_coordinates", True)):
            return 1.0, 1.0
        reference_width = max(int(settings.get("coordinate_reference_width", 1920)), 1)
        reference_height = max(int(settings.get("coordinate_reference_height", 1080)), 1)
        return width / reference_width, height / reference_height

    def _grab_virtual_region(self, x: int, y: int, width: int, height: int) -> Image.Image:
        bbox = (x, y, x + max(width, 1), y + max(height, 1))
        try:
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            return ImageGrab.grab(bbox=bbox)

    def press(self, key: str, presses: int = 1) -> None:
        self.check_stop()
        self.log_step(f"Press {key} x{presses}", "INFO")
        pyautogui.press(key, presses=presses)
        self.wait()

    def press_repeated(self, key: str, presses: int, interval_sec: float) -> None:
        self.check_stop()
        self.log_step(f"Press {key} x{presses} with {interval_sec} second interval", "INFO")
        for _ in range(presses):
            self.check_stop()
            pyautogui.press(key)
            self._sleep_interruptibly(interval_sec)

    def hotkey(self, *keys: str) -> None:
        self.check_stop()
        combo = " + ".join(keys)
        self.log_step(f"Press hotkey {combo}", "INFO")
        pyautogui.hotkey(*keys)
        self._sleep_interruptibly(self.config.wait("after_hotkey_wait_sec", 1.0))

    def type_text(self, text: str, interval: float = 0.02, sensitive: bool = False) -> None:
        self.check_stop()
        logged_text = mask_sensitive_text(text) if sensitive else text
        self.log_step(f"Type text: {logged_text}", "INFO")
        for char in text:
            self.check_stop()
            pyautogui.write(char)
            self._sleep_interruptibly(interval)
        self._sleep_interruptibly(self.config.wait("after_type_wait_sec", 0.4))

    def copy_selected_text_from_active_window(self) -> str:
        self.check_stop()
        self.log_step("Select and copy text from active window for verification", "INFO")
        pyautogui.hotkey("ctrl", "a")
        self._sleep_interruptibly(self.config.wait("cmd_copy_wait_sec", 0.5))
        pyautogui.hotkey("ctrl", "c")
        self._sleep_interruptibly(self.config.wait("cmd_copy_wait_sec", 0.5))
        text = get_clipboard_text()
        self.log_step(f"Copied {len(text)} character(s) for verification", "INFO")
        return text

    def verify_active_window_text_contains(self, expected_text: str, failure_message: str) -> str:
        text = self.copy_selected_text_from_active_window()
        if expected_text.lower() not in text.lower():
            raise RuntimeError(failure_message)
        return text

    def capture_evidence(self, evidence_name: str, status: str = "Pass", copy_to_clipboard: bool = True):
        self.check_stop()
        screenshots = ScreenshotManager(
            screenshots_dir=evidence_category_path(
                self.config.path("screenshots_dir"),
                self.citrix_desktop_name,
                self.evidence_category,
            ),
            settle_seconds=self.config.wait("screenshot_settle_sec", 0.8),
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            desktop_name=self.citrix_desktop_name,
            capture_region=self.capture_region(),
            suppress_local_notifications=bool(
                self.config.screenshot_settings.get("suppress_local_notifications", True)
            ),
            notification_guard_wait_seconds=self.config.wait("notification_guard_wait_sec", 0.8),
        )
        path = screenshots.capture(evidence_name, status)
        self.check_stop()
        self.evidence_paths.append(path)
        self.log_step(f"Evidence screenshot saved: {path}", "INFO")
        if copy_to_clipboard:
            screenshots.copy_to_clipboard(path)
            self.log_step("Evidence screenshot copied to clipboard", "INFO")
        return path


def mask_sensitive_text(text: str) -> str:
    if len(text) <= 2:
        return "*" * len(text)
    return text[:1] + "*" * (len(text) - 2) + text[-1:]


def evidence_category_path(base_path, desktop_name, evidence_category):
    path = desktop_scoped_path(base_path, desktop_name)
    return path / evidence_category if evidence_category else path
