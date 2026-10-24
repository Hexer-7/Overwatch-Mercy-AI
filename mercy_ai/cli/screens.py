# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""What each screen contains, and how a whole frame is drawn.

Every screen is a list of :class:`Row` objects. Some can take focus (a switch,
a number, a key, a button); the rest are text. :func:`rows_for` builds the list
for whatever the state says is on screen, and :func:`render` turns state and
data into exactly ``rows`` lines of at most ``cols`` cells each.

Both are pure. The controller uses :func:`rows_for` to know what the arrow
keys move between; the app draws whatever :func:`render` returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as _field
from typing import Any

from .. import __copyright__
from ..winutil import elevation_warning
from .state import TABS, Data, UIState
from .style import Style
from .text import cell_width, fit, truncate, wrap
from .words import (
    RESOLUTION_NAMES,
    default_words,
    flash_caption,
    fmt3,
    help_checks,
    is_default,
    key_name,
    live_line,
    plain,
    resolution_alert,
    resolution_note,
    threshold_for,
    trigger_label,
    wizard_key_note,
    wizard_key_problem,
)

__all__ = [
    "CLI_FIELDS",
    "MIN_COLS",
    "MIN_ROWS",
    "Frame",
    "Row",
    "render",
    "rows_for",
    "view_name",
]

MIN_COLS = 80
MIN_ROWS = 24
#: Where values start, counted from the label.
LABEL_WIDTH = 42
#: The right-hand panel in a wide window.
PANEL_WIDTH = 42

#: Every setting the text interface can change. The window can also change
#: ``theme``, which means nothing in a console.
CLI_FIELDS = frozenset(
    {
        "resolution",
        "heal_btn",
        "damage_boost_btn",
        "keybind_btn",
        "pause_toggle_button",
        "threshold_percent",
        "focus_gate",
        "click_check",
        "bomb_detection",
        "flash_heal_btn",
        "flash_heal_trigger",
        "flash_heal_on_health_drop",
        "flash_heal_health_mode",
        "flash_heal_low_health",
        "flash_heal_drop",
        "flash_heal_staff_check",
        "dynamic_heal",
        "dynamic_heal_drop",
    }
)

#: Arrow-key step for each number, matching the window's input boxes.
STEPS = {
    "threshold_percent": 0.5,
    "dynamic_heal_drop": 0.01,
    "flash_heal_drop": 0.05,
    "flash_heal_low_health": 0.05,
}

#: Captions for the rows the window explains with its layout instead.
EXTRA_CAPTIONS = {
    "heal_btn": "The keyboard key you bound to Mercy's healing beam (Primary Fire) in "
    "Overwatch. Mercy AI holds it down. It must be a keyboard key, not a mouse button.",
    "damage_boost_btn": "The keyboard key you bound to Mercy's damage boost (Secondary "
    "Fire) in Overwatch. It must be different from your heal key.",
    "keybind_btn": "What you hold in game to make Mercy AI work: Mouse Left click, Mouse "
    "Right click or a keyboard key. Mercy AI only presses keys while you hold it.",
    "pause_toggle_button": "Tap it any time, in game or here, to pause or resume "
    "everything. It must be a key no other setting uses.",
    "flash_heal_btn": "The key you bound to Mercy's Flash Heal in Overwatch. It must be "
    "its own key while Flash Heal is on.",
    "resolution": "Your game screen's height in pixels: 2160, 1440, 1080 or 768. It is "
    "normally your desktop resolution.",
    "advanced": "Controls, detection, smooth healing and Flash Heal numbers. Sensible "
    "defaults; change these only if something specific needs it.",
    "open_folder": "Opens the folder with Mercy AI and config.xml. A few rarely needed "
    "settings are only in config.xml; edits there apply the next time you change a "
    "setting here or restart Mercy AI.",
    "redo_setup": "Runs the three setup steps again with your current keys and "
    "resolution filled in. Esc on step 1 leaves setup with nothing changed.",
}

#: Shown at the top of Settings > Advanced.
PRACTICE_NOTICE = (
    "If you want to edit advanced settings, it's recommended to test them in the "
    "Practice Range first."
)

#: The line under each Flash Heal switch in Advanced.
ANA_SUB = "Casts Flash Heal when Ana's grenade wears off your ally, if they are below half health."
HEALTH_SUB = "Casts Flash Heal when your ally's health falls fast or drops below a line."

CAPTURE_PROMPTS = {
    "heal_btn": (
        "Set the heal key",
        "Press the key you bound to Mercy's healing beam in Overwatch.",
    ),
    "damage_boost_btn": (
        "Set the damage boost key",
        "Press the key you bound to Mercy's damage boost in Overwatch.",
    ),
    "pause_toggle_button": (
        "Set the pause key",
        "Press the key you will tap to pause or resume everything.",
    ),
    "flash_heal_btn": (
        "Set the Flash Heal key",
        "Press the key you bound to Mercy's Flash Heal in Overwatch.",
    ),
    "keybind_btn": ("Trigger (hold to run)", "Press the keyboard key you will hold in game."),
}

KEY_LABELS = {
    "heal_btn": "Heal key",
    "damage_boost_btn": "Damage boost key",
    "keybind_btn": "Trigger",
    "pause_toggle_button": "Pause key",
    "flash_heal_btn": "Flash Heal key",
}

TRIGGER_CHOICES = (("left_mouse", "Mouse Left click"), ("right_mouse", "Mouse Right click"))

#: Characters that can still reach a frame through shared helpers, for a
#: console that can only show ASCII.
_ASCII_FALLBACK = str.maketrans(
    {"…": ".", "›": ">", "‹": "<", "·": "-", "—": "-", "–": "-", "→": ">"}
)

FOCUSABLE = frozenset({"toggle", "choice", "number", "dial", "key", "button", "check", "radio"})


@dataclass
class Row:
    id: str
    kind: str  # one of FOCUSABLE, or "text"
    label: str = ""
    field: str = ""
    value: Any = None
    options: tuple[tuple[str, str], ...] = ()
    enabled: bool = True
    hint: str = ""
    sub: list[str] = _field(default_factory=list)
    sub_tone: str = "accent"
    caption: str = ""
    reset: dict[str, Any] | None = None
    reset_words: str = ""
    lines: list[str] = _field(default_factory=list)  # for "text" rows, already styled
    label_width: int = LABEL_WIDTH

    @property
    def focusable(self) -> bool:
        return self.kind in FOCUSABLE and self.enabled


@dataclass
class Frame:
    lines: list[str]
    scroll: int


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def view_name(state: UIState) -> str:
    if state.screen == "setup":
        return f"setup{state.setup.step}"
    if state.tab == 1 and state.advanced:
        return "advanced"
    return ("status", "settings", "help", "about")[state.tab]


def _wide(state: UIState) -> bool:
    return state.cols >= 120 and state.rows >= 30


def _left_width(state: UIState) -> int:
    if _wide(state) and view_name(state) in ("status", "settings", "advanced", "help"):
        return min(state.cols - PANEL_WIDTH - 4, 96)
    return min(state.cols, 100)


def value_of(state: UIState, data: Data, name: str) -> Any:
    """A setting as shown: a stepped or just-sent value wins over the stored one."""
    if name in state.pending:
        return state.pending[name]
    if name in state.inflight:
        return state.inflight[name]
    return data.config.get(name)


def _text(lines: list[str], ident: str = "") -> Row:
    return Row(id=ident or f"text{id(lines)}", kind="text", lines=lines)


def _para(text: str, width: int, paint=lambda s: s, indent: int = 3) -> list[str]:
    return [" " * indent + paint(line) for line in wrap(text, max(10, width - indent - 2))]


def _box(
    title: str, body: list[str], width: int, style: Style, paint=None, indent: int = 3
) -> list[str]:
    """A titled box; *body* is plain text, wrapped to fit."""
    g = style.glyphs
    paint = paint or (lambda s: s)
    inner = max(10, width - indent - 2 - 4)
    top = f"{g.top_left}{g.rule} {title} "
    top += g.rule * max(0, inner + 3 - cell_width(top)) + g.top_right
    lines = [" " * indent + paint(top)]
    for paragraph in body:
        for line in wrap(paragraph, inner):
            lines.append(
                " " * indent + paint(g.vbar) + " " + fit(line, inner) + " " + paint(g.vbar)
            )
    lines.append(" " * indent + paint(g.bottom_left + g.rule * (inner + 2) + g.bottom_right))
    return lines


def _range_text(name: str, config: dict[str, Any], style: Style) -> str:
    low, high = (config.get("ranges") or {}).get(name, (None, None))
    if low is None:
        return ""

    def show(value: float) -> str:
        return plain(value) if float(value).is_integer() else f"{value:.2f}"

    return f"{show(low)}{'–' if style.glyphs.dash == '—' else '-'}{show(high)}"


def _changed(field_name: str, config: dict[str, Any]) -> bool:
    return not is_default(field_name, config)


def _caption(field_name: str, config: dict[str, Any]) -> str:
    text = EXTRA_CAPTIONS.get(field_name) or (config.get("descriptions") or {}).get(field_name, "")
    defaults = config.get("defaults") or {}
    if field_name in defaults and "Default" not in text:
        text = f"{text} Default: {default_words(field_name, config)}.".strip()
    return text


# --------------------------------------------------------------------------- #
# Rows for each screen
# --------------------------------------------------------------------------- #


def rows_for(state: UIState, data: Data, style: Style) -> list[Row]:
    view = view_name(state)
    width = _left_width(state)
    builders = {
        "status": _status_rows,
        "settings": _settings_rows,
        "advanced": _advanced_rows,
        "help": _help_rows,
        "about": _about_rows,
        "setup1": _setup1_rows,
        "setup2": _setup2_rows,
        "setup3": _setup3_rows,
    }
    return builders[view](state, data, style, width)


def _key_row(state: UIState, data: Data, name: str, label: str) -> Row:
    config = data.config
    has_default = name in (config.get("defaults") or {})
    return Row(
        id=name,
        kind="key",
        label=label,
        field=name,
        value=value_of(state, data, name),
        caption=_caption(name, config),
        reset={name: config["defaults"][name]} if has_default and _changed(name, config) else None,
        reset_words=default_words(name, config) if has_default else "",
    )


def _toggle_row(state: UIState, data: Data, name: str, label: str) -> Row:
    config = data.config
    return Row(
        id=name,
        kind="toggle",
        label=label,
        field=name,
        value=bool(value_of(state, data, name)),
        caption=_caption(name, config),
    )


def _number_row(state: UIState, data: Data, name: str, label: str, live: str = "") -> Row:
    config = data.config
    shown = dict(config)
    shown[name] = value_of(state, data, name)
    if name == "threshold_percent":
        shown["threshold"] = threshold_for(float(shown[name]))
    sub = [live_line(live, shown)] if live else []
    tone = "accent"
    if name in state.inline:
        sub, tone = ["! " + state.inline[name]], "error"
    elif state.edit is not None and state.edit.field == name:
        low, high = (config.get("ranges") or {}).get(name, (1, 10))
        sub, tone = (
            [f"Type a number from {plain(low)} to {plain(high)}, then Enter. Esc cancels."],
            "dim",
        )
    return Row(
        id=name,
        kind="dial" if name == "threshold_percent" else "number",
        label=label,
        field=name,
        value=shown[name],
        sub=sub,
        sub_tone=tone,
        caption=_caption(name, config),
        reset={name: config["defaults"][name]} if _changed(name, shown) else None,
        reset_words=default_words(name, config),
    )


def _status_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    s, config, env = data.status, data.config, data.env
    running, paused = bool(s.get("running")), bool(s.get("paused"))
    word = s.get("state_word", "STOPPED")
    tone = {"RUNNING": "good", "PAUSED": "warn"}.get(word, "error")
    mode = g.dash if paused or not running else s.get("mode_word", "IDLE")
    paint_mode = {"HEAL": style.good_bold, "BOOST": style.accent_bold}.get(mode, style.dim)
    inner = width - 6
    left = f"   State   {style.badge(word, tone)}"
    right = f"Beam   {paint_mode(mode)}   "
    lines = ["", _spread(left, right, width), ""]

    health = max(0.0, min(1.0, float(s.get("health") or 0.0)))
    threshold = max(0.0, min(1.0, float(config.get("threshold") or 0.0)))
    lines.append(_spread("   Ally health match", f"{fmt3(health)}   ", width))
    filled = round(health * inner)
    tick = round(threshold * (inner - 1))
    bar = ""
    for index in range(inner):
        if index == tick:
            bar += style.warn_bold(g.tick)
        elif index < filled:
            bar += style.fill(g.fill)
        else:
            bar += style.dim(g.track)
    lines.append("   " + bar)
    label = f"switch {fmt3(threshold)}"
    if threshold > 0.70:
        text = f"{label} {g.up}"
        start = max(0, tick - cell_width(text) + 1)
    else:
        text = f"{g.up} {label}"
        start = tick
    lines.append("   " + " " * start + style.warn_bold(text))
    lines.append("")

    fps = s.get("fps_avg") or 0
    flash = (
        "off"
        if not config.get("flash_heal_enabled")
        else (s.get("flash") or g.dash)
        if running
        else g.dash
    )
    stats = [
        f"FPS {round(fps)} avg" if fps > 0 else f"FPS {g.dash}",
        f"Uptime {s.get('uptime') if running else g.dash}",
        f"Flash Heal {flash}",
    ]
    lines.append("   " + "    ".join(stats))
    lines.append("")
    rows = [_text(lines, "head")]

    if env.get("elevated") is False and (warning := elevation_warning(False)):
        rows.append(_text(_para("! " + warning, width, style.warn), "elevation"))
    problems = [s["error"]] if s.get("error") else []
    # A refused Start also lands in the loop's own error; say it once.
    problems += [p for p in state.status_problems if not any(p in shown for shown in problems)]
    if problems:
        rows.append(_text(_box("Problem", problems, width, style, style.error), "problems"))
    alert, fix = resolution_alert(config, env)
    if alert:
        rows.append(_text(_para("! " + alert, width, style.warn), "alert"))
    if s.get("live"):
        rows.append(
            _text(_para("Typing is ignored while Mercy AI is pressing keys.", width, style.dim))
        )
    if not _wide(state):
        pause = key_name(config.get("pause_toggle_button") or "f2")
        rows.append(
            _text(
                [
                    "",
                    f"   Hold {trigger_label(config.get('keybind_btn'))} in game and Mercy AI takes over.",
                    f"   Tap {pause} any time to pause everything.",
                    "",
                ],
                "hint",
            )
        )
    else:
        rows.append(_text([""]))
    rows.append(
        Row(
            id="pause",
            kind="button",
            label=("Resume" if paused else "Pause") + ("" if running else "  (only while running)"),
            hint="P",
            enabled=running,
        )
    )
    rows.append(Row(id="run", kind="button", label="Stop" if running else "Start", hint="S"))
    if fix:
        rows.append(Row(id="use_res", kind="button", label=f"Use {fix}p", hint="U"))
    return rows


def _spread(left: str, right: str, width: int) -> str:
    gap = width - cell_width(left) - cell_width(right)
    return left + " " * max(1, gap) + right


def _settings_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    config, env = data.config, data.env
    rows: list[Row] = [_text([""])]
    alert, fix = resolution_alert(config, env)
    if alert:
        rows.append(_text(_para("! " + alert, width, style.warn), "alert"))
        if fix:
            rows.append(Row(id="use_res", kind="button", label=f"Use {fix}p", hint="U"))
        rows.append(_text([""]))
    dial = _number_row(state, data, "threshold_percent", "Switch to damage boost at")
    if "threshold_percent" not in state.inline and not state.edit:
        threshold = threshold_for(float(dial.value))
        dial.sub = [
            f"1 = boost early {g.middot} 10 = only at full {g.middot} switches at match "
            f"{fmt3(threshold)}"
        ]
    rows += [dial, _text([""])]
    rows.append(_key_row(state, data, "heal_btn", "Heal key (in game)"))
    rows.append(_key_row(state, data, "damage_boost_btn", "Damage boost key (in game)"))
    rows.append(_text([""]))
    rows.append(_toggle_row(state, data, "dynamic_heal", "Smooth healing"))
    flash_on = bool(config.get("flash_heal_enabled"))
    rows.append(
        Row(
            id="flash_enabled",
            kind="toggle",
            label="Flash Heal",
            value=flash_on,
            caption=flash_caption(config)
            + (
                " Enter turns it off. Pick when it fires under Advanced."
                if flash_on
                else " Enter turns it on, after Ana's grenade and on your ally's health."
            ),
        )
    )
    rows.append(_text([""]))
    rows.append(
        Row(
            id="advanced",
            kind="button",
            label="Advanced",
            value=f"{g.right}  keys, detection, numbers",
            caption=EXTRA_CAPTIONS["advanced"],
        )
    )
    return rows


def _heading(text: str, style: Style) -> Row:
    return _text(["", "   " + style.accent(text)], f"h-{text}")


def _advanced_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    config, env = data.config, data.env
    notice = [
        "   " + style.notice(" " + fit(line, width - 8) + " ")
        for line in wrap(PRACTICE_NOTICE, width - 8)
    ]
    rows: list[Row] = [
        _text(
            [
                "   " + style.accent(f"Settings {g.right} Advanced"),
                "   "
                + style.dim("Sensible defaults. Change these only if something specific needs it."),
                *notice,
            ],
            "intro",
        )
    ]
    rows.append(_heading("CONTROLS", style))
    trigger = _key_row(state, data, "keybind_btn", "Trigger (hold to run)")
    rows.append(trigger)
    rows.append(_key_row(state, data, "pause_toggle_button", "Pause key"))
    rows.append(_key_row(state, data, "flash_heal_btn", "Flash Heal key (in game)"))
    note = resolution_note(config, env)
    rows.append(
        Row(
            id="resolution",
            kind="choice",
            label="Game resolution",
            field="resolution",
            value=value_of(state, data, "resolution"),
            options=tuple((r, r) for r in config.get("resolutions") or ()),
            sub=[note] if note else [],
            sub_tone="error" if env.get("primary") and not env.get("detected") else "dim",
            caption=_caption("resolution", config),
        )
    )

    rows.append(_heading("DETECTION", style))
    rows.append(_toggle_row(state, data, "bomb_detection", "Detect Ana's grenade"))

    rows.append(_heading("FOCUS", style))
    rows.append(_toggle_row(state, data, "focus_gate", "Pause while Overwatch is unfocused"))
    if value_of(state, data, "focus_gate"):
        rows.append(_toggle_row(state, data, "click_check", "Strict focus: check where you click"))

    rows.append(_heading("SMOOTH HEALING", style))
    if config.get("dynamic_heal"):
        rows.append(
            _number_row(
                state,
                data,
                "dynamic_heal_drop",
                "Resume healing below the switch point by",
                live="dynamic_heal_drop",
            )
        )
    else:
        rows.append(
            _text(
                _para(
                    "Smooth healing is off. Turn it on in Settings to change this.",
                    width,
                    style.dim,
                )
            )
        )

    rows.append(_heading("FLASH HEAL", style))
    if config.get("flash_heal_enabled"):
        rows.append(
            Row(
                id="flash_ana",
                kind="toggle",
                label="After Ana's grenade",
                field="flash_heal_trigger",
                value=value_of(state, data, "flash_heal_trigger") == "expiry",
                sub=[ANA_SUB],
                sub_tone="dim",
                caption=_caption("flash_heal_trigger", config),
            )
        )
        health = _toggle_row(state, data, "flash_heal_on_health_drop", "Fire on your ally's health")
        health.sub, health.sub_tone = [HEALTH_SUB], "dim"
        rows.append(health)
        if value_of(state, data, "flash_heal_on_health_drop"):
            mode = value_of(state, data, "flash_heal_health_mode")
            rows.append(
                Row(
                    id="flash_heal_health_mode",
                    kind="choice",
                    label="How",
                    field="flash_heal_health_mode",
                    value=mode,
                    options=(("emergency", "Emergency"), ("low", "Normal")),
                    sub=["Emergency: a sharp fall. Normal: below a line you set."],
                    sub_tone="dim",
                    caption=_caption("flash_heal_health_mode", config),
                )
            )
            if mode == "low":
                rows.append(
                    _number_row(
                        state,
                        data,
                        "flash_heal_low_health",
                        "Fire when health is below",
                        live="flash_heal_low_health",
                    )
                )
            else:
                rows.append(
                    _number_row(
                        state,
                        data,
                        "flash_heal_drop",
                        "Fall that counts as an emergency",
                        live="flash_heal_drop",
                    )
                )
        rows.append(_toggle_row(state, data, "flash_heal_staff_check", "Only with the staff out"))
    else:
        rows.append(
            _text(
                _para("Flash Heal is off. Turn it on in Settings to set it up.", width, style.dim)
            )
        )

    rows.append(_heading("TOOLS", style))
    rows.append(
        Row(
            id="open_folder",
            kind="button",
            label="Open folder",
            caption=EXTRA_CAPTIONS["open_folder"],
        )
    )
    rows.append(
        Row(
            id="redo_setup", kind="button", label="Redo setup", caption=EXTRA_CAPTIONS["redo_setup"]
        )
    )
    return rows


def _help_steps(data: Data, style: Style, width: int) -> list[str]:
    g = style.glyphs
    config = data.config

    def key(name: str) -> str:
        value = config.get(name)
        return style.bold(key_name(value)) if value else style.error("not set")

    lines = ["", "   " + style.bold("Three things to set in Overwatch, then hold the trigger"), ""]
    steps = [
        (
            "White health bars",
            f"Options {g.path} Video {g.path} Colour Blind {g.path} set health, shield, armour and "
            f"overhealth to white. Blue is the default and it will not match.",
        ),
        (
            "Fullscreen, on the main monitor",
            f"Options {g.path} Video {g.path} Display mode {g.path} Fullscreen, not borderless. "
            f"Overwatch must run on your primary monitor.",
        ),
        (
            "Give Mercy's beam two keys",
            f"Options {g.path} Controls {g.path} Mercy: add your heal key (\x00H) as a second key "
            f"for Primary Fire and your damage boost key (\x00B) for Secondary Fire. Keep the mouse "
            f"buttons too. You choose the keys under Settings.",
        ),
    ]
    for number, (title, body) in enumerate(steps, start=1):
        lines.append(f"   {style.accent(str(number))}  {style.bold(title)}")
        for line in wrap(body, width - 10):
            lines.append(
                "      "
                + line.replace("\x00H", key("heal_btn")).replace("\x00B", key("damage_boost_btn"))
            )
        lines.append("")
    return lines


def _help_checks(data: Data, style: Style, width: int) -> list[str]:
    lines = ["   " + style.accent("THIS COMPUTER")]
    for ok, text in help_checks(data.config, data.env):
        mark = style.good("[OK]") if ok else style.error("[!!]")
        wrapped = wrap(text, width - 10)
        lines.append(f"   {mark} {wrapped[0]}")
        lines += ["        " + more for more in wrapped[1:]]
    return lines


NOTHING_HAPPENS = (
    "Mercy AI asks for administrator rights when it starts, because Windows silently drops "
    "key presses sent to a game that runs with higher rights. If you answered No, close it "
    "and open it again. Otherwise check the three steps above, and that the resolution under "
    "Settings > Advanced matches your screen."
)


def _help_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    lines = _help_steps(data, style, width)
    if not _wide(state):
        lines += _help_checks(data, style, width)
        lines += ["", "   " + style.accent("NOTHING HAPPENS IN GAME")]
        lines += _para(NOTHING_HAPPENS, width)
        lines += ["", "   " + style.dim("Open folder  O")]
    return [_text(lines, "help")]


def _about_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    lede = (
        "A pocket-healing buddy for Mercy. It watches your ally's health ring, holds heal "
        "while they're hurt, and slips into damage boost the moment they top off "
        f"{g.dash} so you can watch the fight instead of your beam."
    )
    rows: list[Row] = [
        _text(
            [
                "",
                "   " + style.bold("Mercy AI"),
                f"   Version {data.version} {g.middot} free and open source",
                "",
            ]
            + _para(lede, width)
            + ["", "   " + style.accent("DEVELOPER")],
            "about",
        )
    ]
    for ident, label, value, url in (
        ("instagram", "Instagram", "_1_B", "instagram.com/_1_B"),
        ("github", "GitHub", "Hexer-7", "github.com/Hexer-7"),
        ("repo", "Repository", "Overwatch-Mercy-AI", "github.com/Hexer-7/Overwatch-Mercy-AI"),
    ):
        rows.append(
            Row(
                id=ident,
                kind="button",
                label=label,
                value=f"{value:<20}{style.dim(url)}",
                label_width=13,
            )
        )
    copyright_line = __copyright__.replace("Copyright (c)", g.copyright)
    rows.append(
        _text(
            [
                "",
                f"   Thx to {style.claude('Claude')}.",
                f"   {copyright_line} {g.middot} Apache License 2.0",
            ]
        )
    )
    return rows


def _setup_nav(state: UIState, style: Style, final: bool, enabled: bool) -> list[Row]:
    g = style.glyphs
    rows = [_text([""])]
    label = f"Finish setup {g.right}" if final else f"Next {g.right}"
    rows.append(Row(id="next", kind="button", label=label, enabled=enabled))
    if state.setup.step > 1:
        rows.append(Row(id="back", kind="button", label=f"{g.left} Back"))
    return rows


def _setup1_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    rows: list[Row] = []
    if error := data.env.get("config_error"):
        rows.append(
            _text(
                [""]
                + _box(
                    "config.xml",
                    [
                        f"config.xml could not be read. {error} Finishing setup writes a new one "
                        "with default settings. To keep your old settings instead, close Mercy AI, "
                        "fix the file and start it again."
                    ],
                    width,
                    style,
                    style.warn,
                ),
                "config_error",
            )
        )
    rows.append(
        _text(
            ["", "   " + style.bold("Two things about your game"), ""]
            + _para(
                "Mercy AI reads your ally's health ring off the screen. It only reads reliably if "
                "the game looks the way it expects.",
                width,
            )
            + [""]
            + _box(
                "Set the health bar to white",
                [
                    f"Options {g.path} Video {g.path} Colour Blind {g.path} set health, shield, armour "
                    "and overhealth to white. Blue is the default and it will not match."
                ],
                width,
                style,
            )
            + _box(
                "Run Overwatch fullscreen",
                [
                    "Not borderless. Borderless lets Windows shift the picture and the capture "
                    "lands in the wrong place."
                ],
                width,
                style,
            )
            + [""],
            "intro",
        )
    )
    mark = g.tick_box if state.setup.ack else g.empty_box
    rows.append(
        Row(id="ack", kind="check", label=f"{mark} Both done {g.dash} I'm good to continue")
    )
    rows += _setup_nav(state, style, final=False, enabled=state.setup.ack)
    return rows


def _setup2_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    setup = state.setup
    rows: list[Row] = [
        _text(
            ["", "   " + style.bold("Pick two spare keys"), ""]
            + _para(
                "In Overwatch, bind heal and damage boost to keyboard keys as well as the mouse. "
                f"Mercy AI presses those keys {style.glyphs.dash} it can't press your mouse buttons.",
                width,
            )
            + [""],
            "intro",
        )
    ]
    for ident, label, value in (
        ("wiz_heal", f"Heal {style.glyphs.middot} primary weapon", setup.heal),
        ("wiz_boost", f"Damage boost {style.glyphs.middot} secondary", setup.boost),
    ):
        rows.append(
            Row(
                id=ident,
                kind="key",
                label=label,
                value=value,
                hint="change" if value else "set key",
            )
        )
    problem = wizard_key_problem(data.config, setup.heal, setup.boost)
    rows.append(_text([""] + _para(wizard_key_note(data.config), width, style.dim)))
    if problem:
        rows.append(_text(_para(problem, width, style.error), "clash"))
    rows += _setup_nav(
        state, style, final=False, enabled=bool(setup.heal and setup.boost and not problem)
    )
    return rows


def _setup3_rows(state: UIState, data: Data, style: Style, width: int) -> list[Row]:
    g = style.glyphs
    env, config = data.env, data.config
    sizes = config.get("resolution_sizes") or {}
    rows: list[Row] = [
        _text(
            ["", "   " + style.bold("Your resolution"), ""]
            + _para(
                "This is your game computer's screen height in pixels, which is normally your "
                "desktop resolution.",
                width,
            )
            + [""],
            "intro",
        )
    ]
    for res in config.get("resolutions") or ():
        size = str(sizes.get(res, ""))
        label = f"{RESOLUTION_NAMES.get(res, '')} {g.middot} {res}"
        rows.append(
            Row(
                id=f"res{res}",
                kind="radio",
                label=label,
                value=res,
                hint="Detected" if env.get("detected") == res else "",
                sub=[],
                caption=size,
            )
        )
    extra: list[str] = [""]
    if env.get("primary") and not env.get("detected"):
        extra += _box(
            "Note",
            [
                f"This monitor is {env['primary']}, which matches none of these heights. Pick the "
                f"one your game computer uses {g.dash} the setting is saved either way. Mercy AI "
                "just cannot watch the screen on this machine."
            ],
            width,
            style,
            style.warn,
        )
    extra += _box(
        "Note",
        [
            f"Overwatch must run on your primary monitor {g.dash} Mercy AI measures from its "
            "top-left corner."
        ],
        width,
        style,
    )
    rows.append(_text(extra, "notes"))
    rows += _setup_nav(state, style, final=True, enabled=bool(state.setup.resolution))
    return rows


# --------------------------------------------------------------------------- #
# Drawing one row
# --------------------------------------------------------------------------- #


def _value_text(row: Row, state: UIState, data: Data, style: Style) -> str:
    g = style.glyphs
    if row.kind == "toggle":
        return style.accent_bold(g.on) if row.value else style.dim(g.off)
    if row.kind == "choice":
        parts = []
        for value, text in row.options:
            parts.append(style.bold(f"[{text}]") if value == row.value else f" {text} ")
        return " ".join(parts)
    if row.kind in ("number", "dial"):
        if state.edit is not None and state.edit.field == row.field:
            return style.bold(f"[{state.edit.text}_".ljust(8) + "]")
        try:
            number = float(row.value)
        except (TypeError, ValueError):
            number = 0.0
        shown = f"{number:.1f}" if row.kind == "dial" else f"{number:.2f}"
        paint = style.warn_bold if row.field in state.pending else style.bold
        text = f"{g.left} {paint(shown)} {g.right}"
        if row.kind == "dial":
            span = 14
            position = round((min(10.0, max(1.0, number)) - 1) / 9 * (span - 1))
            track = "".join(g.knob if i == position else g.rule for i in range(span))
            return f"{text}   1 {style.dim(track)} 10"
        return f"{text}  {style.dim(_range_text(row.field, data.config, style))}"
    if row.kind == "key":
        if not row.value:
            return style.error("not set" if state.screen == "main" else "Not set")
        return style.bold(key_name(str(row.value)))
    if row.kind == "button" and row.value:
        return str(row.value)
    return ""


def _row_lines(
    row: Row, focused: bool, state: UIState, data: Data, style: Style, width: int
) -> list[str]:
    if row.kind == "text":
        return list(row.lines)
    g = style.glyphs
    mark = g.focus if focused else " "
    if row.kind == "radio":
        radio = g.radio_on if row.value == state.setup.resolution else g.radio_off
        body = f"{radio} {row.label:<16}{row.caption}"
        if focused:
            body = style.reverse(body)
        line = f" {mark} {body}"
        if row.hint:
            line += "     " + style.accent(row.hint)
        return [line]
    if row.kind in ("button", "check") and not row.value:
        label = row.label if row.enabled else style.dim(row.label)
        body = style.reverse(row.label) if focused else label
        line = f" {mark} {body}"
    else:
        span = row.label_width
        label = truncate(row.label, span - 1)
        value = _value_text(row, state, data, style)
        padded = label + " " * max(1, span - cell_width(label))
        if focused:
            body = style.reverse(padded) + value
        else:
            body = (padded if row.enabled else style.dim(padded)) + value
        line = f" {mark} {body}"
    if row.reset is not None and state.screen == "main":
        # A star beside the value, not a word at the right edge: a word there
        # cut into the dial's track on a narrow window.
        line += " " + style.accent("*")
    right = style.dim(row.hint) if row.hint else ""
    if right:
        line = _spread(truncate(line, width - cell_width(right) - 3), right + "  ", width)
    lines = [line]
    paint = {"accent": style.accent, "dim": style.dim, "error": style.error, "warn": style.warn}[
        row.sub_tone
    ]
    for sub in row.sub:
        for part in wrap(sub, max(10, width - 10)):
            lines.append("      " + paint(part))
    return lines


# --------------------------------------------------------------------------- #
# The whole frame
# --------------------------------------------------------------------------- #


def focused_row(
    state: UIState, data: Data, style: Style, rows: list[Row] | None = None
) -> Row | None:
    rows = rows if rows is not None else rows_for(state, data, style)
    wanted = state.focus.get(view_name(state))
    focusable = [row for row in rows if row.focusable]
    for row in focusable:
        if row.id == wanted:
            return row
    return focusable[0] if focusable else None


def _header(state: UIState, data: Data, style: Style) -> list[str]:
    g = style.glyphs
    cols = state.cols
    if state.screen == "setup":
        left = " " + style.bold("Mercy AI") + f" {g.dash} setup"
        dots = "".join(
            (g.dot_on if step <= state.setup.step else g.dot_off) + " " for step in (1, 2, 3)
        )
        return [left, f" {style.accent(dots)} Step {state.setup.step} of 3", g.rule * cols]
    config, env, s = data.config, data.env, data.status
    res = f"{config.get('resolution')}p"
    if env.get("detected") and env["detected"] != config.get("resolution"):
        res = style.error(f"{res} {g.dash} screen is {env['detected']}")
    fps = s.get("fps_avg") or 0
    fps_text = f"{round(fps)} fps avg" if fps > 0 else f"{g.dash} fps"
    left = " " + style.bold("Mercy AI") + "  " + style.dim(data.version)
    header = _spread(left, f"{res} {g.middot} {fps_text} ", cols)
    tabs = []
    for index, name in enumerate(TABS):
        text = f"{index + 1} {name}"
        tabs.append(style.accent_bold(f"[{text}]") if index == state.tab else f" {text} ")
    return [header, " " + " ".join(tabs), g.rule * cols]


def _footer_items(
    state: UIState, data: Data, style: Style, row: Row | None
) -> list[tuple[str, str]]:
    g = style.glyphs
    lr, ud = g.arrows_lr, g.arrows_ud
    modal = state.modal
    if modal is not None:
        if modal.kind == "quit":
            return [(lr, "choose"), ("Enter", "confirm"), ("Y", "quit"), ("N", "stay")]
        if modal.kind == "trigger":
            return [(ud, "choose"), ("Enter", "pick"), ("Esc", "cancel")]
        return [("Esc", "cancel")]
    if state.edit is not None:
        return [("Type", "a number"), ("Backspace", "delete"), ("Enter", "save"), ("Esc", "cancel")]
    if state.screen == "setup":
        items = [(ud, "move")]
        if row is not None and row.kind == "check":
            items.append(("Enter", "tick"))
        elif row is not None and row.kind == "key":
            items.append(("Enter", "set key"))
        elif row is not None and row.kind == "radio":
            items.append(("Enter", "finish setup"))
        else:
            items.append(("Enter", "select"))
        items.append(
            ("Esc", "back" if state.setup.step > 1 else ("leave" if state.setup.redo else "quit"))
        )
        return items
    view = view_name(state)
    if view == "help":
        items = [(ud, "scroll"), (lr, "tab"), ("O", "open folder")]
    else:
        items = [(ud, "move")]
        if row is not None:
            if row.kind in ("number", "dial"):
                items += [(lr, "change"), ("Enter", "type")]
            elif row.kind in ("toggle", "choice"):
                items += [(f"{lr} or Enter", "change")]
            elif row.kind == "key":
                items += [("Enter", "change"), (lr, "tab")]
            elif view == "about":
                items += [("Enter", "open in browser"), (lr, "tab")]
            else:
                items += [("Enter", "select"), (lr, "tab")]
            if row.reset is not None:
                items.append(("R", f"reset to {row.reset_words}"))
    status = data.status
    if status.get("running"):
        items.append(("P", "resume" if status.get("paused") else "pause"))
    items.append(("S", "stop" if status.get("running") else "start"))
    if view == "advanced":
        items.append(("Esc", "back"))
    items.append(("Q", "quit"))
    return items


def _footer(state: UIState, data: Data, style: Style, row: Row | None) -> str:
    items = _footer_items(state, data, style, row)
    if state.busy:
        items = [("", "Working" + style.glyphs.ellipsis), *items]
    width = state.cols - 1
    droppable = ("tab", "start", "stop", "pause", "resume", "open folder")
    while True:
        text = " " + "  ".join(
            f"{style.dim(key)} {action}" if key else action for key, action in items
        )
        if cell_width(text) <= width:
            return text
        drop = next((i for i, (_k, a) in enumerate(items) if a in droppable), None)
        if drop is None:
            return truncate(text, width)
        items.pop(drop)


def _message(state: UIState, style: Style) -> list[str]:
    msg = state.message
    if msg is None:
        return [""]
    paint = {"good": style.good, "warn": style.warn, "error": style.error}[msg.tone]
    prefix = "! " if msg.tone == "error" else ""
    lines = wrap(prefix + msg.text, state.cols - 4)
    if len(lines) > 4:
        lines = [*lines[:3], truncate(lines[3], state.cols - 20) + " " + style.glyphs.ellipsis]
    out = [" " + paint(line) for line in lines]
    if len(out) > 1 and msg.until is None:
        out.append(" " + style.dim("Esc hides this"))
    return out


def _info_box(row: Row | None, width: int, height: int, style: Style) -> list[str]:
    g = style.glyphs
    title = row.label if row is not None else ""
    head = f" {g.rule} {title} "
    lines = [style.dim(head + g.rule * max(0, width - cell_width(head) - 1))]
    text = row.caption if row is not None else ""
    body = wrap(text, width - 6) if text else []
    if len(body) > height:
        body = body[:height]
        body[-1] = truncate(body[-1], width - 8) + " " + g.ellipsis
    lines += ["   " + line for line in body]
    return lines + [""] * (height + 1 - len(lines))


def _panel(title: str, body: list[str], style: Style) -> list[str]:
    g = style.glyphs
    inner = PANEL_WIDTH - 4
    top = f"{g.top_left}{g.rule} {truncate(title, PANEL_WIDTH - 7, g.ellipsis)} "
    top += g.rule * max(0, PANEL_WIDTH - 1 - cell_width(top)) + g.top_right
    lines = [style.dim(top)]
    for item in body:
        for line in [item] if "\x1b" in item or item.startswith(" ") else wrap(item, inner):
            lines.append(style.dim(g.vbar) + " " + fit(line, inner) + " " + style.dim(g.vbar))
    lines.append(style.dim(g.bottom_left + g.rule * (PANEL_WIDTH - 2) + g.bottom_right))
    return lines


def _right_panels(state: UIState, data: Data, style: Style, row: Row | None) -> list[str]:
    view = view_name(state)
    config = data.config
    if view == "status":
        pause = key_name(config.get("pause_toggle_button") or "f2")
        keys = [
            ("Heal", key_name(config.get("heal_btn")) or "not set"),
            ("Damage boost", key_name(config.get("damage_boost_btn")) or "not set"),
            ("Trigger", trigger_label(config.get("keybind_btn"))),
            ("Pause", pause),
            (
                "Flash Heal",
                key_name(config.get("flash_heal_btn"))
                if config.get("flash_heal_enabled")
                else "off",
            ),
        ]
        checks = []
        for ok, text in help_checks(config, data.env):
            checks.append(("[OK] " if ok else "[!!] ") + text)
        return (
            [""]
            + _panel(
                "In game",
                [
                    f"Hold {trigger_label(config.get('keybind_btn'))} and Mercy AI takes over.",
                    f"Tap {pause} any time to pause everything.",
                ],
                style,
            )
            + [""]
            + _panel("Your keys", [f"{name:<16}{value}" for name, value in keys], style)
            + [""]
            + _panel("This computer", checks, style)
        )
    if view == "help":
        return (
            [""]
            + _panel(
                "This computer",
                [("[OK] " if ok else "[!!] ") + t for ok, t in help_checks(config, data.env)],
                style,
            )
            + [""]
            + _panel("Nothing happens in game", [NOTHING_HAPPENS], style)
            + ["", style.dim("Open folder  O")]
        )
    if row is None or not row.caption:
        return []
    return [""] + _panel(row.label, [row.caption], style)


def _modal_box(state: UIState, data: Data, style: Style) -> list[str]:
    g = style.glyphs
    modal = state.modal
    assert modal is not None
    body: list[str] = []
    width = min(60, state.cols - 4)
    inner = width - 4

    def problem_lines(text: str) -> list[str]:
        return [style.error(line) for line in wrap("! " + text, inner)]

    if modal.kind == "quit":
        title = "Quit"
        if state.screen == "setup" and not state.setup.redo:
            body = ["Quit setup? Nothing is saved.", ""]
        else:
            body = ["Quit Mercy AI?", "Mercy AI stops watching and lets go of every key.", ""]
        choices = ["Quit", "Stay"]
        parts = [
            style.reverse(f"{g.focus} {text}") if index == modal.focus else f"  {text}"
            for index, text in enumerate(choices)
        ]
        body.append("        " + "         ".join(parts))
    elif modal.kind == "trigger":
        title = "Trigger (hold to run)"
        body = ["What do you hold in game to make Mercy AI work?", ""]
        current = data.config.get("keybind_btn")
        options = [*TRIGGER_CHOICES, ("key", f"A keyboard key{g.ellipsis}")]
        for index, (value, text) in enumerate(options):
            now = "  (now)" if value == current else ""
            line = f"{g.focus if index == modal.focus else ' '} {text}{now}"
            body.append(style.reverse(line) if index == modal.focus else line)
        if modal.problem:
            body += ["", *problem_lines(modal.problem)]
    else:
        title, prompt = CAPTURE_PROMPTS.get(modal.field, ("Set a key", "Press a key."))
        if modal.in_setup:
            title = "Set the heal key" if modal.field == "wiz_heal" else "Set the damage boost key"
            prompt = CAPTURE_PROMPTS[
                "heal_btn" if modal.field == "wiz_heal" else "damage_boost_btn"
            ][1]
        body = [prompt, ""]
        if modal.problem:
            body += problem_lines(modal.problem)
        else:
            body.append("Saving" + g.ellipsis if modal.saving else "Waiting for a key" + g.ellipsis)
        body += ["", "Esc cancels. Mouse buttons can't be used here."]
        if not state.modal or state.screen == "main":
            body.append("Watching is stopped while you pick a key.")
    top = f"{g.top_left}{g.rule} {title} "
    top += g.rule * max(0, width - 1 - cell_width(top)) + g.top_right
    lines = [top, g.vbar + " " * (width - 2) + g.vbar]
    for item in body:
        for line in [item] if "\x1b" in item or item.startswith(" ") else wrap(item, inner):
            lines.append(g.vbar + " " + fit(line, inner) + " " + g.vbar)
    lines.append(g.vbar + " " * (width - 2) + g.vbar)
    lines.append(g.bottom_left + g.rule * (width - 2) + g.bottom_right)
    return lines


def _too_small(state: UIState) -> list[str]:
    text = [
        "",
        " Make this window bigger.",
        "",
        f" Mercy AI needs at least {MIN_COLS} x {MIN_ROWS}. It is {state.cols} x {state.rows} now.",
        "",
        " Drag a corner of the window, or maximise it.",
        " Q quits.",
    ]
    return [truncate(line, state.cols) for line in text[: state.rows]] + [""] * max(
        0, state.rows - len(text)
    )


def render(state: UIState, data: Data, style: Style) -> Frame:
    """Exactly ``state.rows`` lines, none wider than ``state.cols`` cells."""
    cols, rows_total = state.cols, state.rows
    if cols < MIN_COLS or rows_total < MIN_ROWS:
        return Frame(_too_small(state), 0)

    rows = rows_for(state, data, style)
    focus = focused_row(state, data, style, rows)
    view = view_name(state)
    wide = _wide(state) and view in ("status", "settings", "advanced", "help")
    left_width = _left_width(state)

    head = _header(state, data, style)
    message = _message(state, style)
    footer = [style.glyphs.rule * cols, _footer(state, data, style, focus)]
    body_height = rows_total - len(head) - len(message) - len(footer)

    info: list[str] = []
    if view in ("settings", "advanced") and not wide:
        info = _info_box(focus, left_width, 7 if rows_total >= 30 else 5, style)
    list_height = max(1, body_height - len(info))

    def total(candidates: list[Row]) -> int:
        return sum(len(_row_lines(r, False, state, data, style, left_width)) for r in candidates)

    # The hint under Status is the first thing to go when space runs out.
    if total(rows) > list_height:
        rows = [row for row in rows if row.id != "hint"]
    lines: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for row in rows:
        start = len(lines)
        lines += _row_lines(row, focus is not None and row is focus, state, data, style, left_width)
        spans[row.id] = (start, len(lines))

    scroll = state.scroll.get(view, 0)
    if focus is not None and focus.id in spans:
        first, last = spans[focus.id]
        if last - scroll > list_height:
            scroll = last - list_height
        if first < scroll:
            scroll = first
            # Keep the text above the first focusable row in view when there is room.
            if not any(r.focusable for r in rows[: rows.index(focus)]):
                scroll = 0 if last <= list_height else scroll
    scroll = max(0, min(scroll, max(0, len(lines) - list_height)))
    visible = lines[scroll : scroll + list_height]
    visible += [""] * (list_height - len(visible))
    g = style.glyphs
    if scroll > 0:
        visible[0] = _spread(
            fit(visible[0], left_width - 9), style.dim(f"{g.up} more "), left_width
        )
    if scroll + list_height < len(lines):
        visible[-1] = _spread(
            fit(visible[-1], left_width - 9), style.dim(f"{g.down} more "), left_width
        )

    if wide:
        panels = _right_panels(state, data, style, focus)
        merged = []
        for index in range(list_height):
            left = fit(visible[index], left_width)
            right = panels[index] if index < len(panels) else ""
            merged.append(left + "   " + right)
        visible = merged
    body = visible + info

    if state.modal is not None:
        box = _modal_box(state, data, style)
        top = max(0, (len(body) - len(box)) // 2)
        margin = " " * max(0, (cols - cell_width(box[0])) // 2)
        for offset, line in enumerate(box):
            if top + offset < len(body):
                body[top + offset] = margin + line

    out = head + body + message + footer
    out = out[:rows_total] + [""] * max(0, rows_total - len(out))
    if style.glyphs.ellipsis != "…":
        out = [line.translate(_ASCII_FALLBACK) for line in out]
    dots = style.glyphs.ellipsis if len(style.glyphs.ellipsis) == 1 else "."
    out = [fit(line, cols, dots) for line in out[:-1]] + [fit(out[-1], cols - 1, dots)]
    return Frame(out, scroll)
