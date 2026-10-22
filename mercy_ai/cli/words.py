# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The sentences the text interface shows, worked out from the live settings.

Each function here has a twin in the window's ``app.js`` and says the same
thing in the same words, so a player moving between the two never reads two
explanations of one setting. Everything is pure: it takes the dictionaries the
:class:`~mercy_ai.ui.bridge.Bridge` hands out and returns text.
"""

from __future__ import annotations

from typing import Any

from ..config import threshold_translator
from ..config_schema import MOUSE_TRIGGERS

__all__ = [
    "default_words",
    "flash_caption",
    "fmt3",
    "help_checks",
    "is_default",
    "key_name",
    "live_line",
    "plain",
    "resolution_alert",
    "resolution_note",
    "threshold_for",
    "trigger_label",
    "wizard_key_note",
    "wizard_key_problem",
]

RESOLUTION_NAMES = {"2160": "4K", "1440": "2K", "1080": "FHD", "768": "HD+"}
UNBINDABLE = "That key cannot be used. Try a letter, a digit or an F key."


def key_name(key: str | None) -> str:
    """ "F2" and "R" read better than "f2" and "r"; "page up" stays as it is."""
    if not key:
        return ""
    if key in MOUSE_TRIGGERS:
        return MOUSE_TRIGGERS[key]
    return key.upper() if len(key) <= 3 else key


def trigger_label(key: str | None) -> str:
    return MOUSE_TRIGGERS.get(key or "", key or "")


def fmt3(value: Any) -> str:
    try:
        return f"{float(value or 0):.3f}"
    except (TypeError, ValueError):
        return "0.000"


def plain(value: float) -> str:
    """0.05 rather than 0.05000000000000001, and 1 rather than 1.0."""
    return f"{float(value):g}"


def threshold_for(dial: float) -> float:
    """The switch point a dial position gives, clamped to the dial's range."""
    return threshold_translator(min(10.0, max(1.0, float(dial))))


def live_line(name: str, config: dict[str, Any]) -> str:
    """The line under a number that works it out against the player's own values."""
    if name == "dynamic_heal_drop":
        resume = float(config["threshold"]) - float(config["dynamic_heal_drop"])
        if resume <= 0:
            return (
                "With your settings Mercy AI never goes back to healing once damage boost "
                "starts, until you let go of the trigger. Type a smaller number or move the "
                "dial higher."
            )
        return (
            f"With your settings: damage boost from {fmt3(config['threshold'])}, "
            f"back to healing below {fmt3(resume)}."
        )
    if name == "flash_heal_drop":
        drop = float(config["flash_heal_drop"])
        return (
            f"With {plain(drop)}: a fall from 0.900 to {fmt3(max(0.0, 0.9 - drop))} or lower "
            f"within 0.3 seconds fires Flash Heal."
        )
    if name == "flash_heal_low_health":
        line = float(config["flash_heal_low_health"])
        return (
            f"With {plain(line)}: Flash Heal fires once your ally's health match is below "
            f"{fmt3(line)}, then waits until they are back above {fmt3(line + 0.05)}."
        )
    return ""


def resolution_note(config: dict[str, Any], env: dict[str, Any]) -> str:
    """What the resolution number means, and whether this screen matches it."""
    sizes = config.get("resolution_sizes") or {}
    res = config.get("resolution", "")
    chosen = (
        f"{res} means a screen {res} pixels tall (usually {sizes[res]})." if res in sizes else ""
    )
    primary = env.get("primary")
    if not primary:
        return chosen
    if env.get("detected"):
        return f"{chosen} This monitor is {primary}, so {env['detected']} is the match.".strip()
    return (
        f"{chosen} This monitor is {primary}, which matches none of them {_dash()} fine if you "
        f"are setting up for another computer, but Mercy AI cannot watch the screen here."
    ).strip()


def _dash() -> str:
    return "-"


def resolution_alert(config: dict[str, Any], env: dict[str, Any]) -> tuple[str, str | None]:
    """The warning shown when screen and setting disagree, and the fix to offer.

    Returns ``("", None)`` when there is nothing to say.
    """
    detected = env.get("detected")
    primary = env.get("primary")
    if detected and detected != config.get("resolution"):
        return (
            f"Your screen is {primary}, but Mercy AI is set to {config.get('resolution')}p.",
            detected,
        )
    if primary and not detected:
        return (
            f"This screen is {primary}. Mercy AI only supports screens 2160, 1440, 1080 or 768 "
            f"pixels tall, so it cannot watch this one.",
            None,
        )
    return "", None


def help_checks(config: dict[str, Any], env: dict[str, Any]) -> list[tuple[bool, str]]:
    elevated = env.get("elevated")
    detected = env.get("detected")
    primary = env.get("primary")
    if elevated is True:
        admin = "Running as administrator."
    elif elevated is False:
        admin = "Not running as administrator - fine unless key presses do not reach the game."
    else:
        admin = "Could not tell whether this is running as administrator."
    if detected:
        screen = f"This screen is {primary}, which Mercy AI has templates for."
    else:
        screen = (
            f"This screen is {primary or 'unknown'} - Mercy AI has no templates for it and "
            f"cannot watch it here."
        )
    res = config.get("resolution")
    if detected == res:
        match = "The configured resolution matches this screen."
    else:
        match = f"Configured for {res}p but this screen is {detected or 'different'}."
    return [
        (elevated is not False, admin),
        (bool(detected), screen),
        (not detected or detected == res, match),
    ]


def flash_caption(config: dict[str, Any]) -> str:
    if not config.get("flash_heal_enabled"):
        return "Off. Turn on to counter Ana's anti-heal grenade."
    when = []
    if config.get("flash_heal_trigger") == "expiry":
        when.append("when Ana's grenade wears off your ally, if they are below half health")
    if config.get("flash_heal_on_health_drop"):
        if config.get("flash_heal_health_mode") == "low":
            when.append(f"when your ally is below {fmt3(config.get('flash_heal_low_health'))}")
        else:
            when.append("when your ally suddenly drops")
    return f"Casts Flash Heal (key {key_name(config.get('flash_heal_btn'))}) {', and '.join(when)}."


def is_default(field: str, config: dict[str, Any]) -> bool:
    defaults = config.get("defaults") or {}
    if field not in defaults:
        return True
    want = defaults[field]
    if isinstance(want, bool) or not isinstance(want, (int, float)):
        return config.get(field) == want
    value: Any = config.get(field)
    try:
        return abs(float(value) - float(want)) < 1e-9
    except (TypeError, ValueError):
        return False


def default_words(field: str, config: dict[str, Any]) -> str:
    value = (config.get("defaults") or {}).get(field)
    if isinstance(value, bool):
        return "on" if value else "off"
    if field == "keybind_btn":
        return trigger_label(value)
    if field in ("pause_toggle_button", "flash_heal_btn"):
        return key_name(value)
    if isinstance(value, str):
        return value[:1].upper() + value[1:]
    if isinstance(value, float):
        return plain(value)
    return str(value)


def _taken_keys(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Keys Mercy AI already uses for something else (mouse buttons aside)."""
    taken = [
        (config.get("pause_toggle_button") or "", "your pause key"),
        (
            config.get("flash_heal_btn") or "" if config.get("flash_heal_enabled") else "",
            "your Flash Heal key",
        ),
        (config.get("keybind_btn") or "", "your trigger"),
    ]
    return [(key, what) for key, what in taken if key and key not in MOUSE_TRIGGERS]


def wizard_key_problem(config: dict[str, Any], heal: str | None, boost: str | None) -> str:
    for role, key in (("heal", heal), ("damage boost", boost)):
        for other, what in _taken_keys(config):
            if key and key == other:
                return f"{key_name(key)} is {what}. Pick a different key for {role}."
    if heal and heal == boost:
        return (
            "Those are the same key. Heal and damage boost need to be different, or they'll "
            "fight each other in game."
        )
    return ""


def wizard_key_note(config: dict[str, Any]) -> str:
    """Names only the keys that cannot be used, never a key to pick."""
    taken = [f"{key_name(key)} ({what.replace('your ', '')})" for key, what in _taken_keys(config)]
    return (
        "Choose any keyboard keys you like"
        + (", except " + " and ".join(taken) if taken else "")
        + ". Not a mouse button."
    )
