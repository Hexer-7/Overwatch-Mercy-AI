# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The config file's data tables: defaults, comments, field order and limits.

These tables are pure data, one entry per XML tag; :mod:`mercy_ai.config` holds
the logic that reads, validates and writes them.

Adding a setting touches these places:

* :class:`mercy_ai.config.Config` -- the field and its default.
* :data:`DEFAULTS` -- the value written for a tag that is missing, which is also
  what makes :func:`mercy_ai.config.migrate_config` back-fill an older file.
  Leave it out only when there is no sane default.
* :data:`COMMENTS` -- the explanation written above the tag, so the file
  documents itself.  A ``--`` is fine here: ``config._xml_comment`` rewrites it,
  because it is illegal inside an XML comment.
* :data:`FIELD_ORDER` -- position in the generated file.  A tag missing from
  here is never written, and migration then re-adds it forever.
* :data:`LIMITS` -- the allowed range, for a numeric setting.
* :func:`mercy_ai.config.read_config` and :func:`mercy_ai.config.config_to_xml`
  -- the line that reads the tag and the line that writes it.

A setting the window should show also needs an entry in
``mercy_ai.ui.model.FIELD_TYPES``.
"""

from __future__ import annotations

__all__ = ["COMMENTS", "DEFAULTS", "FIELD_ORDER", "LIMITS", "MOUSE_TRIGGERS"]

#: Trigger settings that mean a mouse button, and how each is shown to the player.
MOUSE_TRIGGERS: dict[str, str] = {
    "left_mouse": "Mouse Left click",
    "right_mouse": "Mouse Right click",
}

#: Keys that have a safe default and can therefore be back-filled on upgrade.
#: ``heal_btn``, ``damage_boost_btn`` and ``resolution`` are deliberately absent:
#: there is no sane default for them, so their absence must still prompt the user.
DEFAULTS: dict[str, str] = {
    "mercy_bar_percentage": "10",
    "disable_mercyAI_while_overwatch_unfocused": "1",
    "check_click_target": "1",
    "pause_toggle_button": "f2",
    "keybind_btn": "left_mouse",
    "ana_bomb_threshold": "0.74",
    "action_cooldown": "0.2",
    "target_fps": "0",
    "enable_ana_bomb_detection": "1",
    "switch_hysteresis": "0",
    "fast_capture": "1",
    "flash_heal_btn": "r",
    "flash_heal_trigger": "off",
    "flash_heal_on_health_drop": "0",
    "flash_heal_health_mode": "emergency",
    "flash_heal_low_health": "0.4",
    "flash_heal_cooldown": "0",
    "flash_heal_min_heal_time": "0.5",
    "flash_heal_drop": "0.5",
    "flash_heal_settle": "0.4",
    "flash_heal_max_fall_rate": "60",
    "flash_heal_blank_floor": "0.02",
    "theme": "light",
    "dynamic_heal": "0",
    "dynamic_heal_drop": "0.15",
}

#: Comments written into a freshly generated config, so the file documents itself.
COMMENTS: dict[str, str] = {
    "mercy_bar_percentage": (
        "When to switch to damage boost, from 1 to 10. 10 waits until the ally is full; "
        "lower numbers switch earlier. Decimals such as 6.3 are allowed. Default 10."
    ),
    "disable_mercyAI_while_overwatch_unfocused": (
        "Pause while Overwatch is not the active window. 1 = on (default), 0 = off."
    ),
    "check_click_target": (
        "Strict focus. With a mouse trigger, only start when the click lands on the Overwatch "
        "window, so a click on another window or monitor never presses keys. Only used while "
        "disable_mercyAI_while_overwatch_unfocused is 1. 1 = on (default), 0 = off. Turn it "
        "off only if holding the trigger in game does nothing while an overlay sits over "
        "the game."
    ),
    "pause_toggle_button": "The key that pauses and resumes Mercy AI. Default f2.",
    "keybind_btn": (
        "What you hold in game to run Mercy AI: left_mouse (default), right_mouse or a "
        "keyboard key name."
    ),
    "heal_btn": (
        "The keyboard key Overwatch has bound to Mercy's healing beam. Mercy AI holds "
        "this key down. It must be a keyboard key, not a mouse button, because a mouse "
        "button cannot be held independently of the trigger."
    ),
    "damage_boost_btn": (
        "The keyboard key Overwatch has bound to Mercy's damage boost. Must be "
        "different from heal_btn."
    ),
    "resolution": "this is the resolution of the game: [2160, 1440, 1080, 768]",
    "ana_bomb_threshold": (
        "How confident the Ana grenade match must be, 0.0 - 1.0. Default 0.74. "
        "Measured on real frames: the icon scores about 0.78 to 0.81 and everything "
        "else scores 0.0, so there is far more room below this number than above it. "
        "Raise it if damage boost triggers randomly; lower it if grenades are missed."
    ),
    "action_cooldown": (
        "Minimum seconds between weapon switches. Lower = more responsive but switches "
        "more often. Default 0.2."
    ),
    "target_fps": (
        "Cap on how many times per second the screen is checked. 0 = unlimited "
        "(default). Weapon switches are already gated to one every "
        "action_cooldown seconds (0.2 by default = 5 per second), so checking far "
        "faster than that mostly burns CPU that the game could be using. 30 to 60 "
        "is plenty; at 60 the worst extra reaction delay is about 17 ms against a "
        "200 ms gate. Lower this first if Mercy AI costs you in-game frames."
    ),
    "enable_ana_bomb_detection": "Detect Ana's biotic grenade and force damage boost. 1 = on.",
    "fast_capture": (
        "Drop the CAPTUREBLT flag from the screen grab. 1 = on (default). "
        "CAPTUREBLT makes Windows flatten the whole desktop on every capture and can "
        "cost several milliseconds per frame; the game's health bar is captured either "
        "way. Set 0 only if the capture looks wrong."
    ),
    "flash_heal_btn": (
        "In-game key bound to Mercy's Flash Heal. Default r, which is the game's "
        "own default. Must be its own key while Flash Heal is on. Nothing is pressed "
        "unless flash_heal_trigger is expiry or flash_heal_on_health_drop is 1."
    ),
    "flash_heal_trigger": (
        "Flash Heal after Ana's anti-heal grenade. off (default) = never. "
        "expiry = press the moment the grenade wears off your ally, but only if their "
        "health score is then below 0.5 (above that the beam tops them up on its own)."
    ),
    "flash_heal_on_health_drop": (
        "Also fire Flash Heal on your ally's health, independent of Ana. 1 = on, "
        "0 = off (default). flash_heal_health_mode picks how."
    ),
    "flash_heal_health_mode": (
        "How flash_heal_on_health_drop watches health. emergency (default) = fire when the "
        "health score falls by flash_heal_drop within 0.3 seconds. low = fire when the "
        "health score is below flash_heal_low_health, however fast it got there; once per "
        "dip, ready again when the ally climbs back above it."
    ),
    "flash_heal_low_health": (
        "The line for flash_heal_health_mode low, 0.1 to 0.8. Default 0.4. Ignored for "
        "the first flash_heal_min_heal_time seconds of healing, while the beam connects."
    ),
    "flash_heal_cooldown": (
        "Minimum seconds Mercy AI waits before pressing Flash Heal again. Default 0, "
        "meaning it does not wait. Pressing the key while the ability is genuinely on "
        "cooldown costs nothing in game, so modelling the 12 second cooldown here only "
        "risked missing a cast that was actually available. Raise it only if you want "
        "the key pressed less often."
    ),
    "flash_heal_min_heal_time": (
        "The beam must already have been healing the ally for this many seconds when "
        "the grenade lands. Stops a flash being spent on someone you merely glanced at. "
        "The low-health trigger waits this long after the beam connects, too."
    ),
    "theme": (
        "Appearance of the desktop window: auto, light or dark. auto follows the "
        "Windows setting. Default light. Ignored by the console version."
    ),
    "dynamic_heal": (
        "Smooth healing. 1 = on, 0 = off (default). Normally the beam flips to damage "
        "boost the instant the ally is at or above mercy_bar_percentage, and back to "
        "healing the instant they are below it, which makes it flick back and forth "
        "while they take chip damage. With this on, Mercy AI stays on damage boost "
        "until they have fallen dynamic_heal_drop below the line, then heals them all "
        "the way back up to it before switching again."
    ),
    "dynamic_heal_drop": (
        "How far below the switch point the ally must fall before smooth healing "
        "starts, 0.0 to 1.0. Default 0.15. Only used when dynamic_heal is 1."
    ),
    "flash_heal_settle": (
        "Seconds to ignore after you press the trigger before the health drop detector "
        "starts watching. Mercy's beam ring animates as it connects, and that animation "
        "reads as a sudden fall, so a flash would fire the instant you pressed. Default "
        "0.4, which covers the connect animation."
    ),
    "flash_heal_max_fall_rate": (
        "Ignore falls faster than this many score points per second. Measured in game: "
        "real damage never fell faster than 16, while a scoreboard drawn over the "
        "reticle fell at 56 to 124. Default 60. 0 turns this check off."
    ),
    "flash_heal_blank_floor": (
        "Ignore falls that end at or below this score. Nothing visible means the ring "
        "is covered, not that the ally is nearly dead: a hurt ally still draws one. "
        "0 turns this check off."
    ),
    "flash_heal_drop": (
        "How far the health score must fall to count as an emergency, 0.1 to 0.85. "
        "Default 0.5. Values near 1.0 can never fire. Only used when "
        "flash_heal_on_health_drop is 1 and flash_heal_health_mode is emergency."
    ),
    "switch_hysteresis": (
        "Dead band around the switch threshold, to stop the beam flickering when the "
        "score sits right on the line. 0.0 = off (default). Try 0.02."
    ),
}

#: Field order in the generated file.
FIELD_ORDER = (
    "mercy_bar_percentage",
    "disable_mercyAI_while_overwatch_unfocused",
    "check_click_target",
    "pause_toggle_button",
    "keybind_btn",
    "heal_btn",
    "damage_boost_btn",
    "resolution",
    "ana_bomb_threshold",
    "action_cooldown",
    "target_fps",
    "enable_ana_bomb_detection",
    "switch_hysteresis",
    "fast_capture",
    "flash_heal_btn",
    "flash_heal_trigger",
    "flash_heal_on_health_drop",
    "flash_heal_health_mode",
    "flash_heal_low_health",
    "flash_heal_cooldown",
    "flash_heal_min_heal_time",
    "flash_heal_drop",
    "flash_heal_settle",
    "flash_heal_max_fall_rate",
    "flash_heal_blank_floor",
    "theme",
    "dynamic_heal",
    "dynamic_heal_drop",
    "config_version",
)

#: Field name -> ``(xml tag, lowest, highest)`` for every numeric setting.
#:
#: :func:`mercy_ai.config.read_config` clamps each number into its range, with
#: a warning, rather than letting nonsense through.
#:
#: The tag is carried alongside because a few fields are named differently in
#: the dataclass than in the file, and the warning has to name the one the user
#: would search for in ``config.xml``.
LIMITS: dict[str, tuple[str, float, float]] = {
    "threshold_percent": ("mercy_bar_percentage", 1.0, 10.0),
    "bomb_threshold": ("ana_bomb_threshold", 0.0, 1.0),
    "action_cooldown": ("action_cooldown", 0.0, 5.0),
    "target_fps": ("target_fps", 0.0, 1000.0),
    "hysteresis": ("switch_hysteresis", 0.0, 0.5),
    "flash_heal_low_health": ("flash_heal_low_health", 0.01, 0.95),
    "flash_heal_cooldown": ("flash_heal_cooldown", 0.0, 120.0),
    "flash_heal_min_heal_time": ("flash_heal_min_heal_time", 0.0, 10.0),
    "flash_heal_drop": ("flash_heal_drop", 0.01, 0.95),
    "flash_heal_settle": ("flash_heal_settle", 0.0, 5.0),
    "flash_heal_max_fall_rate": ("flash_heal_max_fall_rate", 0.0, 500.0),
    "flash_heal_blank_floor": ("flash_heal_blank_floor", 0.0, 0.5),
    "dynamic_heal_drop": ("dynamic_heal_drop", 0.0, 1.0),
}
