from __future__ import annotations

import time
from threading import Event


class CombinedStopSkipEvent:
    """Event-like wrapper used to interrupt the current testcase for Stop or Skip."""

    def __init__(self, stop_event: Event | None, skip_event: Event | None) -> None:
        self.stop_event = stop_event
        self.skip_event = skip_event

    def is_set(self) -> bool:
        return _is_event_set(self.stop_event) or _is_event_set(self.skip_event)

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        if timeout is None:
            while not self.is_set():
                time.sleep(0.05)
            return True

        end_time = time.monotonic() + max(float(timeout), 0.0)
        while True:
            if self.is_set():
                return True
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                return self.is_set()
            delay = min(0.05, remaining)
            if self.stop_event is not None:
                self.stop_event.wait(delay)
            else:
                time.sleep(delay)


def skip_requested(skip_event: Event | None) -> bool:
    return _is_event_set(skip_event)


def consume_skip_request(skip_event: Event | None) -> bool:
    if not skip_requested(skip_event):
        return False
    skip_event.clear()
    return True


def _is_event_set(event: Event | None) -> bool:
    return event is not None and event.is_set()
