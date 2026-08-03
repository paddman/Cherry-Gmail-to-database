from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal


class TaskWorker(QThread):
    progress = Signal(int, int, str)
    result = Signal(object)
    error = Signal(str, str)

    def __init__(self, task: Callable[[Callable[[int, int, str], None], threading.Event], Any]) -> None:
        super().__init__()
        self.task = task
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            value = self.task(self.progress.emit, self.cancel_event)
        except Exception as exc:
            self.error.emit(str(exc), traceback.format_exc())
        else:
            self.result.emit(value)
