"""Run a slow call (a network request, waiting for a browser sign-in) without freezing the window."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Job(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, fn: Callable[..., object], reports_progress: bool):
        super().__init__()
        self.fn = fn
        self.reports_progress = reports_progress

    def run(self) -> None:
        try:
            self.done.emit(self.fn(self.progress.emit) if self.reports_progress else self.fn())
        except Exception as e:  # shown to the user; the window must stay usable
            self.failed.emit(str(e))


def run_in_background(parent: QObject, fn: Callable[..., object],
                      on_done: Callable[[object], None], on_error: Callable[[str], None],
                      on_progress: Callable[[object], None] | None = None) -> None:
    """Calls `fn` on a worker thread, then `on_done(result)` or `on_error(message)` on the UI thread.
    `fn` must not touch widgets or the SQLite store, which belong to the UI thread.
    With `on_progress`, `fn` is called with a `report(value)` function, and each value reaches
    `on_progress` on the UI thread."""
    thread = QThread(parent)
    job = _Job(fn, on_progress is not None)
    job.moveToThread(thread)
    if on_progress is not None:
        job.progress.connect(on_progress)
    thread.started.connect(job.run)
    job.done.connect(on_done)
    job.failed.connect(on_error)
    job.done.connect(thread.quit)
    job.failed.connect(thread.quit)
    thread.finished.connect(job.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread._job = job  # keep the job alive as long as the thread
    thread.start()
