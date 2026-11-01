# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Running the real control loop on a background thread.

Nothing here is a simulation.  :class:`LoopRunner` builds the real
:class:`~mercy_ai.loop.ControlLoop`, :class:`~mercy_ai.input_control.KeyController`
and input hooks, and runs them on a background thread while the window or the
text interface owns the main one.

The interesting half is :class:`StatusHub`, and it is pure.  ``ControlLoop``
already calls a ``status`` callback roughly ten times a second with everything a
dashboard needs, so the hub just has to hold the last set of numbers and hand
out a snapshot.  Keeping that separable is what lets
the Status tab be tested with no thread, no screen and no game.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "LIVE_FIELDS",
    "LoopRunner",
    "StatusHub",
    "apply_capture_settings",
    "changed_fields",
    "format_uptime",
]

#: Settings the running loop reads from ``self.config`` on every frame, so a new
#: value takes effect by swapping the config -- no rebuild, no dropped frames,
#: no released keys. Anything else is baked in when the loop is built (key scan
#: codes, hooks, the focus gate, the Flash Heal planner, capture flags) and
#: still needs a restart. ``theme`` is not read by the loop at all.
LIVE_FIELDS = frozenset(
    {
        "threshold_percent",
        "bomb_threshold",
        "action_cooldown",
        "target_fps",
        "bomb_detection",
        "hysteresis",
        "dynamic_heal",
        "dynamic_heal_drop",
        "theme",
    }
)


def changed_fields(old: Any, new: Any) -> set[str] | None:
    """Names of the dataclass fields that differ, or ``None`` if not comparable."""
    if not (dataclasses.is_dataclass(old) and dataclasses.is_dataclass(new)):
        return None
    if type(old) is not type(new):
        return None
    return {
        field.name
        for field in dataclasses.fields(old)
        if getattr(old, field.name) != getattr(new, field.name)
    }


def format_uptime(seconds: float) -> str:
    """``M:SS`` under an hour, ``H:MM:SS`` above it.

    The dashboard cell is narrow and the number is glanceable rather than
    precise, so it never grows a leading zero it does not need.
    """
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class StatusHub:
    """The last thing the loop reported, plus what the window derives from it.

    Thread-safe because ``record`` is called from the loop thread and
    ``snapshot`` from the web view's, and the two are unrelated.  The lock is
    uncontended in practice -- ten writes a second against a handful of reads.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._started: float | None = None
        self.paused = False
        self.triggered = False
        self.health = 0.0
        self.bomb = 0.0
        self.fps = 0.0
        self.boosting = False
        self.flash: str | None = None
        self.error: str | None = None
        self._last_seen = 0.0
        # The page shows an average: the instantaneous rate flickers too fast
        # to read. Roughly six seconds of samples at the loop's ~10 Hz status
        # rate.
        self._fps_samples: deque[float] = deque(maxlen=64)

    # -- writing (loop thread) --------------------------------------------- #

    def start(self) -> None:
        with self._lock:
            self._started = self._clock()

    def stop(self) -> None:
        """Forget the live numbers, so the tab goes quiet."""
        with self._lock:
            self._started = None
            self.health = self.bomb = self.fps = 0.0
            self.boosting = False
            self.triggered = False
            self.flash = None
            self._fps_samples.clear()

    def record(
        self,
        paused: bool,
        health: float,
        bomb: float,
        fps: float,
        boosting: bool,
        flash: str | None = None,
    ) -> None:
        """The ``ControlLoop`` status callback, verbatim.

        Matching that signature exactly is deliberate: the hub is fed by the
        loop's own status call, so there is no second code path that could
        disagree with what the loop is actually doing.
        """
        with self._lock:
            self.paused = paused
            self.health = health
            self.bomb = bomb
            self.fps = fps
            if fps > 0:
                self._fps_samples.append(fps)
            self.boosting = boosting
            self.flash = flash
            self.triggered = True
            self._last_seen = self._clock()

    def idle(self, paused: bool) -> None:
        """Called on frames where nothing was scored, so the bars fall to zero."""
        with self._lock:
            self.paused = paused
            self.triggered = False
            self.health = self.bomb = self.fps = 0.0
            self.boosting = False

    def fail(self, message: str) -> None:
        with self._lock:
            self.error = message
            self._started = None

    # -- reading (window thread) -------------------------------------------- #

    def snapshot(self) -> dict[str, Any]:
        """Everything the Status tab draws, already in display form."""
        with self._lock:
            running = self._started is not None
            uptime = 0.0 if self._started is None else self._clock() - self._started
            # The loop only reports while the trigger is held, so a report that
            # has gone stale means the user let go rather than that the loop
            # died.  Half a second is several missed status intervals.
            live = self.triggered and (self._clock() - self._last_seen) < 0.5
            # Three words only: "watching but the trigger is not held" gets no
            # word of its own, because the user does not act on it -- the
            # trigger is in their hand.
            if not running:
                state = "STOPPED"
            elif self.paused:
                state = "PAUSED"
            else:
                state = "RUNNING"
            average = sum(self._fps_samples) / len(self._fps_samples) if self._fps_samples else 0.0
            mode = "IDLE" if not live else ("BOOST" if self.boosting else "HEAL")
            return {
                "running": running,
                "live": live,
                "paused": self.paused,
                "state_word": state,
                "mode_word": mode,
                "health": self.health,
                "bomb": self.bomb,
                "fps": self.fps,
                "fps_avg": average,
                "flash": self.flash,
                "uptime": format_uptime(uptime),
                "error": self.error,
            }


class LoopRunner:
    """Owns the control loop, its keys and its hooks on a background thread.

    ``build`` is injectable so the whole lifecycle -- start, pause, stop, restart
    with new settings -- can be exercised against a fake loop.  The default
    builds the real thing.
    """

    def __init__(
        self,
        config: Any,
        assets: Any,
        *,
        hub: StatusHub | None = None,
        build: Callable[[Any, Any, StatusHub], tuple[Any, Callable[[], None]]] | None = None,
    ) -> None:
        self.config = config
        self.assets = assets
        self.hub = hub or StatusHub()
        self._build = build or _build_real_loop
        self._thread: threading.Thread | None = None
        self._loop: Any = None
        self._close: Callable[[], None] | None = None
        self._state: Any = None
        self._ready = threading.Event()
        self._build_error: BaseException | None = None
        # The window runs every bridge call on a thread of its own, so a quick
        # double-click on a switch is two restarts at once. Unserialised, both
        # would stop the same loop and both start one, orphaning the second with
        # its own mouse hook and keys, out of reach of Pause and F2. Re-entrant because restart() calls stop() and start(). The loop
        # thread never takes it, so stop()'s join cannot deadlock on it.
        self._lifecycle = threading.RLock()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 15.0) -> None:
        """Build and run the loop.  A second call while alive does nothing.

        The loop is built **on the thread that will run it**, and that is not a
        detail. ``mss`` creates its GDI device contexts in ``__init__``
        (``GetWindowDC`` then ``CreateCompatibleDC``) and those belong to the
        thread that created them, so a ``ScreenGrabber`` built here and used
        there fails inside ``BitBlt`` on every single frame -- and fails again in
        ``ReleaseDC`` on the way out. ``ScreenGrabber``'s own docstring says as
        much: *keep it owned by the capture loop*.

        Construction is still awaited, so a failure to resolve a key or open the
        screen is reported to the caller rather than appearing asynchronously.
        """
        with self._lifecycle:
            if self.alive:
                return
            self.hub.error = None
            # A new State starts unpaused. A pause left over from before a stop
            # would show PAUSED and "Resume" over a running loop, and Resume
            # would then really pause it. restart() puts a carried pause back
            # itself.
            self.hub.paused = False
            self._ready = threading.Event()
            self._build_error = None
            # Daemon: if the window is destroyed by Windows rather than by our
            # own close handler, the process must still be able to exit.  The
            # safety net in input_control releases the keys either way.
            self._thread = threading.Thread(target=self._run, name="mercy-ai-loop", daemon=True)
            self._thread.start()
            self._ready.wait(timeout)
            if self._build_error is not None:
                raise self._build_error

    def _run(self) -> None:
        try:
            self._loop, self._close = self._build(self.config, self.assets, self.hub)
            self._state = getattr(self._loop, "state", None)
            self.hub.start()
        except BaseException as exc:
            log.exception("could not build the control loop")
            self._build_error = exc
            self.hub.fail(str(exc) or exc.__class__.__name__)
            self._ready.set()
            return
        self._ready.set()
        try:
            self._loop.run()
        except Exception as exc:
            log.exception("control loop stopped")
            self.hub.fail(str(exc) or exc.__class__.__name__)
        finally:
            # Teardown belongs on this thread too, for the same reason as setup.
            self._shutdown()

    def _shutdown(self) -> None:
        if self._close is not None:
            try:
                self._close()
            except Exception:
                log.debug("teardown failed", exc_info=True)
            self._close = None
        self.hub.stop()

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the loop to finish and wait briefly for it.

        ``State.shutdown`` is the loop's own exit flag, so this uses the loop's
        normal exit path rather than a second mechanism.
        """
        with self._lifecycle:
            state = self._state
            if state is not None:
                state.shutdown.set()
                state.run.clear()
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout)
            self._thread = None
            self._shutdown()

    def toggle_pause(self) -> bool:
        """Flip pause from the window, exactly as the pause hotkey would."""
        with self._lifecycle:
            if self._state is None or not self.alive:
                return False
            self._state.toggle_pause()
            paused = bool(self._state.is_paused)
            self.hub.paused = paused
            return paused

    def restart(self, config: Any, assets: Any) -> None:
        """Apply new settings, rebuilding the loop only when it has to.

        Settings in :data:`LIVE_FIELDS` are read by the loop every frame, so the
        new ``Config`` is simply handed to the running loop: a rebuild would
        release the beam for up to two seconds, in the middle of a fight.

        Anything else (keys, hooks, Flash Heal, the focus gate) is baked in when
        the loop is built, so it is rebuilt, and a pause the user had set is
        carried across.
        """
        with self._lifecycle:
            changed = changed_fields(self.config, config)
            loop = self._loop
            if (
                changed is not None
                and changed <= LIVE_FIELDS
                and assets is self.assets
                and self.alive
                and loop is not None
                and hasattr(loop, "config")
            ):
                self.config = config
                loop.config = config
                return

            was_running = self.alive
            was_paused = bool(self._state is not None and self._state.is_paused)
            self.stop()
            self.config = config
            self.assets = assets
            if was_running:
                self.start()
                if was_paused and self._state is not None:
                    # Nothing is held yet in a loop that has just been built, so
                    # there is nothing for the pause handler to release.
                    self._state.paused.set()
                    self.hub.paused = True


def apply_capture_settings(config: Any) -> dict[str, Any]:
    """Apply the capture and vision settings before a loop is built.

    By default mss blits with ``SRCCOPY | CAPTUREBLT``.  ``CAPTUREBLT`` asks GDI
    to include layered windows, which makes the desktop compositor flatten the
    whole desktop before the copy. Against a game in exclusive fullscreen that
    flattening cannot be done, and ``BitBlt`` fails -- reporting a stale,
    meaningless ``GetLastError`` such as "The system cannot find the file
    specified" -- and only while Overwatch is actually running. It also costs
    most of the frame time, so ``fast_capture`` (on by default) turns it off.

    ``fast_capture = 0`` is honoured explicitly rather than skipped: the flag is
    a module global in mss, so leaving it alone would silently inherit whatever
    the previous run set.
    """
    from ..capture import captureblt_enabled, use_fast_capture

    use_fast_capture(bool(config.fast_capture))
    applied = {"captureblt": captureblt_enabled()}
    log.info("capture settings: CAPTUREBLT=%s", applied["captureblt"])
    return applied


def _build_real_loop(
    config: Any,
    assets: Any,
    hub: StatusHub,
    *,
    loop_factory: Callable[..., Any] | None = None,
) -> tuple[Any, Callable[[], None]]:
    """Wire a live loop with real keys, hooks and screen capture.

    Imported lazily and only here: everything below needs ``keyboard``,
    ``pynput`` and a Windows desktop, none of which a test has.

    *loop_factory* replaces :class:`~mercy_ai.loop.ControlLoop` with something
    that accepts the same keywords -- the same seam ``LoopRunner`` already offers
    through ``build``, one level further in. A test harness can substitute a
    loop that records what it sees; nothing in this package passes it, and the
    default is the only loop the shipped program ever runs.
    """
    from ..capture import CombinedCapture, ScreenGrabber
    from ..focus import FocusGate, click_is_on_overwatch
    from ..input_control import (
        KeyController,
        MouseTrigger,
        install_hooks,
        install_pause_key,
        install_safety_net,
        remove_hooks,
    )
    from ..loop import ControlLoop
    from ..scoreboard import ScoreboardKey
    from ..state import State
    from ..weapon import StaffWatch

    # Before the grabber exists: CAPTUREBLT is read inside every blit.
    apply_capture_settings(config)

    controller = KeyController()
    install_safety_net(controller)
    state: Any = None
    trigger: Any = None
    grabber: Any = None
    hooks: list[Any] = []

    def close() -> None:
        controller.release_all()
        if grabber is not None:
            grabber.close()
        if trigger is not None:
            trigger.stop()
        if state is not None:
            state.shutdown.set()
        # Only this loop's own hooks: a restart with new bindings must not stack
        # a second set on the first, and a hook someone else installed (the text
        # interface's key capture) must survive.
        remove_hooks(hooks)

    def toggle_pause() -> None:
        """The pause hotkey, and the Pause button, both land here.

        Logged at INFO because "F2 does nothing" is otherwise unanswerable from
        a bug report: this line distinguishes *the key never arrived* from *the
        key arrived and the loop ignored it*.
        """
        state.toggle_pause()
        log.info("pause toggled -> %s", "paused" if state.is_paused else "running")

    def on_state_change(changed: Any) -> None:
        controller.release_all()
        hub.idle(changed.is_paused)

    try:
        heal_key = controller.resolve(config.heal_btn, "heal key")
        boost_key = controller.resolve(config.damage_boost_btn, "damage boost key")
        flash_key = (
            controller.resolve(config.flash_heal_btn, "Flash Heal key")
            if config.flash_heal_enabled
            else None
        )

        state = State(on_change=on_state_change)
        if config.uses_mouse_trigger:
            trigger = MouseTrigger(
                config.keybind_btn,
                state.trigger_pressed,
                state.trigger_released,
                # Part of the focus check: only while that is on.
                accept=click_is_on_overwatch if config.focus_gate and config.click_check else None,
            )
            trigger.start()
            hooks = install_pause_key(config.pause_toggle_button, toggle_pause)
        else:
            hooks = install_hooks(
                config.keybind_btn,
                config.pause_toggle_button,
                state.trigger_pressed,
                state.trigger_released,
                toggle_pause,
            )
        # If this line is in the log, the keys were bound. If the pause line
        # never follows it, the key press is not reaching the process at all --
        # an elevation or focus problem, not a Mercy AI one.
        log.info(
            "hooks installed: trigger=%s (%s) pause=%s",
            config.keybind_btn,
            "mouse" if config.uses_mouse_trigger else "keyboard",
            config.pause_toggle_button,
        )

        grabber = ScreenGrabber()
        # The staff check exists only for Flash Heal: with Flash Heal off, or
        # the check turned off, the weapon region is never even captured.
        weapon = None
        icons = getattr(assets, "weapon", None)
        if config.flash_heal_enabled and config.flash_heal_staff_check and icons is not None:
            weapon_region = icons.region.as_mss()
            # A grab overwrites the buffer the health and grenade frames point
            # into; the loop asks for the weapon only after scoring both.
            weapon = StaffWatch(
                icons,
                lambda: grabber.grab(weapon_region),
                threshold=config.flash_heal_weapon_threshold,
            )
        # Read once a frame with GetAsyncKeyState; only for Flash Heal.
        scoreboard = (
            ScoreboardKey(config.scoreboard_btn)
            if config.flash_heal_enabled
            and config.flash_heal_scoreboard_hold
            and config.scoreboard_btn
            else None
        )
        loop = (loop_factory or ControlLoop)(
            assets=assets,
            config=config,
            state=state,
            controller=controller,
            capture=CombinedCapture(assets.geometry.health, assets.geometry.bomb, grabber),
            heal_key=heal_key,
            boost_key=boost_key,
            flash_key=flash_key,
            focus_gate=FocusGate() if config.focus_gate else None,
            weapon=weapon,
            scoreboard=scoreboard,
            status=hub.record,
        )
    except BaseException:
        # A start that fails half-way must not leave a mouse listener, a hook
        # or a screen handle behind with nothing able to stop it.
        close()
        raise

    return loop, close
