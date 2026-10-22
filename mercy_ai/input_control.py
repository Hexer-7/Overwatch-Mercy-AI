# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Sending keystrokes to the game, safely.

``keyboard.press(heal_btn)`` sends a key-down with no matching key-up.  If the
process dies -- Ctrl+C, a crash, closing the console, Task Manager -- while a
key is down, Windows keeps it **held**: the game keeps firing Mercy's beam and
the keyboard behaves as if a key is stuck until it is pressed and released by
hand.

Every press therefore goes through :class:`KeyController`, which tracks what is
down and can put it all back up from an ``atexit`` hook, a signal handler or a
``finally`` block.

The ``keyboard`` module is imported lazily and can be replaced, so this module
imports and tests anywhere.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import signal
import sys
import threading
import weakref
from collections.abc import Callable
from typing import Any, Protocol

from .config_schema import MOUSE_TRIGGERS
from .errors import ConfigError

log = logging.getLogger(__name__)

__all__ = [
    "HookBackend",
    "KeyController",
    "KeyboardBackend",
    "MouseTrigger",
    "emergency_release",
    "install_hooks",
    "install_pause_key",
    "install_safety_net",
    "remove_hooks",
]


class KeyboardBackend(Protocol):
    """The part of the ``keyboard`` module this project uses for output."""

    def press(self, key: Any, /) -> None: ...

    def release(self, key: Any, /) -> None: ...

    def key_to_scan_codes(self, name: str, /) -> tuple[int, ...]: ...


class HookBackend(Protocol):
    """The part of the ``keyboard`` module this project uses for input hooks."""

    def hook_key(self, key: str, callback: Any, suppress: bool = ...) -> Any: ...

    def add_hotkey(self, key: str, callback: Any, suppress: bool = ...) -> Any: ...


def _real_backend() -> KeyboardBackend:
    import keyboard

    return keyboard


def _real_hook_backend() -> HookBackend:
    import keyboard

    return keyboard


#: Every controller that may be holding a key, so that one call can let go of
#: all of them from any thread without taking any other lock first.
_LIVE: weakref.WeakSet[KeyController] = weakref.WeakSet()
#: Set for good once the program is going away: nothing is pressed after it.
_FROZEN = threading.Event()


def emergency_release() -> None:
    """Stop every future press and put every held key back up.

    Safe from any thread, including a console close handler that Windows gives
    only a few seconds: it takes no lock but each controller's own. The freeze
    matters because a loop thread that is slow to stop could otherwise press a
    key again just after this has released it.
    """
    _FROZEN.set()
    for controller in list(_LIVE):
        controller.release_all()


atexit.register(emergency_release)


class KeyController:
    """Presses and releases keys, and never loses track of what is down.

    Thread-safe. The loop thread presses keys while the pause hotkey (on the
    ``keyboard`` library's thread) and the window's Pause button release them.
    Unlocked, ``release_all`` could copy the held set, release it, and then
    ``clear()`` a key the loop had pressed in between -- leaving that key down
    in the game with nothing tracking it, so nothing would ever let it go.
    """

    def __init__(self, backend: KeyboardBackend | None = None) -> None:
        self._backend = backend
        self._held: set[Any] = set()
        # Re-entrant because release_all() calls release().
        self._lock = threading.RLock()
        _LIVE.add(self)

    @property
    def backend(self) -> KeyboardBackend:
        if self._backend is None:
            self._backend = _real_backend()
        return self._backend

    @property
    def held(self) -> frozenset[Any]:
        with self._lock:
            return frozenset(self._held)

    def resolve(self, name: str, label: str) -> int:
        """Turn a key name into a scan code once, at startup.

        ``keyboard.press("p")`` re-resolves the name through the layout table on
        every call and raises ``ValueError`` for a name the current layout does
        not have -- at the worst possible moment, mid-fight.  Resolving up front
        turns that into a startup error with an actionable message, and skips the
        lookup in the hot loop.
        """
        try:
            codes = self.backend.key_to_scan_codes(name)
        except Exception as exc:
            raise ConfigError(
                f"Your {label} is set to '{name}', which this keyboard layout does not have. "
                f"Pick it again in Settings."
            ) from exc
        if not codes:
            raise ConfigError(
                f"Your {label} is set to '{name}', which is not a key Mercy AI can press. "
                f"Pick it again in Settings."
            )
        return codes[0]

    def press(self, key: Any) -> None:
        with self._lock:
            # Checked under the lock: emergency_release() takes it too, so a
            # press that had already started cannot land after the release.
            if _FROZEN.is_set() or key in self._held:
                return
            self.backend.press(key)
            self._held.add(key)

    def release(self, key: Any) -> None:
        with self._lock:
            self._held.discard(key)
            try:
                self.backend.release(key)
            except Exception:  # pragma: no cover - a release must never propagate
                log.debug("release of %r failed", key, exc_info=True)

    def release_all(self) -> None:
        """Put every held key back up.  Safe to call any number of times."""
        with self._lock:
            for key in list(self._held):
                self.release(key)
            self._held.clear()

    def __enter__(self) -> KeyController:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release_all()


def install_safety_net(
    controller: KeyController,
    register_atexit: Callable[[Callable[[], None]], Any] | None = None,
    register_signal: Callable[[int, Callable[..., None]], Any] | None = None,
) -> list[int]:
    """Release every held key on exit, however the program ends.

    Returns the signals that were successfully hooked, so a caller (or a test)
    can see what coverage it got. Signal handlers can only be installed from the
    main thread; elsewhere only the exit hook is registered (and every
    controller is released at exit by :func:`emergency_release` anyway).
    """
    register_atexit = register_atexit or atexit.register
    register_signal = register_signal or signal.signal
    register_atexit(controller.release_all)
    if (
        register_signal is signal.signal
        and threading.current_thread() is not threading.main_thread()
    ):
        return []

    def handler(signum: int, _frame: object) -> None:
        controller.release_all()
        sys.exit(128 + signum)

    hooked: list[int] = []
    candidates = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)  # Windows: console close / Ctrl+Break
    if sigbreak is not None:
        candidates.append(sigbreak)
    for signum in candidates:
        try:
            register_signal(signum, handler)
            hooked.append(int(signum))
        except (ValueError, OSError, RuntimeError):
            # Not available on this platform, or we are not on the main thread.
            log.debug("could not hook signal %s", signum)
    return hooked


def install_hooks(
    keybind_btn: str,
    pause_toggle_button: str,
    on_press: Callable[[], None],
    on_release: Callable[[], None],
    on_toggle_pause: Callable[[], None],
    backend: HookBackend | None = None,
) -> list[Any]:
    """Event-driven trigger and pause keys.  Returns the hooks, for :func:`remove_hooks`.

    Hooks rather than polling: they cost nothing while idle and fire on the
    actual key event, whatever else the program is doing.

    ``suppress=False`` is deliberate and load-bearing: the keystroke must still
    reach the game.
    """
    hooks: HookBackend = backend if backend is not None else _real_hook_backend()

    def trigger(event: Any) -> None:
        if getattr(event, "event_type", "down") == "up":
            on_release()
        else:
            on_press()

    handles = [_hook(hooks, keybind_btn, trigger, "trigger")]
    try:
        handles += install_pause_key(pause_toggle_button, on_toggle_pause, hooks)
    except BaseException:
        remove_hooks(handles)
        raise
    return handles


def _hook(hooks: HookBackend, key: str, callback: Callable[..., Any], label: str) -> Any:
    """Hook one key (press and release), turning the library's ValueError into a sentence.

    One ``hook_key`` per key, never an ``on_press_key`` and an
    ``on_release_key`` for the same name: the ``keyboard`` library files both
    removers under the key's name, so the second overwrites the first and the
    release hook could then never be removed.
    """
    try:
        return hooks.hook_key(key, callback, suppress=False)
    except ValueError as exc:
        raise ConfigError(
            f"Your {label} key is set to '{key}', which is not a key Mercy AI can watch. "
            f"Pick it again in Settings."
        ) from exc


def remove_hooks(handles: list[Any]) -> None:
    """Remove only these hooks, leaving any other hook in the process alone.

    ``keyboard.unhook_all()`` would also remove a hook someone else installed,
    such as the text interface's key capture.
    """
    for handle in handles:
        if callable(handle):
            with contextlib.suppress(Exception):
                handle()


def install_pause_key(
    pause_toggle_button: str,
    on_toggle_pause: Callable[[], None],
    backend: HookBackend | None = None,
) -> list[Any]:
    """Make the pause key toggle pause, whatever else is held down.  Returns the hooks.

    Not ``keyboard.add_hotkey``: a hotkey in the ``keyboard`` library fires only
    when the set of keys held down is **exactly** the hotkey, so F2 pressed while
    walking (``{w, f2}``) would do nothing -- failing in exactly the situation it
    exists for, mid-fight and on the move. That set is also read later, on the
    library's processing thread, so a very quick tap could be missed.

    A key hook fires on the F2 key-down itself, regardless of what else is
    held. The latch stops a held F2 (keyboard auto-repeat sends a stream of
    key-downs) from flipping pause on and off ten times a second.

    A name with ``+`` in it is a real combination, which only ``add_hotkey`` can
    express, so that still uses it.
    """
    hooks: HookBackend = backend if backend is not None else _real_hook_backend()
    if "+" in pause_toggle_button.strip("+"):
        try:
            return [hooks.add_hotkey(pause_toggle_button, on_toggle_pause, suppress=False)]
        except ValueError as exc:
            raise ConfigError(
                f"Your pause key is set to '{pause_toggle_button}', which is not a key "
                f"Mercy AI can watch. Pick it again in Settings."
            ) from exc

    down = threading.Event()

    def pause(event: Any) -> None:
        if getattr(event, "event_type", "down") == "up":
            down.clear()
            return
        if down.is_set():
            return  # auto-repeat of a key that is already down
        down.set()
        on_toggle_pause()

    return [_hook(hooks, pause_toggle_button, pause, "pause")]


class MouseTrigger:
    """Mouse-button trigger via pynput.

    ``Listener.start()`` already runs on its own thread, so no wrapper thread is
    needed.  Keeping the listener handle lets :meth:`stop` remove the low-level
    Windows hook cleanly, and lets tests run without leaking a global hook.
    """

    def __init__(
        self,
        button_name: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        listener_factory: Callable[..., Any] | None = None,
        accept: Callable[[int, int], bool] | None = None,
    ) -> None:
        if button_name not in MOUSE_TRIGGERS:
            raise ValueError(f"{button_name!r} is not a mouse trigger")
        self.button_name = button_name
        self._on_press = on_press
        self._on_release = on_release
        self._factory = listener_factory
        #: Asked where each press landed; a press it refuses is ignored until
        #: the button comes back up.
        self._accept = accept
        self._listener: Any | None = None

    def handle_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        """pynput callback.  Only the configured button drives the state."""
        wanted = "left" if self.button_name == "left_mouse" else "right"
        if getattr(button, "name", None) != wanted:
            return
        if pressed:
            if self._accept is not None and not self._accept(x, y):
                log.debug("trigger press at %d,%d is not on the game; ignored", x, y)
                return
            self._on_press()
        else:
            # Always passed on: releasing a trigger that never started is
            # harmless, and it can never leave the loop running.
            self._on_release()

    def start(self) -> None:
        factory = self._factory
        if factory is None:  # pragma: no cover - needs pynput and a real mouse
            from pynput.mouse import Listener

            factory = Listener
        self._listener = factory(on_click=self.handle_click)
        # daemon=True so a stuck hook can never keep the process alive.
        # Not every listener implementation exposes it; that is not fatal.
        with contextlib.suppress(Exception):
            self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # pragma: no cover
                log.debug("mouse listener stop failed", exc_info=True)
            self._listener = None
