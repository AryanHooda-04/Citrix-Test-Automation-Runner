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
    metadata: dict[str, object] = field(default_factory=dict)
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

    def _active_window_title(self) -> str:
        if pygetwindow is None:
            return ""
        try:
            active_window = pygetwindow.getActiveWindow()
        except Exception:
            return ""
        return (getattr(active_window, "title", "") or "").strip()

    @staticmethod
    def _window_title_matches(actual: str | None, expected: str | None) -> bool:
        actual_key = (actual or "").strip().casefold()
        expected_key = (expected or "").strip().casefold()
        if not actual_key or not expected_key:
            return False
        return actual_key == expected_key or expected_key in actual_key or actual_key in expected_key

    @staticmethod
    def _window_area(window) -> int:
        try:
            return max(int(window.width), 0) * max(int(window.height), 0)
        except Exception:
            return 0

    @staticmethod
    def _window_is_minimized(window) -> bool:
        try:
            return bool(window.isMinimized)
        except Exception:
            return False

    def _matching_windows_by_title(self, title: str, exact: bool = False) -> list:
        if pygetwindow is None:
            raise RuntimeError("PyGetWindow is required. Install dependencies with: pip install -r requirements.txt")
        title_key = title.strip().casefold()
        if not title_key:
            return []
        matches = []
        for window in pygetwindow.getAllWindows():
            window_title = (getattr(window, "title", "") or "").strip()
            if not window_title:
                continue
            window_key = window_title.casefold()
            if exact:
                is_match = window_key == title_key
            else:
                is_match = title_key in window_key or window_key in title_key
            if is_match:
                matches.append(window)
        return matches

    def _best_window_match(self, matches: list, entered_title: str):
        entered_key = entered_title.strip().casefold()

        def rank(window) -> tuple[int, int, int, int]:
            window_title = (getattr(window, "title", "") or "").strip().casefold()
            exact_rank = 0 if window_title == entered_key else 1
            desktop_viewer_rank = 0 if "desktop viewer" in window_title else 1
            minimized_rank = 1 if self._window_is_minimized(window) else 0
            area_rank = -self._window_area(window)
            return exact_rank, desktop_viewer_rank, minimized_rank, area_rank

        return sorted(matches, key=rank)[0]

    def _update_citrix_window_rect(self, window) -> None:
        self.citrix_window_rect = (
            int(window.left),
            int(window.top),
            int(window.width),
            int(window.height),
        )
        self.citrix_window_title = (getattr(window, "title", "") or self.citrix_window_title or "").strip()

    def _click_window_center_raw(self, window) -> None:
        center_x = int(window.left) + max(int(window.width) // 2, 1)
        center_y = int(window.top) + max(int(window.height) // 2, 1)
        self.check_stop()
        pyautogui.click(x=center_x, y=center_y, button="left")

    def _activate_and_confirm_window(self, window, entered_title: str, reason: str) -> bool:
        expected_title = (getattr(window, "title", "") or entered_title).strip()
        last_active_title = ""
        attempts = max(int(self.config.raw.get("citrix_focus_confirmation_attempts", 3)), 1)
        retry_wait = float(self.config.raw.get("citrix_focus_confirmation_wait_sec", 0.35))

        for attempt in range(1, attempts + 1):
            active_title = self._active_window_title()
            already_active = self._window_title_matches(active_title, expected_title) or self._window_title_matches(
                active_title,
                entered_title,
            )

            if already_active and not self._window_is_minimized(window):
                self.log_step(f"Citrix window already active: {expected_title}", "INFO")
            else:
                if attempt == 1:
                    self.log_step(f"Activate local window: {expected_title}", "INFO")
                else:
                    self.log_step(
                        f"Retry Citrix focus confirmation ({attempt}/{attempts}) for: {expected_title}",
                        "WARNING",
                    )
                if self._window_is_minimized(window):
                    window.restore()
                    self._sleep_interruptibly(0.2)
                window.activate()
                self._sleep_interruptibly(retry_wait)

            self._update_citrix_window_rect(window)
            self._click_window_center_raw(window)
            self._sleep_interruptibly(0.25)

            active_title = self._active_window_title()
            last_active_title = active_title
            if self._window_title_matches(active_title, expected_title) or self._window_title_matches(active_title, entered_title):
                return already_active

            self.log_step(
                "Citrix focus not confirmed after activation attempt "
                f"{attempt}/{attempts}. Active local window: {active_title or '<none>'}",
                "WARNING",
            )

        message = (
            f"Citrix desktop focus could not be confirmed before {reason}. "
            f"Expected '{expected_title}', but active local window is "
            f"'{last_active_title or '<none>'}'. Automation stopped to avoid running on the local system."
        )
        self.log_step(message, "ERROR")
        raise RuntimeError(message)

    def ensure_citrix_focus(self, reason: str = "automation input") -> None:
        self.check_stop()
        if pygetwindow is None or not self.citrix_window_title:
            return
        active_title = self._active_window_title()
        if self._window_title_matches(active_title, self.citrix_window_title):
            return

        self.log_step(
            "Citrix focus guard: active local window is "
            f"'{active_title or '<none>'}' before {reason}; refocusing '{self.citrix_window_title}'.",
            "WARNING",
        )
        matches = self._matching_windows_by_title(self.citrix_window_title, exact=True)
        if not matches and self.citrix_desktop_name:
            matches = self._matching_windows_by_title(self.citrix_desktop_name, exact=False)
        if not matches:
            message = (
                f"Citrix focus guard could not find '{self.citrix_window_title}'. "
                "Automation stopped to avoid running on the local system."
            )
            self.log_step(message, "ERROR")
            raise RuntimeError(message)

        window = self._best_window_match(matches, self.citrix_window_title)
        self._activate_and_confirm_window(window, self.citrix_window_title, reason)

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
        matches = self._matching_windows_by_title(entered_title, exact=exact)

        if not matches:
            message = f"Citrix desktop not found: {entered_title}"
            self.log_step(message, "ERROR")
            raise RuntimeError(message)

        window = self._best_window_match(matches, entered_title)
        self.log_step(f"Matching Citrix window found: {window.title}", "INFO")
        already_active = self._activate_and_confirm_window(window, entered_title, "Citrix activation")
        self.log_step(
            "Citrix window bounds: "
            f"left={self.citrix_window_rect[0]}, top={self.citrix_window_rect[1]}, "
            f"width={self.citrix_window_rect[2]}, height={self.citrix_window_rect[3]}",
            "INFO",
        )

        configured_activation_wait = self.config.wait("citrix_activation_wait_sec", 4.0)
        if wait_after_sec is None:
            delay = configured_activation_wait
        else:
            delay = wait_after_sec
        if already_active and abs(delay - configured_activation_wait) < 0.001:
            delay = self.config.wait("local_desktop_focus_wait_sec", 0.5)
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
        self.ensure_citrix_focus("click")
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
        self.ensure_citrix_focus("screen-center click")
        left, top, width, height = self._target_rect()
        x = left + (width // 2)
        y = top + (height // 2)
        self.log_step(f"Click screen center at ({x}, {y})", "INFO")
        pyautogui.click(x=x, y=y, button="left")
        delay = self.config.wait("after_click_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def click_relative(self, x_ratio: float, y_ratio: float, wait_after_sec: float | None = None) -> None:
        self.check_stop()
        self.ensure_citrix_focus("relative click")
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
        self.ensure_citrix_focus("double-click")
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
        self.ensure_citrix_focus("mouse move")
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
        self.ensure_citrix_focus(f"press {key}")
        self.log_step(f"Press {key} x{presses}", "INFO")
        pyautogui.press(key, presses=presses)
        self.wait()

    def press_repeated(self, key: str, presses: int, interval_sec: float) -> None:
        self.check_stop()
        self.ensure_citrix_focus(f"press {key}")
        self.log_step(f"Press {key} x{presses} with {interval_sec} second interval", "INFO")
        for _ in range(presses):
            self.check_stop()
            pyautogui.press(key)
            self._sleep_interruptibly(interval_sec)

    def hotkey(self, *keys: str) -> None:
        self.check_stop()
        combo = " + ".join(keys)
        self.ensure_citrix_focus(f"hotkey {combo}")
        self.log_step(f"Press hotkey {combo}", "INFO")
        pyautogui.hotkey(*keys)
        self._sleep_interruptibly(self.config.wait("after_hotkey_wait_sec", 1.0))

    def hold_key_then_press(
        self,
        hold_key: str,
        press_key: str,
        hold_before_press_sec: float = 1.0,
        wait_after_sec: float | None = None,
    ) -> None:
        self.check_stop()
        self.ensure_citrix_focus(f"hold {hold_key} then press {press_key}")
        self.log_step(
            f"Hold {hold_key} for {hold_before_press_sec} second(s), press {press_key}, then release {hold_key}",
            "INFO",
        )
        pyautogui.keyDown(hold_key)
        try:
            self._sleep_interruptibly(hold_before_press_sec)
            self.check_stop()
            pyautogui.press(press_key)
        finally:
            pyautogui.keyUp(hold_key)
        delay = self.config.wait("after_hotkey_wait_sec", 1.0) if wait_after_sec is None else wait_after_sec
        self._sleep_interruptibly(delay)

    def type_text(self, text: str, interval: float = 0.02, sensitive: bool = False) -> None:
        self.check_stop()
        self.ensure_citrix_focus("typing")
        logged_text = mask_sensitive_text(text) if sensitive else text
        self.log_step(f"Type text: {logged_text}", "INFO")
        for char in text:
            self.check_stop()
            pyautogui.write(char)
            self._sleep_interruptibly(interval)
        self._sleep_interruptibly(self.config.wait("after_type_wait_sec", 0.4))

    def copy_selected_text_from_active_window(self) -> str:
        self.check_stop()
        self.ensure_citrix_focus("copy selected text")
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
