# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The text interface's main loop.

Threads, and the rules that keep them from waiting on each other:

* **This thread** reads keys (waiting at most 50 ms), reads the live status
  about ten times a second, and draws. It never calls a bridge method that
  saves, starts, stops or pauses: those take the bridge's and the runner's
  locks, and a start can hold them for seconds while the loop is built.
* **One worker thread** makes every such call, in order, and hands back the
  answer. While it works the footer says so.
* **The loop thread**, the ``keyboard`` hook thread and the mouse listener are
  the runner's, exactly as under the window.
* **A console close** runs on a thread Windows creates and only lets go of
  every key (:func:`mercy_ai.input_control.emergency_release`), which takes no
  lock but each key controller's own.

The interface never builds a control loop itself: it drives the same
:class:`~mercy_ai.ui.bridge.Bridge` and :class:`~mercy_ai.ui.runner.LoopRunner`
the window does, so the two cannot disagree about what a setting means.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import partial
from typing import Any

from ..input_control import emergency_release
from . import controller
from .capture import KeyCapture, keys_held_now
from .screens import render, view_name
from .state import Data, Effect, UIState
from .style import ASCII, UNICODE, Style

log = logging.getLogger(__name__)

__all__ = ["App", "RingHandler"]

#: Seconds between status reads on the Status tab, and elsewhere (where only
#: the header's frame rate needs it).
STATUS_EVERY = 0.1
STATUS_EVERY_ELSEWHERE = 0.5
#: Keys typed while Mercy AI is pressing keys are dropped: the game keys it
#: presses land in this console too, and must not act as commands.
LIVE_GRACE = 0.3
#: How long quitting waits for a save or a start that is still running.
QUIT_WAIT = 5.0


class RingHandler(logging.Handler):
    """Keeps the last messages in memory. Nothing is written to disk."""

    def __init__(self, size: int = 200) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self.lines.append(self.format(record))


class _Worker:
    """Runs bridge calls one at a time, off the drawing thread."""

    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self.results: queue.SimpleQueue[tuple[Effect, dict[str, Any]]] = queue.SimpleQueue()
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="mercy-ai-cli-worker", daemon=True)
        self._thread.start()

    def submit(self, effect: Effect, job: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._jobs.put((effect, job))

    def _run(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            effect, job = item
            try:
                response = job()
            except Exception as exc:  # the bridge never raises, but a capture start may
                log.exception("a background job failed")
                response = {"ok": False, "problems": [str(exc) or exc.__class__.__name__]}
            self.results.put((effect, response if isinstance(response, dict) else {"ok": True}))
            with self._lock:
                self._pending -= 1
                if self._pending == 0:
                    self._idle.set()

    def wait_idle(self, timeout: float) -> bool:
        return self._idle.wait(timeout)

    def stop(self) -> None:
        self._jobs.put(None)


class App:
    def __init__(
        self,
        console: Any,
        bridge: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        capture_factory: Callable[..., KeyCapture] = KeyCapture,
        worker: Any = None,
    ) -> None:
        self.console = console
        self.bridge = bridge
        self.clock = clock
        self.capture_factory = capture_factory
        self.worker = worker or _Worker()
        self.style = Style(
            color=bool(getattr(console, "color", True)),
            glyphs=UNICODE if getattr(console, "unicode", True) else ASCII,
        )
        state = bridge.get_state()
        self.data = Data(
            config=state["config"],
            status=state["status"],
            env=state["env"],
            version=state["version"],
        )
        self.state = UIState()
        self.capture: KeyCapture | None = None
        self._resume: tuple[bool, bool] = (False, False)
        self._last_status = 0.0
        self._quiet_until = 0.0
        self._failures = 0

    # -- the loop ------------------------------------------------------------ #

    def run(self) -> int:
        try:
            self._sync_size()
            self._dispatch(controller.start(self.state, self.data))
            while not self.state.quit:
                self._step()
        finally:
            self.shutdown()
        return 0

    def _step(self) -> None:
        state = self.state
        state.now = self.clock()
        self._sync_size()
        self._poll_status()
        self._drain_results()
        self._poll_capture()
        self._dispatch(controller.tick(state, self.data))
        self._draw()
        key = self.console.read_key(0.05)
        if key is None:
            return
        state.now = self.clock()
        if key == "resize":
            self._sync_size()
            self.console.invalidate()
        if key not in ("ctrl+c", "interrupt", "resize") and state.now < self._quiet_until:
            return
        self._reduce(lambda: controller.handle_key(state, self.data, key, self.style))

    def _reduce(self, action: Callable[[], list[Effect]]) -> None:
        """Run one controller step; a failure leaves the state as it was."""
        saved = copy.deepcopy(self.state)
        try:
            effects = action()
        except Exception:
            log.exception("a key could not be handled")
            self.state = saved
            self._failed("Something went wrong handling that key. Press Esc to go back.")
            return
        self._dispatch(effects)

    def _failed(self, text: str) -> None:
        self._failures += 1
        if self._failures >= 3:
            raise RuntimeError(text)
        controller._refusal(self.state, [text])

    def _draw(self) -> None:
        try:
            frame = render(self.state, self.data, self.style)
        except Exception:
            log.exception("the screen could not be drawn")
            self._failures += 1
            if self._failures >= 3:
                raise
            self.state.message = None
            if self.state.modal is not None and self.state.modal.kind == "capture":
                self._stop_capture()
            self.state.modal = None
            return
        self._failures = 0
        self.state.scroll[view_name(self.state)] = frame.scroll
        self.console.write(frame.lines)

    def _sync_size(self) -> None:
        cols, rows = self.console.size()
        self.state.cols, self.state.rows = max(1, cols), max(1, rows)

    def _poll_status(self) -> None:
        every = STATUS_EVERY if self.state.tab == 0 else STATUS_EVERY_ELSEWHERE
        if self.state.now - self._last_status < every:
            return
        self._last_status = self.state.now
        self.data.status = self.bridge.get_status()
        if self.data.status.get("live"):
            self._quiet_until = self.state.now + LIVE_GRACE

    def _drain_results(self) -> None:
        while True:
            try:
                effect, response = self.worker.results.get_nowait()
            except queue.Empty:
                return
            if effect.kind == "_capture_ready":
                self._start_capture()
                continue
            if effect.kind == "_restore":
                if response.get("ok") is False:
                    self.state.status_problems = list(response.get("problems") or [])
                continue
            self._reduce(partial(controller.result, self.state, self.data, effect, response))

    def _poll_capture(self) -> None:
        if self.capture is None or not self.capture.active:
            return
        modal = self.state.modal
        if modal is None or modal.kind != "capture":
            # Whatever closed the box, the hook must not outlive it.
            self._stop_capture()
            return
        outcome = self.capture.poll()
        if outcome is not None:
            self._reduce(lambda: controller.captured(self.state, self.data, outcome))

    # -- effects ----------------------------------------------------------- #

    def _dispatch(self, effects: list[Effect]) -> None:
        bridge = self.bridge
        for effect in effects:
            kind = effect.kind
            if kind == "save":
                self._job(effect, partial(bridge.save_settings, dict(effect.payload)))
            elif kind == "run":
                self._job(effect, partial(bridge.set_running, bool(effect.payload)))
            elif kind == "pause":
                self._job(effect, bridge.toggle_pause)
            elif kind == "link":
                self._job(effect, partial(bridge.open_link, effect.payload))
            elif kind == "folder":
                self._job(effect, bridge.open_folder)
            elif kind == "capture":
                self.worker.submit(Effect("_capture_ready"), self._stop_for_capture)
            elif kind == "rearm":
                if self.capture is not None:
                    self.capture.rearm()
            elif kind == "uncapture":
                self._stop_capture()
            elif kind == "quit":
                self.state.quit = True

    def _job(self, effect: Effect, call: Callable[[], dict[str, Any]]) -> None:
        self.state.busy += 1
        self.worker.submit(effect, call)

    def _stop_for_capture(self) -> dict[str, Any]:
        """Stop watching while a key is picked: the loop's own key presses
        reach the capture hook too, and would be taken for the player's."""
        status = self.bridge.get_status()
        running, paused = bool(status.get("running")), bool(status.get("paused"))
        # Two captures can be asked for before the first has started; the
        # second must not forget that the first stopped a running loop.
        self._resume = (self._resume[0] or running, self._resume[1] or paused)
        if running:
            self.bridge.set_running(False)
        return {"ok": True}

    def _start_capture(self) -> None:
        modal = self.state.modal
        if modal is None or modal.kind != "capture":
            if self.capture is None:
                self._restore_run()
            return
        if self.capture is not None:
            return  # already listening for the box that is open
        try:
            self.capture = self.capture_factory(is_foreground=self.console.is_foreground)
            self.capture.start(held=keys_held_now())
        except Exception as exc:
            log.exception("key capture could not start")
            self.capture = None
            self.state.modal = None
            controller._refusal(self.state, [f"Keys cannot be read here: {exc}"])
            self._restore_run()

    def _stop_capture(self) -> None:
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        with contextlib.suppress(Exception):
            self.console.flush_input()
        self._restore_run()

    def _restore_run(self) -> None:
        running, paused = self._resume
        self._resume = (False, False)
        if not running:
            return

        def restore() -> dict[str, Any]:
            response = self.bridge.set_running(True)
            if response.get("ok") and paused:
                self.bridge.toggle_pause()
            return response

        self.worker.submit(Effect("_restore"), restore)

    # -- leaving --------------------------------------------------------------- #

    def shutdown(self) -> None:
        """Let go of every key and stop the loop, whatever state things are in."""
        if self.capture is not None:
            with contextlib.suppress(Exception):
                self.capture.stop()
            self.capture = None
        self.worker.wait_idle(QUIT_WAIT)
        self.worker.stop()
        try:
            self.bridge.close()
        except Exception:
            log.debug("could not stop the loop cleanly", exc_info=True)
        emergency_release()
