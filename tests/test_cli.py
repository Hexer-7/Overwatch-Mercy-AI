# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The text interface, driven key by key against a real Bridge and a fake loop."""

from __future__ import annotations

import queue
import re
import threading
from dataclasses import replace
from typing import ClassVar

import pytest

from mercy_ai.cli import controller
from mercy_ai.cli.app import App
from mercy_ai.cli.capture import CANCEL, REFUSED, KeyCapture
from mercy_ai.cli.console import FakeConsole, decode_ansi, decode_key
from mercy_ai.cli.keys import SCAN_TO_CODE, scan_to_name
from mercy_ai.cli.screens import CLI_FIELDS, render, rows_for
from mercy_ai.cli.snapshot import check_frame, render_all_screens, sample_data, sample_screens
from mercy_ai.cli.state import UIState
from mercy_ai.cli.style import Style
from mercy_ai.cli.text import strip_ansi
from mercy_ai.config import Config, config_to_xml, read_config, write_config_atomic
from mercy_ai.state import State
from mercy_ai.ui.bridge import Bridge
from mercy_ai.ui.model import FIELD_TYPES, normalise_key
from mercy_ai.ui.runner import LoopRunner

KEYS = {"heal_btn": "p", "damage_boost_btn": "o"}


class FakeLoop:
    def __init__(self, config):
        self.config = config
        self.state = State()

    def run(self):
        self.state.shutdown.wait(5)


class SyncWorker:
    """Runs each job at once, so a test sees the answer on the next tick."""

    def __init__(self):
        self.results = queue.SimpleQueue()

    def submit(self, effect, job):
        self.results.put((effect, job()))

    def wait_idle(self, _timeout):
        return True

    def stop(self):
        pass


class FakeCapture:
    """Stands in for the keyboard hook: answers come from a list."""

    answers: ClassVar[list[str]] = []

    def __init__(self, **_kwargs):
        self.active = False

    def start(self, held=None):
        self.active = True

    def rearm(self):
        pass

    def stop(self):
        self.active = False

    def poll(self):
        return FakeCapture.answers.pop(0) if FakeCapture.answers else None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 0.05
        return self.now


def make_app(tmp_path, keys=(), *, config=None, setup=False, env=None, cols=80, rows=25):
    path = tmp_path / "config.xml"
    config = config or Config(resolution="1440", **KEYS)
    if setup:
        config = Config(resolution="1440", heal_btn="", damage_boost_btn="")
    else:
        write_config_atomic(config_to_xml(config), path)
    environment = {
        "needs_setup": setup,
        "elevated": True,
        "detected": "1440",
        "primary": "2560x1440",
        "resolutions": ["2160", "1440", "1080", "768"],
        "config_error": None,
        **(env or {}),
    }
    runner = LoopRunner(
        config, None if setup else object(), build=lambda c, _a, _h: (FakeLoop(c), lambda: None)
    )
    opened: list[str] = []
    bridge = Bridge(
        config=config,
        config_path=path,
        assets=None if setup else object(),
        runner=runner,
        environment=environment,
        loader=lambda _resolution: object(),
        opener=lambda p: opened.append(str(p)),
        url_opener=opened.append,
    )
    console = FakeConsole(cols, rows, keys)
    app = App(console, bridge, clock=Clock(), capture_factory=FakeCapture, worker=SyncWorker())
    app.opened = opened
    _APPS.append(app)
    return app, console, path


_APPS: list[App] = []


def drive(app, console, ticks_after=4):
    """Run until the key script is used up, then a few quiet ticks."""
    if not getattr(app, "started", False):
        app.started = True
        app._sync_size()
        app._dispatch(controller.start(app.state, app.data))
    console.exhausted = False
    while not console.exhausted and not app.state.quit:
        app._step()
    for _ in range(ticks_after):
        if app.state.quit:
            break
        app._step()
    return console.text()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Each test gets its own key freeze and leaves no loop running."""
    from mercy_ai import input_control

    monkeypatch.setattr(input_control, "_FROZEN", threading.Event())
    FakeCapture.answers = []
    yield
    FakeCapture.answers = []
    while _APPS:
        _APPS.pop().shutdown()


# --------------------------------------------------------------------------- #
# Parity and drawing
# --------------------------------------------------------------------------- #


def test_the_text_interface_can_change_every_setting_the_window_can_but_theme():
    assert set(CLI_FIELDS) == set(FIELD_TYPES) - {"theme"}


def test_every_screen_draws_within_its_size_in_both_glyph_sets():
    assert "Thx to Claude." in render_all_screens()


@pytest.mark.parametrize("size", [(80, 24), (100, 40), (150, 50), (200, 60)])
def test_every_screen_fits_other_sizes(size):
    cols, rows = size
    for name, state, data in sample_screens():
        lines = render(replace(state, cols=cols, rows=rows), data, Style()).lines
        assert not check_frame(lines, cols, rows), name


@pytest.mark.parametrize("size", [(79, 25), (80, 23), (1, 1), (10, 3)])
def test_a_tiny_window_only_asks_to_be_made_bigger(size):
    cols, rows = size
    lines = render(UIState(cols=cols, rows=rows), sample_data(), Style()).lines
    assert len(lines) == rows
    assert all(len(line) <= cols for line in lines)


def test_no_screen_suggests_a_heal_or_boost_key():
    for name, state, data in sample_screens():
        if not name.startswith("setup"):
            continue
        text = "\n".join(render(replace(state, cols=80, rows=25), data, Style()).lines)
        assert not re.search(r"\b[89]\b", text), name


def test_claude_is_written_in_claudes_orange():
    lines = render(UIState(tab=3), sample_data(), Style()).lines
    assert "\x1b[33;38;2;217;119;87mClaude\x1b[0m" in "\n".join(lines)


def test_about_names_the_developer_and_the_copyright():
    state = UIState(tab=3)
    text = "\n".join(render(state, sample_data(), Style(color=False)).lines)
    for words in ("_1_B", "Hexer-7", "Overwatch-Mercy-AI", "Thx to Claude.", "2024-2026 Hexer-7"):
        assert words in text


def test_a_long_problem_wraps_inside_the_screen():
    data = sample_data(running=False)
    data.status["error"] = (
        "Could not grab the screen. " * 12 + "C:\\Users\\محمد\\Downloads\\Mercy AI"
    )
    lines = render(UIState(), data, Style()).lines
    assert not check_frame(lines, 80, 25)


def test_not_running_as_administrator_is_said_on_status():
    data = sample_data()
    data.env["elevated"] = False
    text = "\n".join(render(UIState(), data, Style(color=False)).lines)
    assert "Not running as administrator" in text


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def test_letters_are_read_by_position_whatever_the_layout():
    # The P key on an Arabic layout types "ح", but is still the P key.
    assert decode_key(True, 0x50, ord("ح"), 0) == "p"
    assert decode_key(True, 0x09, 9, 0x10) == "shift+tab"
    assert decode_key(True, 0x43, 3, 0x08) == "ctrl+c"
    assert decode_key(False, 0x50, ord("p"), 0) is None


def test_terminal_escape_sequences_become_keys():
    assert decode_ansi("\x1b[A\x1b[Z\rq\x1b") == ["up", "shift+tab", "enter", "q", "esc"]


def test_captured_keys_get_the_windows_names():
    for scan, code in SCAN_TO_CODE.items():
        assert scan_to_name(scan) == normalise_key(code)
    assert scan_to_name(0x19) == "p"
    assert scan_to_name(0x48, is_keypad=True) is None


def test_capture_swallows_the_key_and_lets_the_opening_enter_go():
    events = []

    class Event:
        def __init__(self, scan, kind, name=""):
            self.scan_code, self.event_type, self.name, self.is_keypad = scan, kind, name, False

    capture = KeyCapture(
        hook=lambda cb, **_kw: events.append(cb) or "handle", unhook=lambda _h: None
    )
    capture.start()
    assert capture.on_event(Event(0x1C, "up")) is True  # the Enter that opened it
    assert capture.on_event(Event(0x19, "down", "ح")) is False
    assert capture.on_event(Event(0x19, "up")) is False
    assert capture.poll() == "p"
    capture.rearm()
    capture.on_event(Event(0x01, "down"))
    capture.on_event(Event(0x01, "up"))
    assert capture.poll() == CANCEL
    capture.rearm()
    capture.on_event(Event(0x5B, "down"))  # a Windows key cannot be bound
    capture.on_event(Event(0x5B, "up"))
    assert capture.poll() == REFUSED


# --------------------------------------------------------------------------- #
# Whole flows
# --------------------------------------------------------------------------- #


def test_first_run_setup_writes_the_config_and_starts(tmp_path):
    FakeCapture.answers = ["k", "l"]
    keys = [
        "enter",
        "enter",
        "enter",
        None,
        None,
        None,
        "enter",
        None,
        None,
        None,
        "enter",
        "enter",
    ]
    app, console, path = make_app(tmp_path, keys, setup=True)
    text = drive(app, console)
    saved = read_config(path)
    assert (saved.heal_btn, saved.damage_boost_btn, saved.resolution) == ("k", "l", "1440")
    assert app.state.screen == "main"
    assert app.data.status["running"]
    assert "Setup saved" in text


def test_quitting_setup_writes_nothing(tmp_path):
    app, console, path = make_app(tmp_path, ["q", "y"], setup=True)
    drive(app, console)
    assert app.state.quit
    assert not path.exists()


def test_setup_refuses_the_same_key_for_heal_and_boost(tmp_path):
    FakeCapture.answers = ["k", "k"]
    keys = ["enter", "enter", "enter", None, None, None, "enter", None, None, None]
    app, console, _path = make_app(tmp_path, keys, setup=True)
    text = drive(app, console)
    assert "Those are the same key" in text


def test_a_toggle_saves_only_that_setting(tmp_path):
    app, console, path = make_app(tmp_path, ["2", "down", "down", "down", "enter"])
    text = drive(app, console)
    assert read_config(path).dynamic_heal is True
    assert "Smooth healing is on." in text


FLASH_ON = {"flash_heal_trigger": "expiry", "flash_heal_on_health_drop": True}


def test_a_typed_number_is_saved_and_a_bad_one_is_explained(tmp_path):
    keys = ["2", "end", "enter"] + ["down"] * 10 + ["enter", "0", ".", "9", "enter"]
    config = Config(resolution="1440", **KEYS, **FLASH_ON)
    app, console, path = make_app(tmp_path, keys, config=config)
    text = drive(app, console)
    assert "is outside the range" in text
    assert app.state.edit is not None
    console.feed("backspace", "4", "enter")
    text = drive(app, console)
    assert read_config(path).flash_heal_drop == pytest.approx(0.4)
    assert app.state.edit is None


def test_arrows_step_a_number_and_save_once_it_settles(tmp_path):
    app, console, path = make_app(tmp_path, ["2", "left", "left", "left"])
    drive(app, console, ticks_after=0)
    assert read_config(path).threshold_percent == 10.0
    for _ in range(20):
        app._step()
    assert read_config(path).threshold_percent == 8.5


def test_binding_a_key_saves_it(tmp_path):
    FakeCapture.answers = ["k"]
    app, console, path = make_app(tmp_path, ["2", "down", "enter", None, None, None])
    text = drive(app, console)
    assert read_config(path).heal_btn == "k"
    assert "Heal key set to K." in text


def test_a_clash_is_worded_from_the_key_that_changed(tmp_path):
    FakeCapture.answers = ["f2"]
    app, console, path = make_app(tmp_path, ["2", "down", "enter", None, None, None])
    drive(app, console)
    assert app.state.modal.problem == "F2 is already your pause key. Pick a different heal key."
    assert read_config(path).heal_btn == "p"


def test_the_trigger_menu_offers_both_mouse_buttons(tmp_path):
    keys = ["2", "end", "enter", "home", "enter", "down", "enter"]
    app, console, path = make_app(tmp_path, keys)
    drive(app, console)
    assert read_config(path).keybind_btn == "right_mouse"


def test_reset_puts_back_only_that_setting(tmp_path):
    changed = Config(resolution="1440", **KEYS, threshold_percent=7.0, flash_heal_drop=0.3)
    app, console, path = make_app(tmp_path, ["2", "r"], config=changed)
    drive(app, console)
    saved = read_config(path)
    assert saved.threshold_percent == 10.0
    assert saved.flash_heal_drop == 0.3


def test_a_switch_has_no_reset(tmp_path):
    changed = Config(resolution="1440", **KEYS, dynamic_heal=True)
    app, console, path = make_app(tmp_path, ["2", "down", "down", "down", "r"], config=changed)
    text = drive(app, console)
    assert read_config(path).dynamic_heal is True
    assert "reset to" not in text


def test_a_changed_setting_is_marked_with_a_star_beside_its_value():
    data = sample_data()
    data.config["threshold_percent"] = 7.0
    lines = render(UIState(tab=1, cols=80, rows=25), data, Style(color=False)).lines
    dial = next(line for line in lines if "Switch to damage boost at" in line)
    assert strip_ansi(dial).rstrip().endswith("10 *")
    assert "changed" not in "\n".join(lines)


def test_advanced_says_to_try_changes_in_the_practice_range():
    lines = render(UIState(tab=1, advanced=True), sample_data(), Style()).lines
    text = "\n".join(lines)
    assert "\x1b[35;38;2;138;28;60;107m" in text
    assert "Practice Range" in strip_ansi(text)


def test_flash_heal_picks_emergency_or_normal_under_advanced(tmp_path):
    config = Config(resolution="1440", **KEYS, **FLASH_ON)
    keys = ["2", "end", "enter"] + ["down"] * 9 + ["right"]
    app, console, path = make_app(tmp_path, keys, config=config)
    text = drive(app, console)
    assert "After Ana's grenade" in text
    assert read_config(path).flash_heal_health_mode == "low"
    labels = [row.label for row in rows_for(app.state, app.data, Style())]
    assert "Fire when health is below" in labels
    assert "Fall that counts as an emergency" not in labels


def test_the_ana_switch_turns_only_the_grenade_trigger_off(tmp_path):
    config = Config(resolution="1440", **KEYS, **FLASH_ON)
    keys = ["2", "end", "enter"] + ["down"] * 7 + ["enter"]
    app, console, path = make_app(tmp_path, keys, config=config)
    drive(app, console)
    saved = read_config(path)
    assert saved.flash_heal_trigger == "off"
    assert saved.flash_heal_on_health_drop is True


def test_pause_and_stop_from_the_keyboard(tmp_path):
    app, console, _path = make_app(tmp_path, [None, "p", None, None])
    text = drive(app, console)
    assert "PAUSED" in text
    console.feed("s", None, None)
    text = drive(app, console)
    assert "STOPPED" in text


def test_a_key_that_is_also_the_pause_key_is_left_to_the_pause_hook(tmp_path):
    config = Config(resolution="1440", **KEYS, pause_toggle_button="s")
    app, console, _path = make_app(tmp_path, [None, "s", None], config=config)
    drive(app, console)
    assert app.data.status["running"]


def test_using_the_detected_resolution(tmp_path):
    env = {"detected": "1080", "primary": "1920x1080"}
    app, console, path = make_app(tmp_path, ["u", None], env=env)
    text = drive(app, console)
    assert read_config(path).resolution == "1080"
    assert "Resolution set to 1080p." in text


def test_about_links_open_by_name(tmp_path):
    app, console, _path = make_app(tmp_path, ["4", "down", "enter"])
    drive(app, console)
    assert app.opened == ["https://github.com/Hexer-7"]


def test_quitting_stops_the_loop(tmp_path):
    app, console, _path = make_app(tmp_path, [None, "q", "enter"])
    drive(app, console)
    app.shutdown()
    assert app.state.quit
    assert not app.bridge.get_status()["running"]


def test_ctrl_c_quits_at_once(tmp_path):
    app, console, _path = make_app(tmp_path, ["ctrl+c"])
    drive(app, console)
    assert app.state.quit


def test_a_key_that_breaks_the_controller_leaves_the_state_as_it_was(tmp_path, monkeypatch):
    app, console, _path = make_app(tmp_path, ["2"])
    drive(app, console)

    def broken(*_args):
        raise ValueError("boom")

    monkeypatch.setattr(controller, "handle_key", broken)
    console.feed("down")
    text = drive(app, console)
    assert app.state.tab == 1
    assert "Something went wrong" in text


# --------------------------------------------------------------------------- #
# Regressions found in review
# --------------------------------------------------------------------------- #


def test_ctrl_c_in_a_diagnostic_is_left_to_python(monkeypatch):
    import ctypes
    import types

    from mercy_ai.cli import console as console_module

    api = types.SimpleNamespace(
        PHANDLER_ROUTINE=ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32),
        SetConsoleCtrlHandler=lambda _cb, _add: 1,
    )
    monkeypatch.setattr(console_module, "_api", lambda: api)
    plain = console_module.WindowsConsole()
    plain.full_screen = False
    plain._install_ctrl_handler()
    assert plain._ctrl_cb(console_module.CTRL_C_EVENT) == 0  # Python raises KeyboardInterrupt
    full = console_module.WindowsConsole()
    full._install_ctrl_handler()
    assert full._ctrl_cb(console_module.CTRL_C_EVENT) == 1
    assert full.quit_requested.is_set()


def test_a_held_opening_key_is_not_the_answer():
    class Event:
        def __init__(self, scan, kind):
            self.scan_code, self.event_type, self.name, self.is_keypad = scan, kind, "", False

    capture = KeyCapture(hook=lambda _cb, **_kw: "handle", unhook=lambda _h: None)
    capture.start(held={0x1C})
    assert capture.on_event(Event(0x1C, "down")) is True  # auto-repeat of the opening Enter
    assert capture.poll() is None
    assert capture.on_event(Event(0x1C, "up")) is True
    assert capture.on_event(Event(0x1C, "down")) is False  # a fresh Enter press counts
    capture.on_event(Event(0x1C, "up"))
    assert capture.poll() == "enter"


def test_a_capture_nobody_answers_stops_swallowing_keys():
    clock = Clock()

    class Event:
        scan_code, event_type, name, is_keypad = 0x10, "down", "q", False

    capture = KeyCapture(
        hook=lambda _cb, **_kw: "h", unhook=lambda _h: None, timeout=1.0, clock=clock
    )
    capture.start()
    clock.now += 5
    assert capture.on_event(Event()) is True


def test_a_drawing_failure_during_capture_removes_the_hook(tmp_path, monkeypatch):
    app, console, _path = make_app(tmp_path, ["2", "down", "enter", None, None])
    drive(app, console, ticks_after=0)
    assert app.capture is not None and app.capture.active
    from mercy_ai.cli import app as app_module

    real = app_module.render
    calls = []

    def broken(*args):
        if not calls:
            calls.append(1)
            raise ValueError("boom")
        return real(*args)

    monkeypatch.setattr(app_module, "render", broken)
    console.feed(None, None)
    drive(app, console)
    assert app.capture is None
    assert app.data.status["running"]


def test_two_quick_captures_leave_one_hook_and_restart_the_loop(tmp_path):
    class LateWorker(SyncWorker):
        """Holds jobs until released, like a worker busy with a restart."""

        def __init__(self):
            super().__init__()
            self.held = []

        def submit(self, effect, job):
            self.held.append((effect, job))

        def release(self):
            while self.held:
                effect, job = self.held.pop(0)
                self.results.put((effect, job()))

    app, console, path = make_app(tmp_path)
    worker = LateWorker()
    app.worker = worker
    drive(app, console, ticks_after=0)
    worker.release()
    assert app.bridge.get_status()["running"]
    console.feed("2", "down", "enter", "esc", "enter")
    drive(app, console, ticks_after=0)
    made = []
    real_factory = app.capture_factory

    def counting(**kwargs):
        made.append(real_factory(**kwargs))
        return made[-1]

    app.capture_factory = counting
    worker.release()
    app._step()
    assert len(made) == 1
    FakeCapture.answers = ["k"]
    for _ in range(3):
        app._step()
        worker.release()
    for _ in range(3):
        app._step()
        worker.release()
    assert read_config(path).heal_btn == "k"
    assert app.bridge.get_status()["running"]


def test_reset_undoes_a_stepped_value_that_was_not_saved_yet(tmp_path):
    app, console, path = make_app(tmp_path, ["2", "left", "r"])
    drive(app, console, ticks_after=0)
    for _ in range(20):
        app._step()
    assert read_config(path).threshold_percent == 10.0
    assert "Back to the default" in console.text()


def test_reset_says_nothing_for_settings_without_a_default(tmp_path):
    app, console, _path = make_app(tmp_path, ["2", "down", "r"])
    text = drive(app, console)
    assert "Already the default" not in text


def test_stepping_from_between_notches_does_not_skip_one(tmp_path):
    config = Config(resolution="1440", **KEYS, threshold_percent=5.25)
    app, console, path = make_app(tmp_path, ["2", "right"], config=config)
    drive(app, console, ticks_after=0)
    for _ in range(20):
        app._step()
    assert read_config(path).threshold_percent == 5.5


def test_an_empty_dial_says_it_is_empty(tmp_path):
    app, console, _path = make_app(tmp_path, ["2", "enter", "enter"])
    drive(app, console)
    assert "this box is empty or not a number" in app.state.inline["threshold_percent"]


def test_a_successful_save_clears_a_refused_start(tmp_path):
    app, console, _path = make_app(tmp_path)
    drive(app, console)
    app.state.status_problems = ["The templates for 1440p could not be loaded."]
    console.feed("2", "down", "down", "down", "enter")
    drive(app, console)
    assert app.state.status_problems == []


def test_esc_hides_a_refusal_in_setup_before_going_back(tmp_path):
    app, console, _path = make_app(tmp_path, setup=True)
    drive(app, console)
    app.state.setup.step = 2
    controller._refusal(app.state, ["Something was refused."])
    console.feed("esc")
    drive(app, console)
    assert app.state.message is None
    assert app.state.setup.step == 2
