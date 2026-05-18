from __future__ import annotations

import time
from threading import Event


class StopRequested(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Execution stopped by user.")


def wait_if_paused(
    pause_event: Event | None,
    stop_event: Event | None = None,
    poll_seconds: float = 0.2,
) -> None:
    while pause_event is not None and pause_event.is_set():
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        if stop_event is not None:
            if stop_event.wait(poll_seconds):
                raise StopRequested()
        else:
            time.sleep(poll_seconds)


def interruptible_sleep(
    seconds: float,
    stop_event: Event | None = None,
    pause_event: Event | None = None,
    poll_seconds: float = 0.2,
) -> None:
    if seconds <= 0:
        wait_if_paused(pause_event, stop_event, poll_seconds)
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        return

    end_time = time.monotonic() + seconds
    while True:
        wait_if_paused(pause_event, stop_event, poll_seconds)
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()

        remaining = end_time - time.monotonic()
        if remaining <= 0:
            break

        delay = min(poll_seconds, remaining)
        if stop_event is not None:
            if stop_event.wait(delay):
                raise StopRequested()
        else:
            time.sleep(delay)
