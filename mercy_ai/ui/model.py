# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Pure translation between :class:`Config` and the window's JSON.

Everything a settings screen has to do besides drawing itself lives here:
convert a ``Config`` to a dictionary and back, say what is wrong with a set of
bindings or a typed value, and turn a browser key event into a name the
``keyboard`` library understands.

It is pure by design.  A settings screen that silently writes a bad
``config.xml`` is worse than no settings screen at all, and the only way to be
sure it does not is to test the conversion without a window in the way.

One table -- :data:`FIELD_TYPES` -- drives both directions of the conversion,
so there is no hand-written reader and writer to keep in step.
"""

from __future__ import annotations

import math
import re
from dataclasses import MISSING, fields, replace
from typing import Any

from ..config import Config
from ..config_schema import COMMENTS, MOUSE_TRIGGERS
from ..errors import ConfigError
from ..paths import RESOLUTION_SIZES, RESOLUTIONS

__all__ = [
    "CAPTIONS",
    "FIELD_TAGS",
    "FIELD_TYPES",
    "UI_RANGES",
    "config_to_ui",
    "describe_settings",
    "normalise_key",
    "range_problems",
    "setting_defaults",
    "settings_problems",
    "typed_problems",
    "ui_to_config",
]

#: Every :class:`Config` field the window shows, and how to coerce it.
#:
#: This is also the list of fields the window is *allowed to write*: anything
#: else in a payload is ignored. Everything not listed -- the grenade
#: confidence, the loop rate and switch cooldown, capture flags, Flash Heal's
#: measured filters -- lives in ``config.xml`` only. The bridge re-reads the file
#: before every save, so a hand edit made while the window is open is kept.
#:
#: ``resolution``, ``heal_btn`` and ``damage_boost_btn`` are here too: the
#: window can change them after first run, which is the whole point of having a
#: Settings tab.
FIELD_TYPES: dict[str, type] = {
    "resolution": str,
    "heal_btn": str,
    "damage_boost_btn": str,
    "keybind_btn": str,
    "pause_toggle_button": str,
    "threshold_percent": float,
    "focus_gate": bool,
    "click_check": bool,
    "bomb_detection": bool,
    "flash_heal_btn": str,
    "flash_heal_trigger": str,
    "flash_heal_on_health_drop": bool,
    "flash_heal_health_mode": str,
    "flash_heal_low_health": float,
    "flash_heal_drop": float,
    "flash_heal_staff_check": bool,
    "dynamic_heal": bool,
    "dynamic_heal_drop": float,
    "theme": str,
}

#: :class:`Config` field -> the XML tag it is stored under.
#:
#: Most are identical; these are the ones the dataclass names differently from
#: the file. The mapping lets a setting with no caption of
#: its own fall back to the explanation written into ``config.xml``.
FIELD_TAGS: dict[str, str] = {
    **{name: name for name in FIELD_TYPES},
    "threshold_percent": "mercy_bar_percentage",
    "focus_gate": "disable_mercyAI_while_overwatch_unfocused",
    "bomb_detection": "enable_ana_bomb_detection",
    "click_check": "check_click_target",
}

#: The numbers a player can type in the window: ``(lowest, highest, name)``.
#:
#: Tighter than what ``config.xml`` accepts on purpose. These are the values
#: that make sense in play, and a value outside them is refused with a sentence
#: saying what to type instead -- rather than written to the file and clamped
#: somewhere later, which is how "I typed 5 and nothing happened" comes about.
#: Only fields the player actually typed are checked, so a value set by hand in
#: ``config.xml`` never blocks saving something else.
UI_RANGES: dict[str, tuple[float, float, str]] = {
    "dynamic_heal_drop": (0.05, 0.5, "Resume healing below the switch point by"),
    # Capped well below 1.0: a fall has to end above flash_heal_blank_floor
    # (0.02) to count, and the match never goes above 1.0, so anything near 1.0
    # could never fire. At 0.85 the live example (0.900 down to 0.050) still can.
    "flash_heal_drop": (0.1, 0.85, "Fall that counts as an emergency"),
    "flash_heal_low_health": (0.1, 0.8, "Fire when health is below"),
}


#: Sentences that only make sense next to an XML tag.
#:
#: The comments in ``config.xml`` have to explain that a boolean is written as
#: ``1`` or ``0``, because in that file it is. Beside a toggle switch the same
#: sentence is noise at best and confusing at worst -- there is no ``1`` to type.
#:
#: The look-behind keeps it off the tail of a decimal: without it ``0.0 = off``
#: matched from its second ``0`` and left a stray "0." in the caption.
_FILE_ONLY = re.compile(r"\s*(?<![\d.])(?:1 = on|0 = off|1 = |0 = )[^.]*\.", re.IGNORECASE)

#: What each setting does, in words a player can act on.
#:
#: Every number says what scale it is on, which way to move it, the range to
#: stay inside and the default -- so nobody has to guess a value. The scale is
#: the "Ally health match" bar on the Status tab: 0 is no ring at all, 1.0 a
#: full one. The window adds a live line under some of these that works the
#: setting out against the player's own switch point.
CAPTIONS: dict[str, str] = {
    "threshold_percent": "10 switches to damage boost only when your ally's health match is "
    "full (0.99). Each step lower switches about 0.09 earlier on the Ally health match bar on "
    "Status: 9 is about 0.90, 5 about 0.55, 1 is 0.20. The match number under the slider is the "
    "exact switch point. Low numbers boost even when your ally is badly hurt.",
    "dynamic_heal": "Stays on damage boost through chip damage instead of flicking back to heal.",
    "dynamic_heal_drop": "How far your ally's health match must fall below the switch point "
    "before Mercy AI goes back to healing, on the same 0 to 1 scale as the health bar on "
    "Status. Smaller heals sooner, larger stays on damage boost longer. "
    "Type 0.05 to 0.50. Default 0.15.",
    "bomb_detection": "Switches to damage boost while your ally has Ana's anti-heal on them.",
    "focus_gate": "Stops pressing keys while you are tabbed out of Overwatch.",
    "click_check": "Turn off only if holding the trigger in game does nothing while an "
    "overlay sits over the game.",
    "flash_heal_trigger": "When Ana's anti-heal grenade on your ally wears off, casts Flash "
    "Heal if they are below half health. Above that the beam tops them up on its own.",
    "flash_heal_on_health_drop": "Casts Flash Heal when your ally's health falls fast or "
    "drops below a line you set, Ana or not.",
    "flash_heal_health_mode": "Emergency fires when your ally's health falls fast. Normal "
    "fires whenever it is below a line you set, however it got there: once per dip, then again "
    "after they climb back above it. Both wait a moment after you start healing, so the beam "
    "connecting is never mistaken for low health.",
    "flash_heal_low_health": "Flash Heal fires when your ally's health match is below this, "
    "on the same 0 to 1 scale as the health bar on Status: 0.4 is a little under half. "
    "Higher fires sooner, lower only when they are nearly dead. Type 0.10 to 0.80. Default 0.4.",
    "flash_heal_staff_check": "Casts Flash Heal only while you hold the staff. With the "
    "blaster its key reloads, and on another hero it is that hero's key. Turn off only if "
    "Flash Heal never fires with the staff out.",
    "flash_heal_drop": "How much your ally's health match must fall within 0.3 seconds to "
    "count as an emergency, on the same 0 to 1 scale as the health bar on Status: 0.5 is "
    "half of it. Lower fires on smaller hits, higher only on big ones. "
    "Type 0.10 to 0.85. Default 0.5.",
}


def _for_humans(comment: str) -> str:
    """Collapse a config.xml comment into one line fit for a control's caption."""
    text = " ".join(comment.split())
    text = _FILE_ONLY.sub("", text)
    return " ".join(text.split()).strip()


def describe_settings() -> dict[str, str]:
    """The explanation for each setting the window shows.

    A caption from :data:`CAPTIONS` where there is one, otherwise the comment
    written into config.xml.
    """
    described = {
        field: _for_humans(COMMENTS[tag]) for field, tag in FIELD_TAGS.items() if tag in COMMENTS
    }
    described.update(CAPTIONS)
    return described


def _plain(value: float) -> str:
    """0.05 rather than 0.05000000000000001, and 1 rather than 1.0."""
    return f"{value:g}"


def _how_to_type(field: str) -> str:
    low, high, _name = UI_RANGES[field]
    return f"Type a number from {_plain(low)} to {_plain(high)}, with a dot for decimals."


def typed_problems(payload: dict[str, Any]) -> list[str]:
    """Say which number boxes hold something that is not a number at all.

    A cleared box arrives as ``""``, and so does anything a number box cannot
    read (letters, ``0.2.``). Caught here so the player is told, in the box's own
    words, what to type -- not shown a Python error naming an internal field.
    """
    problems: list[str] = []
    for field, (_low, _high, name) in UI_RANGES.items():
        if field not in payload:
            continue
        raw = payload[field]
        try:
            float(str(raw).strip())
        except ValueError:
            problems.append(f"{name}: this box is empty or not a number. {_how_to_type(field)}")
    return problems


def range_problems(payload: dict[str, Any], candidate: Config) -> list[str]:
    """Say which typed numbers are outside :data:`UI_RANGES`, and what to type.

    Only the fields in *payload* -- the ones the player just typed -- are
    checked. ``NaN`` fails every comparison, so it is refused too.
    """
    problems: list[str] = []
    for field, (low, high, name) in UI_RANGES.items():
        if field not in payload:
            continue
        value = float(getattr(candidate, field))
        if not (math.isfinite(value) and low <= value <= high):
            problems.append(f"{name}: {_plain(value)} is outside the range. {_how_to_type(field)}")
    return problems


def setting_defaults() -> dict[str, Any]:
    """What each setting the window shows goes back to with its Reset button.

    Read off :class:`Config` itself, so there is no second list to fall out of
    step (and ``test_back_filled_defaults_match_the_dataclass`` already holds the
    dataclass to the values config.xml is back-filled with). A field with no
    default -- the heal and damage boost keys, the resolution -- is one only the
    player can choose, so it has no Reset button at all.
    """
    return {
        field.name: field.default
        for field in fields(Config)
        if field.name in FIELD_TYPES and field.default is not MISSING
    }


def config_to_ui(config: Config) -> dict[str, Any]:
    """Flatten *config* into the dictionary the page renders from.

    The derived values -- ``threshold``, ``trigger_label``,
    ``flash_heal_enabled`` -- are computed here rather than in JavaScript so
    there is exactly one implementation of each.
    """
    values: dict[str, Any] = {key: getattr(config, key) for key in FIELD_TYPES}
    values.update(
        threshold=config.threshold,
        trigger_label=config.trigger_label,
        uses_mouse_trigger=config.uses_mouse_trigger,
        flash_heal_enabled=config.flash_heal_enabled,
        resolutions=list(RESOLUTIONS),
        resolution_sizes=dict(RESOLUTION_SIZES),
        descriptions=describe_settings(),
        ranges={field: [low, high] for field, (low, high, _name) in UI_RANGES.items()},
        defaults=setting_defaults(),
    )
    return values


def _coerce(name: str, kind: type, raw: Any) -> Any:
    """Turn one JSON value into the type the dataclass field wants.

    JavaScript has one number type and its checkboxes arrive as ``true``/
    ``false``, but a range input arrives as the *string* ``"7.5"``.  Being strict
    about it here is what stops ``"7.5"`` reaching ``config_to_xml`` and being
    written out as a quoted string that the next read rejects.
    """
    if kind is bool:
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes")
        return bool(raw)
    if kind is str:
        return str(raw).strip()
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} should be a number but is {raw!r}.") from exc
    return int(number) if kind is int else number


def ui_to_config(base: Config, payload: dict[str, Any]) -> Config:
    """Apply the page's dictionary onto *base*, coercing every value.

    Keys the page does not send keep their current value, and keys the page
    invents are ignored -- so an older window and a newer config, or the reverse,
    degrade to "nothing changed" instead of to an exception.
    """
    changes = {
        key: _coerce(key, kind, payload[key]) for key, kind in FIELD_TYPES.items() if key in payload
    }
    return replace(base, **changes)


#: What each key setting is called in a sentence.
_KEY_LABELS: dict[str, str] = {
    "heal_btn": "heal key",
    "damage_boost_btn": "damage boost key",
    "keybind_btn": "trigger",
    "pause_toggle_button": "pause key",
    "flash_heal_btn": "Flash Heal key",
}

#: Turning Flash Heal on is, for clash purposes, choosing its key.
_FLASH_SWITCHES = frozenset({"flash_heal_trigger", "flash_heal_on_health_drop"})


def _key_name(key: str) -> str:
    """A key as a player reads it: "F2", "R", "Mouse Left click", "page up"."""
    if key in MOUSE_TRIGGERS:
        return MOUSE_TRIGGERS[key]
    return key.upper() if len(key) <= 3 else key


def settings_problems(
    config: Config, changed: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """List what would stop *config* working, in words a player can act on.

    Every key collision that would stop the loop working, including
    the Flash Heal key, worded for the player rather than in internal setting
    names and "delete config.xml".

    *changed* is the set of fields the player just set. A clash is worded from
    that side ("F2 is already your pause key. Pick a different heal key."), so
    the message never tells them to change a key that was fine. The Flash Heal
    key only counts while Flash Heal is on: when it is off nothing presses it.
    """
    problems: list[str] = []
    if not config.heal_btn:
        problems.append("Set a heal key.")
    if not config.damage_boost_btn:
        problems.append("Set a damage boost key.")
    if config.resolution not in RESOLUTIONS:
        problems.append(f"Resolution must be one of {', '.join(RESOLUTIONS)}.")

    bindings = ["heal_btn", "damage_boost_btn", "keybind_btn", "pause_toggle_button"]
    if config.flash_heal_enabled:
        bindings.append("flash_heal_btn")
    chosen = set(changed)
    if chosen & _FLASH_SWITCHES:
        chosen.add("flash_heal_btn")

    for i, first in enumerate(bindings):
        for second in bindings[i + 1 :]:
            key = getattr(config, first)
            if not key or key != getattr(config, second):
                continue
            name = _key_name(key)
            if {first, second} == {"heal_btn", "damage_boost_btn"} and not (
                (first in chosen) ^ (second in chosen)
            ):
                problems.append(f"Heal and damage boost are both {name}. Pick two different keys.")
            elif (first in chosen) != (second in chosen):
                new, old = (first, second) if first in chosen else (second, first)
                if new == "flash_heal_btn" and new not in changed:
                    # Turned on by its switch: the key itself lives elsewhere.
                    problems.append(
                        f"{name} is already your {_KEY_LABELS[old]}. Pick a different Flash "
                        f"Heal key under Settings > Advanced > Controls, or change your "
                        f"{_KEY_LABELS[old]}."
                    )
                    continue
                problems.append(
                    f"{name} is already your {_KEY_LABELS[old]}. "
                    f"Pick a different {_KEY_LABELS[new]}."
                )
            else:
                problems.append(
                    f"{name} can't be both your {_KEY_LABELS[first]} and your "
                    f"{_KEY_LABELS[second]}. Change one of them under Settings."
                )

    if config.flash_heal_trigger != "off" and not config.flash_heal_btn:
        problems.append("Flash Heal has a trigger but no key, so it can never fire.")
    return problems


# --------------------------------------------------------------------------- #
# Key capture
# --------------------------------------------------------------------------- #

#: ``KeyboardEvent.code`` -> the name the ``keyboard`` library binds.
#:
#: Keyed on ``code`` rather than ``key`` because ``key`` is layout-dependent:
#: on an Arabic or French layout the physical ``KeyP`` reports something else
#: entirely, and the user is binding a physical key in Overwatch.
_CODES: dict[str, str] = {
    "Space": "space",
    "Tab": "tab",
    "Escape": "esc",
    "Enter": "enter",
    "NumpadEnter": "enter",
    "Backspace": "backspace",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "PageUp": "page up",
    "PageDown": "page down",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "ShiftLeft": "shift",
    "ShiftRight": "shift",
    "ControlLeft": "ctrl",
    "ControlRight": "ctrl",
    "AltLeft": "alt",
    "AltRight": "alt",
    "CapsLock": "caps lock",
    "Minus": "-",
    "Equal": "=",
    "BracketLeft": "[",
    "BracketRight": "]",
    "Backslash": "\\",
    "Semicolon": ";",
    "Quote": "'",
    "Comma": ",",
    "Period": ".",
    "Slash": "/",
    "Backquote": "`",
}

#: Codes that must not become a binding, whatever the user presses.
#:
#: A dead key here is one Overwatch will never see or one the ``keyboard``
#: library cannot press, so accepting it would produce a config that looks fine
#: and does nothing.
_REJECTED = ("Meta", "OS", "ContextMenu", "Numpad")


def normalise_key(code: str, key: str = "") -> str | None:
    """Turn a browser ``keydown`` into a ``keyboard``-library name.

    Returns ``None`` when the key cannot be bound, so the caller can keep the
    capture open rather than storing something unusable.
    """
    code = (code or "").strip()
    if not code or code.startswith(_REJECTED):
        return None
    if code in _CODES:
        return _CODES[code]
    if code.startswith("Key") and len(code) == 4:
        return code[3].lower()
    if code.startswith("Digit") and len(code) == 6:
        return code[5]
    if code.startswith("F") and code[1:].isdigit() and 1 <= int(code[1:]) <= 24:
        return code.lower()
    # Last resort: a single printable character from a layout this table does
    # not cover.  Better than refusing a key that would have worked.
    printable = (key or "").strip()
    if len(printable) == 1 and printable.isprintable() and not printable.isspace():
        return printable.lower()
    return None
