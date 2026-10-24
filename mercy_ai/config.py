# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Reading, writing, validating and migrating ``config.xml``.

Settings travel as a frozen :class:`Config` dataclass, so every field is named
and typed rather than unpacked by position.  The tag names, defaults, comments
and limits live in :mod:`mercy_ai.config_schema`.
"""

from __future__ import annotations

import logging
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config_schema import COMMENTS, DEFAULTS, FIELD_ORDER, LIMITS, MOUSE_TRIGGERS
from .errors import ConfigError

log = logging.getLogger(__name__)

__all__ = [
    "CONFIG_VERSION",
    "DEFAULTS",
    "NCC_MAX",
    "NCC_MIN",
    "THRESHOLD_MAX",
    "THRESHOLD_MIN",
    "Config",
    "clamped",
    "config_to_xml",
    "migrate_config",
    "read_config",
    "threshold_translator",
    "wants_boost",
    "write_config_atomic",
]

#: Bumped whenever a key is added.  Purely informational; migration is by key.
CONFIG_VERSION = 5

THRESHOLD_MIN, THRESHOLD_MAX = 1.0, 10.0
NCC_MIN, NCC_MAX = 0.2, 0.99


@dataclass(frozen=True)
class Config:
    """The effective settings."""

    resolution: str
    heal_btn: str
    damage_boost_btn: str
    keybind_btn: str = "left_mouse"
    pause_toggle_button: str = "f2"
    #: The raw 1-10 dial as the user typed it; :attr:`threshold` is derived.
    threshold_percent: float = 10.0
    bomb_threshold: float = 0.74
    focus_gate: bool = True
    #: A mouse trigger only starts when the click lands on the game window.
    click_check: bool = True
    action_cooldown: float = 0.2
    target_fps: float = 0.0
    bomb_detection: bool = True
    hysteresis: float = 0.0
    fast_capture: bool = True

    # -- Flash Heal (off by default) ---------------------------------------- #
    flash_heal_btn: str = "r"
    flash_heal_trigger: str = "off"
    flash_heal_on_health_drop: bool = False
    #: ``emergency`` fires on a sharp fall, ``low`` below a fixed line.
    flash_heal_health_mode: str = "emergency"
    flash_heal_low_health: float = 0.4
    flash_heal_cooldown: float = 0.0
    flash_heal_min_heal_time: float = 0.5
    flash_heal_drop: float = 0.5
    flash_heal_settle: float = 0.4
    flash_heal_max_fall_rate: float = 60.0
    flash_heal_blank_floor: float = 0.02
    #: Press Flash Heal only while the staff is out: elsewhere its key reloads
    #: the blaster or is another hero's ability.
    flash_heal_staff_check: bool = True
    flash_heal_weapon_threshold: float = 0.6

    # -- Appearance and smooth healing ------------------------------------ #
    theme: str = "light"
    dynamic_heal: bool = False
    dynamic_heal_drop: float = 0.15

    @property
    def flash_heal_enabled(self) -> bool:
        """A key *and* a trigger are both required before anything can fire."""
        if not self.flash_heal_btn:
            return False
        return self.flash_heal_trigger == "expiry" or self.flash_heal_on_health_drop

    @property
    def threshold(self) -> float:
        """The 1-10 dial mapped into normalised-cross-correlation space."""
        return threshold_translator(self.threshold_percent)

    @property
    def uses_mouse_trigger(self) -> bool:
        return self.keybind_btn in MOUSE_TRIGGERS

    @property
    def trigger_label(self) -> str:
        return MOUSE_TRIGGERS.get(self.keybind_btn, self.keybind_btn)


def threshold_translator(value: float) -> float:
    """Map the user-facing 1-10 dial onto the NCC range, clamping out-of-range input.

    Normalised cross-correlation never exceeds 1.0, so an unclamped dial of, say,
    20 would map above 1 and damage boost could never trigger, with no hint why.
    """
    limited = min(max(float(value), THRESHOLD_MIN), THRESHOLD_MAX)
    if limited != float(value):
        log.warning(
            "mercy_bar_percentage=%g is outside %g..%g; using %g.",
            value,
            THRESHOLD_MIN,
            THRESHOLD_MAX,
            limited,
        )
    scale = (NCC_MAX - NCC_MIN) / (THRESHOLD_MAX - THRESHOLD_MIN)
    return NCC_MIN + (limited - THRESHOLD_MIN) * scale


def clamped(name: str, value: float, low: float, high: float) -> float:
    """Bound a numeric field and say so when it had to be changed."""
    if not low <= value <= high:
        limited = min(max(value, low), high)
        log.warning("%s=%g is outside %g..%g; using %g.", name, value, low, high, limited)
        return limited
    return value


def wants_boost(
    score: float,
    threshold: float,
    currently_boosting: bool,
    hysteresis: float,
    dynamic_drop: float = 0.0,
) -> bool:
    """Decide whether to damage boost.

    With ``hysteresis == 0.0`` and ``dynamic_drop == 0.0`` this is plain
    ``score >= threshold``.

    ``dynamic_drop`` is smooth healing. The plain comparison flips the beam the
    instant the ally crosses the line in either direction, so an ally sitting
    near it under chip damage makes the beam flick back and forth. Smooth healing
    keeps boosting until they have fallen ``dynamic_drop`` below the line, then
    heals them all the way back **up to** it -- so the switch point stays exactly
    where the dial says, and only the point at which healing *resumes* moves
    down. That asymmetry is the difference from ``hysteresis``, which widens the
    band in both directions and therefore boosts slightly early.
    """
    if dynamic_drop > 0.0:
        return score >= (threshold - dynamic_drop if currently_boosting else threshold)
    if hysteresis <= 0.0:
        return score >= threshold
    if currently_boosting:
        return score >= threshold - hysteresis
    return score >= threshold + hysteresis


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _text(root: ET.Element, tag: str, default: str | None = None) -> str:
    node = root.find(tag)
    if node is None or node.text is None:
        if default is None:
            raise ConfigError(
                f"config.xml is missing <{tag}>. It was probably written by an older "
                f"version of Mercy AI -- delete config.xml to regenerate it."
            )
        return default
    return node.text.strip()


FLASH_TRIGGERS = ("off", "expiry")
HEALTH_MODES = ("emergency", "low")
THEMES = ("auto", "light", "dark")


def _theme(raw: str) -> str:
    """Accept only a known theme, falling back to ``auto``."""
    value = raw.strip().lower()
    if value in THEMES:
        return value
    log.warning("theme=%r is not one of %s; using 'auto'.", raw, ", ".join(THEMES))
    return "auto"


def _flash_trigger(raw: str) -> str:
    """Accept only a known trigger, falling back to ``off`` with a warning.

    The warning matters: a typo would otherwise disable the feature silently.
    """
    value = raw.strip().lower()
    if value in FLASH_TRIGGERS:
        return value
    if value == "timer":
        # The timer trigger was retired; its files keep the grenade trigger on.
        return "expiry"
    log.warning(
        "flash_heal_trigger=%r is not one of %s; using 'off'.", raw, ", ".join(FLASH_TRIGGERS)
    )
    return "off"


def _health_mode(raw: str) -> str:
    """Accept only a known health trigger mode, falling back to ``emergency``."""
    value = raw.strip().lower()
    if value in HEALTH_MODES:
        return value
    log.warning(
        "flash_heal_health_mode=%r is not one of %s; using 'emergency'.",
        raw,
        ", ".join(HEALTH_MODES),
    )
    return "emergency"


def read_config(path: Path) -> Config:
    """Parse ``config.xml`` into a :class:`Config`, or explain why it cannot.

    A corrupt file, a missing required tag or a non-numeric value each raise
    :class:`ConfigError` with a message the player can act on, never a raw
    traceback.
    """
    if not path.is_file():
        raise ConfigError(f"config.xml not found at {path}.")
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, LookupError, ValueError) as exc:
        # LookupError: an unknown encoding in the XML declaration, which a hand
        # edit can introduce just as easily as a broken tag.
        raise ConfigError(
            f"config.xml is corrupt and cannot be read ({exc}).\nDelete it to regenerate it."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"config.xml could not be opened ({exc}).") from exc

    def number(tag: str, default: str | None = None) -> float:
        raw = _text(root, tag, default)
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError(
                f"config.xml: <{tag}> should be a number but is {raw!r}. "
                f"Use a dot for decimals, like 0.65."
            ) from exc
        # "nan" and "inf" parse as floats but are not settings: they slip past
        # every range check, and int() of them raises outside ConfigError.
        if not math.isfinite(value):
            raise ConfigError(f"config.xml: <{tag}> should be a number but is {raw!r}.")
        return value

    config = Config(
        resolution=_text(root, "resolution"),
        heal_btn=_text(root, "heal_btn"),
        damage_boost_btn=_text(root, "damage_boost_btn"),
        keybind_btn=_text(root, "keybind_btn", DEFAULTS["keybind_btn"]),
        pause_toggle_button=_text(root, "pause_toggle_button", DEFAULTS["pause_toggle_button"]),
        threshold_percent=number("mercy_bar_percentage", DEFAULTS["mercy_bar_percentage"]),
        bomb_threshold=number("ana_bomb_threshold", DEFAULTS["ana_bomb_threshold"]),
        focus_gate=_text(
            root,
            "disable_mercyAI_while_overwatch_unfocused",
            DEFAULTS["disable_mercyAI_while_overwatch_unfocused"],
        )
        != "0",
        click_check=_text(root, "check_click_target", DEFAULTS["check_click_target"]) != "0",
        action_cooldown=number("action_cooldown", DEFAULTS["action_cooldown"]),
        target_fps=number("target_fps", DEFAULTS["target_fps"]),
        bomb_detection=_text(
            root, "enable_ana_bomb_detection", DEFAULTS["enable_ana_bomb_detection"]
        )
        != "0",
        hysteresis=number("switch_hysteresis", DEFAULTS["switch_hysteresis"]),
        fast_capture=_text(root, "fast_capture", DEFAULTS["fast_capture"]) != "0",
        flash_heal_btn=_text(root, "flash_heal_btn", DEFAULTS["flash_heal_btn"]),
        flash_heal_trigger=_flash_trigger(
            _text(root, "flash_heal_trigger", DEFAULTS["flash_heal_trigger"])
        ),
        flash_heal_on_health_drop=_text(
            root, "flash_heal_on_health_drop", DEFAULTS["flash_heal_on_health_drop"]
        )
        == "1",
        flash_heal_health_mode=_health_mode(
            _text(root, "flash_heal_health_mode", DEFAULTS["flash_heal_health_mode"])
        ),
        flash_heal_low_health=number("flash_heal_low_health", DEFAULTS["flash_heal_low_health"]),
        flash_heal_cooldown=number("flash_heal_cooldown", DEFAULTS["flash_heal_cooldown"]),
        flash_heal_min_heal_time=number(
            "flash_heal_min_heal_time", DEFAULTS["flash_heal_min_heal_time"]
        ),
        flash_heal_drop=number("flash_heal_drop", DEFAULTS["flash_heal_drop"]),
        flash_heal_settle=number("flash_heal_settle", DEFAULTS["flash_heal_settle"]),
        flash_heal_max_fall_rate=number(
            "flash_heal_max_fall_rate", DEFAULTS["flash_heal_max_fall_rate"]
        ),
        flash_heal_blank_floor=number("flash_heal_blank_floor", DEFAULTS["flash_heal_blank_floor"]),
        flash_heal_staff_check=_text(
            root, "flash_heal_staff_check", DEFAULTS["flash_heal_staff_check"]
        )
        != "0",
        flash_heal_weapon_threshold=number(
            "flash_heal_weapon_threshold", DEFAULTS["flash_heal_weapon_threshold"]
        ),
        theme=_theme(_text(root, "theme", DEFAULTS["theme"])),
        dynamic_heal=_text(root, "dynamic_heal", DEFAULTS["dynamic_heal"]) == "1",
        dynamic_heal_drop=number("dynamic_heal_drop", DEFAULTS["dynamic_heal_drop"]),
    )

    # Bound every numeric field rather than letting nonsense through. The
    # limits are a table in config_schema; this applies it.
    bounded: dict[str, Any] = {
        field: clamped(tag, getattr(config, field), low, high)
        for field, (tag, low, high) in LIMITS.items()
    }
    return replace(config, **bounded)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def config_to_xml(config: Config) -> ET.Element:
    """Serialise a :class:`Config` into the documented element tree."""
    values: dict[str, str] = {
        "mercy_bar_percentage": _format_number(config.threshold_percent),
        "disable_mercyAI_while_overwatch_unfocused": "1" if config.focus_gate else "0",
        "check_click_target": "1" if config.click_check else "0",
        "pause_toggle_button": config.pause_toggle_button,
        "keybind_btn": config.keybind_btn,
        "heal_btn": config.heal_btn,
        "damage_boost_btn": config.damage_boost_btn,
        "resolution": config.resolution,
        "ana_bomb_threshold": _format_number(config.bomb_threshold),
        "action_cooldown": _format_number(config.action_cooldown),
        "target_fps": _format_number(config.target_fps),
        "enable_ana_bomb_detection": "1" if config.bomb_detection else "0",
        "switch_hysteresis": _format_number(config.hysteresis),
        "fast_capture": "1" if config.fast_capture else "0",
        "flash_heal_btn": config.flash_heal_btn,
        "flash_heal_trigger": config.flash_heal_trigger,
        "flash_heal_on_health_drop": "1" if config.flash_heal_on_health_drop else "0",
        "flash_heal_health_mode": config.flash_heal_health_mode,
        "flash_heal_low_health": _format_number(config.flash_heal_low_health),
        "flash_heal_cooldown": _format_number(config.flash_heal_cooldown),
        "flash_heal_min_heal_time": _format_number(config.flash_heal_min_heal_time),
        "flash_heal_drop": _format_number(config.flash_heal_drop),
        "flash_heal_settle": _format_number(config.flash_heal_settle),
        "flash_heal_max_fall_rate": _format_number(config.flash_heal_max_fall_rate),
        "flash_heal_blank_floor": _format_number(config.flash_heal_blank_floor),
        "flash_heal_staff_check": "1" if config.flash_heal_staff_check else "0",
        "flash_heal_weapon_threshold": _format_number(config.flash_heal_weapon_threshold),
        "theme": config.theme,
        "dynamic_heal": "1" if config.dynamic_heal else "0",
        "dynamic_heal_drop": _format_number(config.dynamic_heal_drop),
        "config_version": str(CONFIG_VERSION),
    }

    root = ET.Element("config")
    for tag in FIELD_ORDER:
        if tag in COMMENTS:
            root.append(ET.Comment(_xml_comment(COMMENTS[tag])))
        ET.SubElement(root, tag).text = values[tag]
    return root


def _xml_comment(text: str) -> str:
    """Make *text* safe to put inside an XML comment.

    A double hyphen is illegal inside ``<!-- -->`` and makes the whole file
    unparseable -- which would leave the user with a config.xml that Mercy AI
    itself had just written and could no longer read.  Prose written for these
    comments naturally contains "--" as punctuation, so this is not hypothetical.
    """
    return " " + text.replace("--", "-") + " "


def _format_number(value: float) -> str:
    """Write 10 rather than 10.0, so a hand-edited file stays readable."""
    return f"{value:g}"


def write_config_atomic(root: ET.Element, path: Path) -> None:
    """Write the config so an interrupted save cannot destroy it.

    Writes via a sibling ``.tmp`` file and ``os.replace``, as UTF-8 to match the
    XML declaration, so a kill mid-save (Ctrl+C, Task Manager) never leaves a
    truncated or wrongly encoded config.xml.
    """
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tmp = path.with_name(path.name + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    # Path.replace would be equivalent, but keeping the os-level call gives tests
    # a seam to monkeypatch a mid-write failure and prove the previous config
    # survives it (see tests/test_config.py::
    # test_a_failed_write_leaves_the_previous_config_intact).
    os.replace(tmp, path)  # noqa: PTH105 - atomic on Windows and POSIX


def migrate_config(root: ET.Element) -> bool:
    """Back-fill keys added since the file was written.

    Returns ``True`` when the tree changed and should be written back.  Only keys
    with a safe default are filled in; the rest still raise so the user is
    prompted rather than silently given someone else's settings.
    """
    changed = False
    for tag, default in DEFAULTS.items():
        if root.find(tag) is None:
            ET.SubElement(root, tag).text = default
            log.info("config.xml: added missing <%s> with default %r", tag, default)
            changed = True

    version_node = root.find("config_version")
    if version_node is None:
        version_node = ET.SubElement(root, "config_version")
        changed = True
    if version_node.text != str(CONFIG_VERSION):
        version_node.text = str(CONFIG_VERSION)
        changed = True
    return changed
