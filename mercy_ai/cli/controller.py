# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""What every key does, and what happens when the bridge answers.

The functions here change a :class:`~mercy_ai.cli.state.UIState` in place and
return the :class:`~mercy_ai.cli.state.Effect` objects only the outside world
can carry out: saving a setting, starting or pausing the loop, opening a link,
capturing a game key, quitting. Nothing here touches the bridge, a thread or
the console, so every key sequence can be tested on its own.

Every save sends only the setting the player changed, exactly as the window
does: the bridge words a key clash from the side that changed, and a value set
by hand in config.xml never blocks saving something else.
"""

from __future__ import annotations

import math
from typing import Any

from .screens import (
    KEY_LABELS,
    MIN_COLS,
    MIN_ROWS,
    STEPS,
    TRIGGER_CHOICES,
    Row,
    focused_row,
    rows_for,
    view_name,
)
from .state import TABS, Data, Edit, Effect, Message, Modal, Setup, UIState
from .style import Style
from .words import UNBINDABLE, is_default, key_name, resolution_alert, trigger_label

__all__ = ["captured", "handle_key", "result", "start", "tick"]

#: How long a toast stays up, and how long after the last arrow press a
#: stepped number is saved (saving restarts the loop for some settings).
TOAST_SECONDS = 3.0
STEP_DELAY = 0.6

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫,", "0123456789..")

_LINKS = ("instagram", "github", "repo")


def _toast(state: UIState, text: str, tone: str = "good") -> None:
    state.message = Message(text, tone, state.now + TOAST_SECONDS)


def _refusal(state: UIState, problems: list[str] | None) -> None:
    text = " ".join(problems or []) or "That did not work, and Mercy AI was not told why."
    state.message = Message(text, "error", None)


def _save(payload: dict[str, Any], tag: Any) -> Effect:
    return Effect("save", payload, tag)


# --------------------------------------------------------------------------- #
# Starting, ticking and quitting
# --------------------------------------------------------------------------- #


def start(state: UIState, data: Data) -> list[Effect]:
    """The first screen: setup on a first run, otherwise Status with the loop started."""
    if data.env.get("needs_setup"):
        _enter_setup(state, data, redo=False)
        return []
    state.screen = "main"
    return [Effect("run", True, ("auto",))]


def _enter_setup(state: UIState, data: Data, *, redo: bool) -> None:
    config = data.config
    state.screen = "setup"
    state.setup = Setup(
        step=1,
        redo=redo,
        heal=config.get("heal_btn") or "",
        boost=config.get("damage_boost_btn") or "",
        resolution=data.env.get("detected") or (config.get("resolution") if redo else "") or "",
    )
    for step in (1, 2, 3):
        state.focus.pop(f"setup{step}", None)
        state.scroll.pop(f"setup{step}", None)


def flush_pending(state: UIState) -> list[Effect]:
    """Save every number stepped with the arrows but not saved yet."""
    effects = []
    for name, value in state.pending.items():
        state.inflight[name] = value
        effects.append(_save({name: value}, ("step", name)))
    state.pending.clear()
    return effects


def tick(state: UIState, data: Data) -> list[Effect]:
    """Called every frame: expire the toast, save stepped numbers that have settled."""
    message = state.message
    if message is not None and message.until is not None and state.now >= message.until:
        state.message = None
    if state.pending and state.now >= state.pending_at:
        return flush_pending(state)
    return []


def _quit(state: UIState) -> list[Effect]:
    effects = flush_pending(state)
    state.quit = True
    state.modal = None
    return [*effects, Effect("quit")]


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def handle_key(state: UIState, data: Data, key: str, style: Style) -> list[Effect]:
    if key in ("ctrl+c", "interrupt"):
        return _quit(state)
    if key == "resize":
        return []
    if state.cols < MIN_COLS or state.rows < MIN_ROWS:
        return _quit(state) if key == "q" else []
    if state.modal is not None:
        return _modal_key(state, data, key, style)
    if state.edit is not None:
        return _edit_key(state, data, key)
    if state.screen == "setup":
        return _setup_key(state, data, key, style)
    return _main_key(state, data, key, style)


def _set_focus(state: UIState, row: Row) -> list[Effect]:
    view = view_name(state)
    if state.focus.get(view) == row.id:
        return []
    state.focus[view] = row.id
    return flush_pending(state)


def _move(state: UIState, data: Data, style: Style, key: str) -> list[Effect]:
    rows = [row for row in rows_for(state, data, style) if row.focusable]
    if not rows:
        return []
    current = focused_row(state, data, style)
    index = rows.index(current) if current in rows else 0
    target = {
        "up": index - 1,
        "down": index + 1,
        "shift+tab": index - 1,
        "tab": index + 1,
        "home": 0,
        "end": len(rows) - 1,
        "pgup": index - 5,
        "pgdn": index + 5,
    }[key]
    return _set_focus(state, rows[max(0, min(len(rows) - 1, target))])


def _switch_tab(state: UIState, tab: int) -> list[Effect]:
    effects = flush_pending(state)
    state.tab = tab % len(TABS)
    if state.message is not None and state.message.until is None:
        state.message = None
    return effects


def _main_key(state: UIState, data: Data, key: str, style: Style) -> list[Effect]:
    config, status = data.config, data.status
    running = bool(status.get("running"))
    view = view_name(state)
    row = focused_row(state, data, style)

    # The pause hook already acted on this key; acting on it here too would
    # undo it.
    pause_key = str(config.get("pause_toggle_button") or "")
    if running and key == pause_key:
        return []

    if key in ("1", "2", "3", "4"):
        return _switch_tab(state, int(key) - 1)
    if key == "f1":
        return _switch_tab(state, 2)
    if key == "tab":
        return _switch_tab(state, state.tab + 1)
    if key == "shift+tab":
        return _switch_tab(state, state.tab - 1)
    if key == "q":
        state.modal = Modal("quit")
        return []
    if key == "p":
        return _pause(state, data)
    if key == "s":
        return _run(state, data, not running)
    if key == "u":
        _alert, fix = resolution_alert(config, data.env)
        return [_save({"resolution": fix}, ("toast", f"Resolution set to {fix}p."))] if fix else []
    if key == "o":
        return [Effect("folder", tag=("folder",))]
    if key == "r":
        return _reset(state, data, row)

    if key in ("up", "down", "home", "end", "pgup", "pgdn"):
        if view == "help":
            page = max(1, state.rows - 8)
            delta = {
                "up": -1,
                "down": 1,
                "pgup": -page,
                "pgdn": page,
                "home": -(10**6),
                "end": 10**6,
            }
            state.scroll[view] = max(0, state.scroll.get(view, 0) + delta[key])
            return []
        return _move(state, data, style, key)

    if key in ("left", "right"):
        direction = 1 if key == "right" else -1
        if (
            row is not None
            and row.kind in ("toggle", "choice", "number", "dial")
            and view != "help"
        ):
            return _change(state, data, row, direction)
        return _switch_tab(state, state.tab + direction)

    if key in ("enter", "space", " "):
        return _act(state, data, row) if row is not None else []

    if key in ("esc", "backspace"):
        if state.message is not None and state.message.until is None:
            state.message = None
            return []
        if state.advanced and state.tab == 1:
            state.advanced = False
            return flush_pending(state)
        if state.tab != 0:
            return _switch_tab(state, 0)
        if key == "esc":
            state.modal = Modal("quit")
        return []
    return []


def _reset(state: UIState, data: Data, row: Row | None) -> list[Effect]:
    """Put the focused setting back to its default, if it has one."""
    if row is None or row.kind not in ("number", "dial", "key"):
        # Only numbers and keys have a Reset; a switch is put back by
        # pressing it again.
        return []
    defaults = data.config.get("defaults") or {}
    stepped = bool(row.field) and state.pending.pop(row.field, None) is not None
    payload = row.reset
    if payload is None and row.field in defaults and not is_default(row.field, data.config):
        payload = {row.field: defaults[row.field]}  # stepped back to the default, not saved
    if payload is not None:
        return [_save(dict(payload), ("toast", f"Back to the default: {row.reset_words}."))]
    if stepped:
        _toast(state, f"Back to the default: {row.reset_words}.")
    elif row.reset_words:
        _toast(state, "Already the default.", "warn")
    return []


def _pause(state: UIState, data: Data) -> list[Effect]:
    if not data.status.get("running"):
        _toast(state, "Mercy AI is stopped. Press S to start.", "warn")
        return []
    return [Effect("pause", tag=("pause",))]


def _run(state: UIState, data: Data, wanted: bool) -> list[Effect]:
    return [Effect("run", wanted, ("run", wanted))]


def _act(state: UIState, data: Data, row: Row) -> list[Effect]:
    config = data.config
    if row.kind == "toggle":
        return _change(state, data, row, 1)
    if row.kind == "choice":
        values = [value for value, _text in row.options]
        if not values:
            return []
        index = values.index(row.value) if row.value in values else -1
        return _choose(row, values[(index + 1) % len(values)])
    if row.kind in ("number", "dial"):
        state.edit = Edit(row.field, "")
        state.inline.pop(row.field, None)
        return flush_pending(state)
    if row.kind == "key":
        if row.field == "keybind_btn":
            current = config.get("keybind_btn")
            values = [value for value, _ in TRIGGER_CHOICES]
            state.modal = Modal(
                "trigger",
                field="keybind_btn",
                focus=values.index(current) if current in values else 2,
            )
            return []
        state.modal = Modal("capture", field=row.field)
        return [Effect("capture", row.field)]
    if row.id == "pause":
        return _pause(state, data)
    if row.id == "run":
        return _run(state, data, not data.status.get("running"))
    if row.id == "use_res":
        _alert, fix = resolution_alert(config, data.env)
        return [_save({"resolution": fix}, ("toast", f"Resolution set to {fix}p."))] if fix else []
    if row.id == "advanced":
        state.advanced = True
        return []
    if row.id == "open_folder":
        return [Effect("folder", tag=("folder",))]
    if row.id == "redo_setup":
        effects = flush_pending(state)
        _enter_setup(state, data, redo=True)
        return effects
    if row.id in _LINKS:
        return [Effect("link", row.id, ("link",))]
    return []


def _choose(row: Row, value: Any) -> list[Effect]:
    if value == row.value:
        return []
    text = f"Resolution set to {value}p." if row.field == "resolution" else "Saved."
    return [_save({row.field: value}, ("toast", text))]


def _change(state: UIState, data: Data, row: Row, direction: int) -> list[Effect]:
    if row.kind == "toggle":
        if row.id == "flash_enabled":
            payload = (
                {"flash_heal_trigger": "off", "flash_heal_on_health_drop": False}
                if row.value
                else {"flash_heal_trigger": "expiry", "flash_heal_on_health_drop": True}
            )
            text = "Flash Heal is off." if row.value else "Flash Heal is on."
            return [_save(payload, ("toast", text))]
        if row.id == "flash_ana":
            payload = {"flash_heal_trigger": "off" if row.value else "expiry"}
            return [_save(payload, ("toast", f"{row.label} is {'off' if row.value else 'on'}."))]
        text = f"{row.label} is {'off' if row.value else 'on'}."
        return [_save({row.field: not row.value}, ("toast", text))]
    if row.kind == "choice":
        values = [value for value, _text in row.options]
        index = (
            values.index(row.value)
            if row.value in values
            else (-1 if direction > 0 else len(values))
        )
        target = index + direction
        if not 0 <= target < len(values):
            return []
        return _choose(row, values[target])
    # number or dial: step now, save once the arrows settle
    step = STEPS.get(row.field, 0.05)
    low, high = (data.config.get("ranges") or {}).get(row.field, (1.0, 10.0))
    try:
        current = float(row.value)
    except (TypeError, ValueError):
        current = float(low)
    # Snap to the next notch in the direction of travel, so a typed value
    # between notches never skips one.
    notch = current / step
    target = math.floor(notch + 1e-9) + 1 if direction > 0 else math.ceil(notch - 1e-9) - 1
    value = round(target * step, 4)
    value = min(float(high), max(float(low), value))
    if math.isclose(value, current):
        return []
    state.pending[row.field] = value
    state.pending_at = state.now + STEP_DELAY
    state.inline.pop(row.field, None)
    return []


def _edit_key(state: UIState, data: Data, key: str) -> list[Effect]:
    edit = state.edit
    assert edit is not None
    if key == "esc":
        state.inline.pop(edit.field, None)
        state.edit = None
        return []
    if key == "backspace":
        edit.text = edit.text[:-1]
        return []
    if key == "enter":
        return _submit(state, edit)
    char = key.translate(_ARABIC_DIGITS) if len(key) == 1 else ""
    if char and (char.isdigit() or char == ".") and len(edit.text) < 6:
        edit.text += char
    return []


def _submit(state: UIState, edit: Edit) -> list[Effect]:
    if edit.field == "threshold_percent":
        # The window's dial cannot leave 1 to 10, so the bridge does not check
        # it; a typed one is checked here, in the same words.
        try:
            number = float(edit.text)
        except ValueError:
            number = math.nan
        how = "Type a number from 1 to 10, with a dot for decimals."
        if not math.isfinite(number):
            state.inline[edit.field] = (
                f"Switch to damage boost at: this box is empty or not a number. {how}"
            )
            return []
        if not 1.0 <= number <= 10.0:
            state.inline[edit.field] = (
                f"Switch to damage boost at: {edit.text} is outside the range. {how}"
            )
            return []
    return [_save({edit.field: edit.text}, ("edit", edit.field))]


def _modal_key(state: UIState, data: Data, key: str, style: Style) -> list[Effect]:
    modal = state.modal
    assert modal is not None
    if modal.kind == "quit":
        if key in ("left", "right", "tab", "shift+tab"):
            modal.focus = 1 - modal.focus
        elif key == "y" or (key in ("enter", "space", " ") and modal.focus == 0):
            return _quit(state)
        elif key in ("n", "esc", "backspace") or key in ("enter", "space", " "):
            state.modal = None
        return []
    if modal.kind == "trigger":
        if key in ("up", "down"):
            modal.focus = max(0, min(2, modal.focus + (1 if key == "down" else -1)))
        elif key in ("enter", "space", " "):
            if modal.focus < 2:
                value = TRIGGER_CHOICES[modal.focus][0]
                if value == data.config.get("keybind_btn"):
                    state.modal = None
                    return []
                modal.saving = True
                return [_save({"keybind_btn": value}, ("trigger", value))]
            state.modal = Modal("capture", field="keybind_btn")
            return [Effect("capture", "keybind_btn")]
        elif key in ("esc", "backspace"):
            state.modal = None
        return []
    # capture: the key itself arrives through the capture hook, not here
    if key == "esc" and not modal.saving:
        state.modal = None
        return [Effect("uncapture")]
    return []


def _setup_key(state: UIState, data: Data, key: str, style: Style) -> list[Effect]:
    setup = state.setup
    row = focused_row(state, data, style)
    if key in ("up", "down", "home", "end", "tab", "shift+tab", "pgup", "pgdn"):
        effects = _move(state, data, style, key)
        moved = focused_row(state, data, style)
        if setup.step == 3 and moved is not None and moved.kind == "radio":
            setup.resolution = moved.value
        return effects
    if key == "q":
        state.modal = Modal("quit")
        return []
    if key in ("esc", "backspace"):
        if state.message is not None and state.message.until is None:
            state.message = None
            return []
        if setup.step > 1:
            setup.step -= 1
            return []
        if setup.redo:
            state.screen = "main"
            return []
        if key == "esc":
            state.modal = Modal("quit")
        return []
    if key not in ("enter", "space", " ") or row is None:
        return []
    if row.id == "ack":
        setup.ack = not setup.ack
        if setup.ack:
            state.focus["setup1"] = "next"
        return []
    if row.id in ("wiz_heal", "wiz_boost"):
        setup.problem = ""
        state.modal = Modal("capture", field=row.id, in_setup=True)
        return [Effect("capture", row.id)]
    if row.kind == "radio":
        # The radio follows the arrows, so Enter here means "this one, done".
        setup.resolution = row.value
        return _finish_setup(setup)
    if row.id == "back":
        setup.step = max(1, setup.step - 1)
        return []
    if row.id == "next":
        if setup.step < 3:
            setup.step += 1
            if setup.step == 3 and setup.resolution:
                state.focus["setup3"] = f"res{setup.resolution}"
            return []
        return _finish_setup(setup)
    return []


def _finish_setup(setup: Setup) -> list[Effect]:
    """One save for all three answers, so an abandoned setup writes nothing."""
    payload = {
        "heal_btn": setup.heal,
        "damage_boost_btn": setup.boost,
        "resolution": setup.resolution,
    }
    return [_save(payload, ("setup",))]


# --------------------------------------------------------------------------- #
# Key capture and answers from the bridge
# --------------------------------------------------------------------------- #


def captured(state: UIState, data: Data, outcome: str) -> list[Effect]:
    """The capture hook's answer: a key name, ``"cancel"`` or ``"refused"``."""
    modal = state.modal
    if modal is None or modal.kind != "capture" or modal.saving:
        return []
    if outcome == "cancel":
        state.modal = None
        return [Effect("uncapture")]
    if outcome == "refused":
        modal.problem = UNBINDABLE
        return [Effect("rearm")]
    if modal.in_setup:
        if modal.field == "wiz_heal":
            state.setup.heal = outcome
            state.focus["setup2"] = "wiz_boost" if not state.setup.boost else "next"
        else:
            state.setup.boost = outcome
            state.focus["setup2"] = "next" if state.setup.heal else "wiz_heal"
        state.modal = None
        return [Effect("uncapture")]
    modal.saving = True
    modal.problem = ""
    return [_save({modal.field: outcome}, ("capture", modal.field, outcome))]


def result(state: UIState, data: Data, effect: Effect, response: dict[str, Any]) -> list[Effect]:
    """The bridge answered *effect* with *response*."""
    state.busy = max(0, state.busy - 1)
    if isinstance(response.get("config"), dict):
        data.config = response["config"]
    ok = bool(response.get("ok"))
    if ok and effect.kind == "save":
        # A successful save is what a refused Start asked for (another
        # resolution, a key that works), so its old complaint goes, as in the
        # window.
        state.status_problems = []
    problems = list(response.get("problems") or [])
    tag: tuple[Any, ...] = effect.tag or ("quiet",)
    kind = tag[0]

    if kind == "capture":
        _kind, name, key = tag
        if state.modal is None or state.modal.kind != "capture":
            return [Effect("uncapture")]
        if ok:
            state.modal = None
            _toast(state, f"{KEY_LABELS.get(name, 'Key')} set to {key_name(key)}.")
            return [Effect("uncapture")]
        state.modal.saving = False
        state.modal.problem = " ".join(problems)
        return [Effect("rearm")]
    if kind == "trigger":
        if ok:
            state.modal = None
            _toast(state, f"Trigger set to {trigger_label(tag[1])}.")
        elif state.modal is not None:
            state.modal.saving = False
            state.modal.problem = " ".join(problems)
        return []
    if kind == "edit":
        field = tag[1]
        if ok:
            state.edit = None
            state.inline.pop(field, None)
            _toast(state, "Saved.")
        else:
            state.inline[field] = " ".join(problems)
        return []
    if kind == "step":
        field = tag[1]
        state.inflight.pop(field, None)
        if ok:
            state.inline.pop(field, None)
        else:
            state.inline[field] = " ".join(problems)
        return []
    if kind in ("toast", "reset"):
        if ok:
            _toast(state, tag[1])
        else:
            _refusal(state, problems)
        return []
    if kind == "setup":
        if not ok:
            _refusal(state, problems)
            return []
        state.screen = "main"
        state.tab = 0
        state.advanced = False
        _toast(state, "Setup saved. Mercy AI is watching the screen.")
        return [Effect("run", True, ("auto",))]
    if kind in ("auto", "run", "restore"):
        if ok:
            state.status_problems = []
            if kind == "run":
                _toast(state, "Started." if tag[1] else "Stopped. Press S to start again.")
        else:
            state.status_problems = problems
            if state.tab != 0 or state.screen != "main":
                _refusal(state, problems)
        return []
    if kind == "pause":
        if ok:
            pause = key_name(str(data.config.get("pause_toggle_button") or ""))
            if response.get("paused"):
                hint = "Press P to resume." if pause == "P" else f"Press P or {pause} to resume."
                _toast(state, f"Paused. {hint}")
            else:
                _toast(state, "Resumed.")
        return []
    if kind == "link":
        if ok:
            _toast(state, "Opened in your browser.")
        else:
            _refusal(state, problems)
        return []
    if kind == "folder":
        if ok:
            _toast(state, "Opened the Mercy AI folder.")
        else:
            _refusal(state, problems)
        return []
    if not ok and problems:
        _refusal(state, problems)
    return []
