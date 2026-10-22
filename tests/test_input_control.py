# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The pause key and the key controller.

The pause key is the one control a player reaches for mid-fight, so it is
checked against the real ``keyboard`` library: its ``add_hotkey`` ignores F2
whenever another key is held, and :func:`install_pause_key` must not.
"""

from __future__ import annotations

import threading
import types

import pytest

from mercy_ai.input_control import KeyController, install_hooks, install_pause_key


class FakeHooks:
    """Records what was hooked and lets a test fire key events at it."""

    def __init__(self) -> None:
        self.keys: dict[str, list[object]] = {}
        self.hotkeys: dict[str, object] = {}

    def hook_key(self, key, callback, suppress=False):
        assert suppress is False, "the key must still reach the game"
        self.keys.setdefault(key, []).append(callback)
        return lambda: self.keys[key].remove(callback)

    def add_hotkey(self, key, callback, suppress=False):
        self.hotkeys[key] = callback
        return lambda: self.hotkeys.pop(key)

    def send(self, key: str, kind: str) -> None:
        event = type("Event", (), {"event_type": kind, "name": key})()
        for callback in list(self.keys.get(key, [])):
            callback(event)


def test_pause_key_toggles_once_per_press_despite_auto_repeat():
    hooks = FakeHooks()
    toggles = []
    install_pause_key("f2", lambda: toggles.append(1), hooks)

    for _ in range(5):  # a held key repeats its key-down
        hooks.send("f2", "down")
    hooks.send("f2", "up")
    hooks.send("f2", "down")
    hooks.send("f2", "up")

    assert len(toggles) == 2
    assert hooks.hotkeys == {}


def test_a_real_combination_still_uses_add_hotkey():
    hooks = FakeHooks()
    install_pause_key("shift+f2", lambda: None, hooks)
    assert list(hooks.hotkeys) == ["shift+f2"]
    assert hooks.keys == {}


def test_install_hooks_routes_the_pause_key_through_the_new_handler():
    hooks = FakeHooks()
    pressed, released, toggled = [], [], []
    install_hooks(
        "x",
        "f2",
        lambda: pressed.append(1),
        lambda: released.append(1),
        lambda: toggled.append(1),
        hooks,
    )
    hooks.send("x", "down")
    hooks.send("x", "up")
    hooks.send("f2", "down")
    assert (len(pressed), len(released), len(toggled)) == (1, 1, 1)
    assert hooks.hotkeys == {}


# --------------------------------------------------------------------------- #
# F2 while walking, against keyboard 0.13.5's real matching logic
# --------------------------------------------------------------------------- #

SCAN_CODES = {
    "f2": 60, "w": 17, "k": 37,
    "shift": 42, "left shift": 42, "right shift": 54,
    "ctrl": 29, "left ctrl": 29, "right ctrl": 157,
    "alt": 56, "left alt": 56, "right alt": 184, "alt gr": 541,
    "windows": 91, "left windows": 91, "right windows": 92,
}  # fmt: skip


@pytest.fixture
def offline_keyboard(monkeypatch):
    """The real ``keyboard`` module with no OS hook behind it.

    Events are fed straight into the listener's two stages -- the hook-thread
    callback that tracks which keys are down, then the processing-thread step
    that runs hooks and hotkeys -- which is exactly what the OS hook would do.
    """
    keyboard = pytest.importorskip("keyboard")
    listener = keyboard._listener
    monkeypatch.setattr(keyboard._os_keyboard, "init", lambda: None)
    monkeypatch.setattr(listener, "start_if_necessary", lambda: None)
    monkeypatch.setattr(
        keyboard,
        "key_to_scan_codes",
        lambda key, _error_if_missing=True: (SCAN_CODES[key],) if key in SCAN_CODES else (),
    )
    listener.init()
    keyboard._pressed_events.clear()

    def send(name: str, kind: str) -> None:
        event = keyboard.KeyboardEvent(kind, SCAN_CODES[name], name=name)
        listener.direct_callback(event)
        listener.pre_process_event(event)

    yield keyboard, send
    keyboard._pressed_events.clear()
    listener.init()


def _presses_that_paused(keyboard, send, install) -> tuple[int, int]:
    hits: list[int] = []
    install(lambda: hits.append(1))
    send("f2", keyboard.KEY_DOWN)
    send("f2", keyboard.KEY_UP)
    alone = len(hits)
    send("w", keyboard.KEY_DOWN)  # walking
    send("f2", keyboard.KEY_DOWN)
    send("f2", keyboard.KEY_UP)
    send("w", keyboard.KEY_UP)
    return alone, len(hits) - alone


def test_add_hotkey_misses_f2_while_walking(offline_keyboard):
    """Why the pause key avoids ``add_hotkey``: it misses F2 while walking."""
    keyboard, send = offline_keyboard
    alone, walking = _presses_that_paused(
        keyboard, send, lambda cb: keyboard.add_hotkey("f2", cb, suppress=False)
    )
    assert (alone, walking) == (1, 0)


def test_pause_key_fires_while_walking(offline_keyboard):
    keyboard, send = offline_keyboard
    alone, walking = _presses_that_paused(
        keyboard, send, lambda cb: install_pause_key("f2", cb, keyboard)
    )
    assert (alone, walking) == (1, 1)


# --------------------------------------------------------------------------- #
# KeyController
# --------------------------------------------------------------------------- #


class FakeKeys:
    def __init__(self) -> None:
        self.down: set[int] = set()

    def press(self, key):
        self.down.add(key)

    def release(self, key):
        self.down.discard(key)

    def key_to_scan_codes(self, name):
        return (ord(name),)


def test_release_all_leaves_nothing_down():
    keys = FakeKeys()
    controller = KeyController(keys)
    controller.press(1)
    controller.press(2)
    controller.release_all()
    assert keys.down == set()
    assert controller.held == frozenset()


def test_a_press_during_release_all_is_not_lost():
    """The pause handler's release_all racing a press from the loop thread.

    The press is started from inside release_all, between the release and the
    final clear(). Without the lock it would complete there and clear() would
    forget it, leaving the key down in the game with nothing tracking it.
    """
    keys = FakeKeys()
    controller = KeyController(keys)
    controller.press(1)
    racer: list[threading.Thread] = []
    release = keys.release

    def release_and_race(key):
        release(key)
        if not racer:
            racer.append(threading.Thread(target=controller.press, args=(2,)))
            racer[0].start()
            racer[0].join(0.2)  # unlocked it finishes here; locked it must wait

    keys.release = release_and_race
    controller.release_all()
    racer[0].join()
    assert keys.down == set(controller.held) == {2}


# --------------------------------------------------------------------------- #
# Letting go of everything, from anywhere
# --------------------------------------------------------------------------- #


class RecordingBackend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def press(self, key):
        self.events.append(f"down:{key}")

    def release(self, key):
        self.events.append(f"up:{key}")

    def key_to_scan_codes(self, name):
        return (25,)


@pytest.fixture
def unfrozen(monkeypatch):
    from mercy_ai import input_control

    monkeypatch.setattr(input_control, "_FROZEN", threading.Event())
    return input_control


def test_emergency_release_lets_go_and_blocks_later_presses(unfrozen):
    backend = RecordingBackend()
    controller = unfrozen.KeyController(backend)
    controller.press(25)
    unfrozen.emergency_release()
    assert backend.events == ["down:25", "up:25"]
    controller.press(25)
    assert controller.held == frozenset()
    assert backend.events == ["down:25", "up:25"]


def test_emergency_release_needs_no_other_lock(unfrozen):
    controller = unfrozen.KeyController(RecordingBackend())
    controller.press(25)
    busy = threading.RLock()
    busy.acquire()  # stands in for a runner lock held elsewhere
    done = threading.Event()
    threading.Thread(target=lambda: (unfrozen.emergency_release(), done.set())).start()
    assert done.wait(1.0)
    busy.release()


def test_hooks_are_returned_so_only_they_are_removed():
    from mercy_ai.input_control import remove_hooks

    hooks = FakeHooks()
    other = []
    hooks.hook_key("q", other.append)  # someone else's hook
    handles = install_hooks("e", "f2", lambda: None, lambda: None, lambda: None, hooks)
    remove_hooks(handles)
    assert hooks.keys == {"q": [other.append], "e": [], "f2": []}


def test_removing_the_hooks_leaves_nothing_in_the_real_library(offline_keyboard):
    from mercy_ai.input_control import remove_hooks

    keyboard, _send = offline_keyboard
    handles = install_hooks("k", "f2", lambda: None, lambda: None, lambda: None, keyboard)
    remove_hooks(handles)
    assert not any(keyboard._listener.nonblocking_keys.values())


def test_a_key_the_library_cannot_watch_is_a_sentence():
    from mercy_ai.errors import ConfigError

    class Hooks:
        def hook_key(self, key, callback, suppress=False):
            raise ValueError(key)

    with pytest.raises(ConfigError, match="Pick it again in Settings"):
        install_pause_key("nonsense", lambda: None, Hooks())


def test_a_click_off_the_game_never_starts_the_trigger():
    from mercy_ai.input_control import MouseTrigger

    events = []
    trigger = MouseTrigger(
        "left_mouse",
        lambda: events.append("press"),
        lambda: events.append("release"),
        accept=lambda x, _y: x < 100,
    )
    left = types.SimpleNamespace(name="left")
    trigger.handle_click(500, 10, left, True)  # another monitor
    trigger.handle_click(500, 10, left, False)
    trigger.handle_click(50, 10, left, True)  # the game
    trigger.handle_click(50, 10, left, False)
    assert events == ["release", "press", "release"]


def test_the_click_check_reads_the_window_under_the_pointer():
    from mercy_ai.focus import click_is_on_overwatch

    names = {1: "Overwatch.exe", 2: "Discord.exe"}

    def at(x, _y):
        return 1 if x < 100 else 2

    assert click_is_on_overwatch(50, 0, window_at=at, name_for_hwnd=names.get)
    assert not click_is_on_overwatch(500, 0, window_at=at, name_for_hwnd=names.get)
    assert not click_is_on_overwatch(0, 0, window_at=lambda *_: 0, name_for_hwnd=names.get)


def test_a_click_check_that_breaks_lets_the_click_through():
    from mercy_ai.focus import click_is_on_overwatch

    def broken(*_args):
        raise OSError("no window")

    assert click_is_on_overwatch(1, 1, window_at=broken)
