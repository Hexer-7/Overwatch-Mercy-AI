# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Defaults, the window's settings model, and applying settings to a live loop."""

from __future__ import annotations

import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from mercy_ai.config import Config, config_to_xml, migrate_config, read_config, write_config_atomic
from mercy_ai.config_schema import COMMENTS
from mercy_ai.flash_heal import FlashHealPlanner
from mercy_ai.state import State
from mercy_ai.ui.bridge import LINKS, Bridge
from mercy_ai.ui.model import (
    CAPTIONS,
    FIELD_TYPES,
    UI_RANGES,
    _for_humans,
    config_to_ui,
    describe_settings,
    range_problems,
    setting_defaults,
    settings_problems,
    typed_problems,
    ui_to_config,
)
from mercy_ai.ui.runner import LIVE_FIELDS, LoopRunner, StatusHub, changed_fields
from mercy_ai.ui.session import starting_config as _starting_config
from mercy_ai.winutil import SingleInstanceError, acquire_single_instance, focus_existing_window

WEB = Path(__file__).resolve().parent.parent / "mercy_ai" / "ui" / "web"

#: Keys are the player's own choice and have no default, so the tests use
#: arbitrary ones.
KEYS = {"heal_btn": "p", "damage_boost_btn": "o"}

#: The config.xml the defaults were taken from (the shipped, tuned settings),
#: with the keys above in place of the player's.
SHIPPED = {
    "mercy_bar_percentage": "10",
    "disable_mercyAI_while_overwatch_unfocused": "1",
    "check_click_target": "1",
    "pause_toggle_button": "f2",
    "keybind_btn": "left_mouse",
    "heal_btn": "p",
    "damage_boost_btn": "o",
    "resolution": "1440",
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
    "flash_heal_staff_check": "1",
    "flash_heal_weapon_threshold": "0.6",
    "flash_heal_scoreboard_hold": "1",
    "scoreboard_btn": "tab",
    "flash_heal_scoreboard_delay": "0.1",
    "theme": "light",
    "dynamic_heal": "0",
    "dynamic_heal_drop": "0.15",
    "config_version": "6",
}


def _written(config: Config, tmp_path) -> dict[str, str]:
    path = tmp_path / "config.xml"
    write_config_atomic(config_to_xml(config), path)
    return {node.tag: (node.text or "").strip() for node in ET.parse(path).getroot()}


def test_a_fresh_config_is_the_shipped_one(tmp_path):
    assert _written(Config(resolution="1440", **KEYS), tmp_path) == SHIPPED


def test_back_filled_defaults_match_the_dataclass(tmp_path):
    root = ET.Element("config")
    for tag, value in (("resolution", "1440"), *KEYS.items()):
        ET.SubElement(root, tag).text = value
    migrate_config(root)
    path = tmp_path / "config.xml"
    write_config_atomic(root, path)
    assert read_config(path) == Config(resolution="1440", **KEYS)


def test_first_run_suggests_no_keys(tmp_path):
    config, needs_setup, error = _starting_config(tmp_path / "missing.xml", "1440")
    assert needs_setup and error is None
    assert (config.heal_btn, config.damage_boost_btn) == ("", "")


# --------------------------------------------------------------------------- #
# The window's model
# --------------------------------------------------------------------------- #


def test_captions_have_no_stray_fragments():
    stray = re.compile(r"(?:^|\. )0\.(?!\d)")  # a lone "0." left behind by the filter
    for field, text in describe_settings().items():
        assert not stray.search(text), (field, text)


def test_decimal_off_values_survive_the_caption_filter():
    # Without the look-behind, "0.0 = off" is cut from its second 0, leaving
    # "... line. 0. Try 0.02."
    assert "0.0 = off" in _for_humans(COMMENTS["switch_hysteresis"])


def test_every_typed_number_says_what_to_type():
    """Each number box's caption names the range the window enforces, and the default."""
    for field, (low, high, _name) in UI_RANGES.items():
        caption = CAPTIONS[field]
        typed = re.search(r"Type ([\d.]+) to ([\d.]+)\.", caption)
        assert typed, (field, caption)
        assert (float(typed.group(1)), float(typed.group(2))) == (low, high), field
        assert "Default" in caption, field
        default = getattr(Config(resolution="1440", **KEYS), field)
        assert low <= default <= high, (field, default)


def test_the_window_cannot_write_config_only_settings():
    base = Config(resolution="1440", **KEYS)
    payload = {"bomb_threshold": 0.1, "target_fps": 5, "action_cooldown": 2, "fast_capture": False}
    assert ui_to_config(base, payload) == base
    shown = config_to_ui(base)
    assert not {"bomb_threshold", "target_fps", "action_cooldown", "fast_capture"} & set(shown)
    assert "preset" not in shown and "presets" not in shown


def test_out_of_range_numbers_are_refused_with_what_to_type():
    base = Config(resolution="1440", **KEYS)
    candidate = ui_to_config(base, {"dynamic_heal_drop": "5"})
    (problem,) = range_problems({"dynamic_heal_drop": "5"}, candidate)
    assert "0.05 to 0.5" in problem
    nan = ui_to_config(base, {"flash_heal_drop": "nan"})
    assert range_problems({"flash_heal_drop": "nan"}, nan)
    assert not range_problems({"dynamic_heal_drop": "0.2"}, replace(base, dynamic_heal_drop=0.2))


def test_a_hand_edited_value_does_not_block_other_saves():
    odd = replace(Config(resolution="1440", **KEYS), dynamic_heal_drop=0.9)  # set in config.xml
    assert not range_problems({"theme": "dark"}, replace(odd, theme="dark"))


class FakeRunner:
    def __init__(self) -> None:
        self.hub = StatusHub()
        self.restarts = 0
        self.last = None

    def restart(self, config, _assets) -> None:
        self.restarts += 1
        self.last = config


def test_bridge_refuses_a_bad_number_and_writes_nothing(tmp_path):
    path = tmp_path / "config.xml"
    base = Config(resolution="1440", **KEYS)
    runner = FakeRunner()
    bridge = Bridge(config=base, config_path=path, assets=object(), runner=runner)

    refused = bridge.save_settings({"dynamic_heal_drop": 2})
    assert not refused["ok"] and refused["config"]["dynamic_heal_drop"] == 0.15
    assert not path.exists() and runner.restarts == 0

    accepted = bridge.save_settings({"dynamic_heal_drop": 0.2})
    assert accepted["ok"] and read_config(path).dynamic_heal_drop == 0.2


def _low_planner(**fields) -> FlashHealPlanner:
    fields = {"flash_heal_on_health_drop": True, "flash_heal_health_mode": "low", **fields}
    config = replace(Config(resolution="1440", **KEYS), **fields)
    return FlashHealPlanner(config)


def _feed(planner, frames, start=0.0, step=1 / 60, bomb=False):
    """Frames of ``(healing, health)``; returns the reason for each press."""
    fired = []
    for index, (healing, health) in enumerate(frames):
        action = planner.update(
            now=start + index * step,
            bomb_present=bomb,
            healing=healing,
            boosting=False,
            health=health,
        )
        if action.press:
            fired.append(action.reason)
    return fired


def test_low_health_waits_for_the_beam_to_connect():
    planner = _low_planner()
    # The ring sweeps up from empty as the beam connects: low for the first
    # half second, which must not fire.
    assert _feed(planner, [(True, 0.1)] * 25) == []
    assert _feed(planner, [(True, 0.1)] * 30, start=25 / 60) == ["low health"]


def test_low_health_fires_once_per_dip_and_again_after_recovery():
    planner = _low_planner()
    frames = [(True, 0.9)] * 60 + [(True, 0.3)] * 120 + [(True, 0.9)] * 60 + [(True, 0.3)] * 60
    assert _feed(planner, frames) == ["low health", "low health"]


def test_low_health_ignores_a_covered_ring_and_a_released_trigger():
    planner = _low_planner()
    frames = [(True, 0.9)] * 60 + [(True, 0.0)] * 60 + [(False, 0.0)] * 30
    assert _feed(planner, frames) == []


def test_low_health_is_blind_to_the_emergency_fall_rule():
    # A slow slide below the line fires in normal mode but never as an emergency.
    slide = [(True, 0.9 - 0.002 * i) for i in range(300)]
    assert _feed(_low_planner(), slide) == ["low health"]
    emergency = _low_planner(flash_heal_health_mode="emergency")
    assert _feed(emergency, slide) == []


def _actions(planner, frames, start=0.0, step=1 / 60):
    """Frames of ``(bomb_present, health)`` while healing; returns every action."""
    return [
        planner.update(
            now=start + index * step,
            bomb_present=bomb,
            healing=True,
            boosting=False,
            health=health,
        )
        for index, (bomb, health) in enumerate(frames)
    ]


#: Healthy long enough for the ring to settle, then a fall of 0.6 over 0.2 s.
_FALL = [0.9] * 60 + [0.9 - 0.05 * i for i in range(1, 13)] + [0.3] * 30


@pytest.mark.parametrize("mode", ["low", "emergency"])
def test_the_health_triggers_wait_out_the_grenade(mode):
    planner = _low_planner(flash_heal_health_mode=mode)
    under = _actions(planner, [(True, health) for health in _FALL])
    # Not even a request for heal mode: that would drop the grenade's boost.
    assert not any(action.press or action.force_heal_mode for action in under)


def test_low_health_fires_once_the_grenade_wears_off():
    planner = _low_planner()
    assert _feed(planner, [(True, 0.1)] * 120, bomb=True) == []
    assert _feed(planner, [(True, 0.1)] * 3, start=2.0) == ["low health"]


def test_a_health_drop_after_the_grenade_wears_off_fires():
    planner = _low_planner(flash_heal_health_mode="emergency")
    frames = [(True, health) for health in _FALL] + [(False, 0.9)] * 60
    frames += [(False, health) for health in _FALL[60:]]
    actions = _actions(planner, frames)
    assert [action.reason for action in actions if action.press] == ["health drop"]
    assert actions.index(next(a for a in actions if a.press)) > len(_FALL)


def test_the_first_unconfirmed_grenade_frame_already_holds_the_fire():
    planner = _low_planner(flash_heal_health_mode="emergency")
    assert not any(a.press for a in _actions(planner, [(False, h) for h in _FALL[:69]]))
    # The fall completes on the very frame the icon first shows up.
    (action,) = _actions(planner, [(True, 0.3)], start=69 / 60)
    assert not action.press and not action.force_heal_mode


def test_an_old_timer_trigger_is_read_as_the_grenade_trigger(tmp_path):
    path = tmp_path / "config.xml"
    write_config_atomic(config_to_xml(Config(resolution="1440", **KEYS)), path)
    text = path.read_text(encoding="utf-8").replace(
        "<flash_heal_trigger>off<", "<flash_heal_trigger>timer<"
    )
    path.write_text(text, encoding="utf-8")
    assert read_config(path).flash_heal_trigger == "expiry"


def test_flash_heal_and_smooth_healing_are_off_by_default():
    config = Config(resolution="1440", **KEYS)
    assert not config.flash_heal_enabled
    assert not config.dynamic_heal


@pytest.mark.parametrize("raw", ["", "  ", "abc", "0.2.", None])
def test_a_box_that_is_not_a_number_says_what_to_type(raw):
    (problem,) = typed_problems({"dynamic_heal_drop": raw})
    assert problem.startswith("Resume healing below the switch point by:")
    assert "0.05 to 0.5" in problem and "dot for decimals" in problem
    assert "dynamic_heal_drop" not in problem


def test_every_typed_maximum_can_still_fire_flash_heal():
    # A fall only counts if it ends above flash_heal_blank_floor; the live line
    # starts its example at 0.900, so the largest typeable drop must end above it.
    floor = Config(resolution="1440", **KEYS).flash_heal_blank_floor
    assert 0.9 - UI_RANGES["flash_heal_drop"][1] > floor


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"heal_btn": "f2"}, "F2 is already your pause key. Pick a different heal key."),
        ({"pause_toggle_button": "p"}, "P is already your heal key. Pick a different pause key."),
        ({"keybind_btn": "f2"}, "F2 is already your pause key. Pick a different trigger."),
        (
            {"flash_heal_btn": "o", "flash_heal_trigger": "expiry"},
            "O is already your damage boost key. Pick a different Flash Heal key.",
        ),
        (
            {"damage_boost_btn": "p"},
            "P is already your heal key. Pick a different damage boost key.",
        ),
    ],
)
def test_a_key_clash_names_the_key_the_player_just_chose(change, expected):
    clash = replace(Config(resolution="1440", **KEYS), **change)
    assert settings_problems(clash, set(change)) == [expected]


def test_the_wizard_words_both_keys_together():
    same = replace(Config(resolution="1440", **KEYS), damage_boost_btn="p")
    (problem,) = settings_problems(same, {"heal_btn", "damage_boost_btn", "resolution"})
    assert problem == "Heal and damage boost are both P. Pick two different keys."


def test_the_flash_heal_key_only_counts_while_flash_heal_is_on():
    off = replace(
        Config(resolution="1440", **KEYS),
        heal_btn="r",
        flash_heal_trigger="off",
        flash_heal_on_health_drop=False,
    )
    assert settings_problems(off, {"heal_btn"}) == []
    on = replace(off, flash_heal_trigger="expiry", flash_heal_on_health_drop=True)
    (problem,) = settings_problems(on, {"flash_heal_trigger", "flash_heal_on_health_drop"})
    assert problem.startswith("R is already your heal key. Pick a different Flash Heal key")
    assert "Settings > Advanced > Controls" in problem


def test_clash_messages_never_use_setting_names():
    clash = replace(Config(resolution="1440", **KEYS), heal_btn="f2", damage_boost_btn="r")
    for text in settings_problems(clash) + settings_problems(clash, {"heal_btn"}):
        assert "config.xml" not in text and "_btn" not in text and "_button" not in text


def test_config_comments_match_the_window():
    assert "below 0.5" in COMMENTS["flash_heal_trigger"]
    assert "0.1 to 0.85" in COMMENTS["flash_heal_drop"]


def test_a_hand_edit_of_config_xml_survives_a_save_from_the_window(tmp_path):
    path = tmp_path / "config.xml"
    started = Config(resolution="1440", **KEYS)
    write_config_atomic(config_to_xml(started), path)
    runner = FakeRunner()
    bridge = Bridge(config=started, config_path=path, assets=object(), runner=runner)

    # While the window is open, the player sets grenade confidence by hand.
    write_config_atomic(config_to_xml(replace(started, bomb_threshold=0.6)), path)
    assert bridge.save_settings({"theme": "dark"})["ok"]

    saved = read_config(path)
    assert (saved.bomb_threshold, saved.theme) == (0.6, "dark")
    assert runner.last.bomb_threshold == 0.6


@pytest.mark.parametrize("mistake", ["0,65", "nan", "inf"])
def test_a_hand_edit_with_a_mistake_is_reported_and_kept(tmp_path, mistake):
    path = tmp_path / "config.xml"
    started = Config(resolution="1440", **KEYS)
    write_config_atomic(config_to_xml(started), path)
    bridge = Bridge(config=started, config_path=path, assets=object(), runner=FakeRunner())

    text = path.read_text(encoding="utf-8").replace(
        "<ana_bomb_threshold>0.74<", f"<ana_bomb_threshold>{mistake}<"
    )
    path.write_text(text, encoding="utf-8")
    result = bridge.save_settings({"theme": "dark"})

    assert not result["ok"]
    assert result["problems"][0].startswith("Nothing was saved, because config.xml has a mistake")
    assert path.read_text(encoding="utf-8") == text


def test_setup_may_replace_an_unreadable_file(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text("<config><heal_btn>", encoding="utf-8")
    blank, needs_setup, error = _starting_config(path, "1440")
    assert needs_setup and error
    bridge = Bridge(
        config=blank,
        config_path=path,
        assets=object(),
        runner=FakeRunner(),
        environment={"needs_setup": True},
    )
    assert bridge.save_settings({**KEYS, "resolution": "1440"})["ok"]
    assert read_config(path).heal_btn == "p"


def test_an_unknown_encoding_is_a_readable_mistake(tmp_path):
    path = tmp_path / "config.xml"
    started = Config(resolution="1440", **KEYS)
    write_config_atomic(config_to_xml(started), path)
    bridge = Bridge(config=started, config_path=path, assets=object(), runner=FakeRunner())
    # A name no platform knows ("ansi" is a real alias on Windows).
    text = path.read_text(encoding="utf-8").replace(
        "encoding='utf-8'", "encoding='no-such-encoding'"
    )
    path.write_text(text, encoding="utf-8")

    result = bridge.save_settings({"theme": "dark"})  # must not raise across the bridge
    assert not result["ok"] and "config.xml has a mistake" in result["problems"][0]
    assert _starting_config(path, "1440")[1] is True  # opens setup rather than crashing


def test_finishing_setup_clears_the_old_read_error(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text("<config><heal_btn>", encoding="utf-8")
    blank, _needs_setup, error = _starting_config(path, "1440")
    env = {"needs_setup": True, "config_error": error}
    bridge = Bridge(
        config=blank, config_path=path, assets=object(), runner=FakeRunner(), environment=env
    )
    assert bridge.save_settings({**KEYS, "resolution": "1440"})["ok"]
    assert bridge.get_state()["env"]["config_error"] is None
    assert bridge.get_state()["env"]["needs_setup"] is False


# --------------------------------------------------------------------------- #
# Reset buttons
# --------------------------------------------------------------------------- #

#: The settings only the player can choose: no default, so no Reset.
PLAYERS_OWN = {"heal_btn", "damage_boost_btn", "resolution"}


def test_every_shown_setting_but_the_players_own_has_a_default():
    defaults = setting_defaults()
    assert set(defaults) == set(FIELD_TYPES) - PLAYERS_OWN
    fresh = Config(resolution="1440", **KEYS)
    assert all(getattr(fresh, field) == value for field, value in defaults.items())
    assert config_to_ui(fresh)["defaults"] == defaults


#: The settings with a Reset button: every number and key that has a default.
#: A switch or a choice is put back by clicking it again.
RESETTABLE = {
    field
    for field in setting_defaults()
    if FIELD_TYPES[field] is float or field.endswith(("_btn", "_button"))
}


def test_every_number_and_key_with_a_default_has_a_reset_button_and_no_other_does():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    covered = {
        field for group in re.findall(r'data-reset="([^"]+)"', html) for field in group.split()
    }
    assert covered == RESETTABLE
    assert not covered & PLAYERS_OWN
    assert "dynamic_heal" not in covered and "theme" not in covered


def test_resetting_everything_gives_the_fresh_config(tmp_path):
    path = tmp_path / "config.xml"
    changed = replace(
        Config(resolution="1440", **KEYS),
        keybind_btn="ctrl",
        pause_toggle_button="f3",
        flash_heal_btn="t",
        threshold_percent=6.0,
        flash_heal_low_health=0.6,
        flash_heal_drop=0.4,
        dynamic_heal_drop=0.3,
    )
    write_config_atomic(config_to_xml(changed), path)
    bridge = Bridge(config=changed, config_path=path, assets=object(), runner=FakeRunner())
    for field in RESETTABLE:  # one Reset click at a time
        assert bridge.save_settings({field: setting_defaults()[field]})["ok"], field
    assert read_config(path) == Config(resolution="1440", **KEYS)


# --------------------------------------------------------------------------- #
# About
# --------------------------------------------------------------------------- #


def test_about_links_open_only_the_three_known_pages():
    opened: list[str] = []
    bridge = Bridge(
        config=Config(resolution="1440", **KEYS),
        config_path=Path("config.xml"),
        assets=object(),
        runner=FakeRunner(),
        url_opener=opened.append,
    )
    for name in ("instagram", "github", "repo"):
        assert bridge.open_link(name)["ok"]
    assert opened == [
        "https://www.instagram.com/_1_B/",
        "https://github.com/Hexer-7",
        "https://github.com/Hexer-7/Overwatch-Mercy-AI",
    ]
    for name in ("https://example.com", "", None, "../repo"):
        assert not bridge.open_link(name)["ok"]
    assert len(opened) == 3


def test_a_link_that_cannot_open_says_so():
    def broken(_url):
        raise OSError("no browser")

    bridge = Bridge(
        config=Config(resolution="1440", **KEYS),
        config_path=Path("config.xml"),
        assets=object(),
        runner=FakeRunner(),
        url_opener=broken,
    )
    result = bridge.open_link("repo")
    assert not result["ok"] and "no browser" in result["problems"][0]


def test_about_shows_the_developer_instead_of_fair_play():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    about = html[html.index('id="tabAbout"') : html.index('id="statusBar"')]
    assert "Fair play" not in about
    assert re.findall(r'data-link="(\w+)"', about) == list(LINKS)
    claude = 'Thx to <span class="claude-name">Claude</span>.'
    for text in ("_1_B", "Hexer-7", "Overwatch-Mercy-AI", claude):
        assert text in about, text


def test_status_has_no_flash_counter():
    assert "flashes" not in StatusHub().snapshot()


def test_the_window_uses_only_the_plain_name_and_suggests_no_keys():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    # Every capitalised word after "Mercy AI" would be an edition name.
    assert set(re.findall(r"Mercy AI(?: [A-Z][a-z]+)?\b", html + js)) == {"Mercy AI"}
    assert "edition" not in (html + js).lower()
    for text in (html, js):
        assert "8 and 9" not in text
    assert all(field in FIELD_TYPES for field in UI_RANGES)


# --------------------------------------------------------------------------- #
# Applying settings to a running loop
# --------------------------------------------------------------------------- #


class FakeLoop:
    def __init__(self, config):
        self.config = config
        self.state = State()

    def run(self):
        self.state.shutdown.wait(5)


@pytest.fixture
def runner():
    builds: list[FakeLoop] = []

    def build(config, _assets, _hub):
        loop = FakeLoop(config)
        builds.append(loop)
        return loop, lambda: None

    instance = LoopRunner(Config(resolution="1440", **KEYS), object(), build=build)
    instance.start()
    yield instance, builds
    instance.stop()


def test_live_settings_do_not_rebuild_the_loop(runner):
    instance, builds = runner
    new = replace(instance.config, threshold_percent=6.5, theme="dark", dynamic_heal_drop=0.2)
    assert changed_fields(instance.config, new) <= LIVE_FIELDS
    instance.restart(new, instance.assets)
    assert len(builds) == 1
    assert builds[0].config is new
    assert instance.alive


def test_key_changes_rebuild_the_loop(runner):
    instance, builds = runner
    instance.restart(replace(instance.config, heal_btn="k"), instance.assets)
    assert len(builds) == 2
    assert builds[1].config.heal_btn == "k"


def test_a_rebuild_keeps_the_pause(runner):
    instance, builds = runner
    assert instance.toggle_pause() is True
    instance.restart(replace(instance.config, flash_heal_btn="t"), instance.assets)
    assert len(builds) == 2
    assert builds[1].state.is_paused
    assert instance.hub.paused


def test_a_new_resolution_always_rebuilds(runner):
    instance, builds = runner
    instance.restart(replace(instance.config, threshold_percent=5.0), object())
    assert len(builds) == 2


def _loop_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "mercy-ai-loop" and t.is_alive()]


def test_overlapping_restarts_never_orphan_a_loop():
    """A quick double-click on a switch is two restarts on two threads at once.

    Unserialised, both would stop the same loop and both start one, leaving
    the first out of reach: still running after Stop, with its own mouse hook
    and keys, and no Pause or F2 that could reach it.
    """
    for attempt in range(10):
        builds: list[FakeLoop] = []

        def build(config, _assets, _hub, builds=builds):
            loop = FakeLoop(config)
            builds.append(loop)
            return loop, lambda: None

        instance = LoopRunner(Config(resolution="1440", **KEYS), object(), build=build)
        instance.start()
        go = threading.Event()

        def restart(key, instance=instance, go=go):
            go.wait()
            instance.restart(replace(instance.config, heal_btn=key), instance.assets)

        threads = [threading.Thread(target=restart, args=(key,)) for key in ("k", "l")]
        for thread in threads:
            thread.start()
        go.set()
        for thread in threads:
            thread.join()
        instance.stop()
        orphans = _loop_threads()
        for loop in builds:  # tidy up before failing, so later tests start clean
            loop.state.shutdown.set()
        assert not orphans, f"attempt {attempt}: a loop survived Stop"


def test_start_after_a_stop_while_paused_is_not_paused(runner):
    instance, _builds = runner
    assert instance.toggle_pause() is True
    instance.stop()
    instance.start()
    assert instance.hub.snapshot()["state_word"] == "RUNNING"
    assert instance.toggle_pause() is True  # the button says Pause, and pauses


# --------------------------------------------------------------------------- #
# Second launch
# --------------------------------------------------------------------------- #


class FakeUser32:
    """Top-level windows as (hwnd, class name, title)."""

    def __init__(self, windows, minimised: bool = False) -> None:
        self.windows = {hwnd: (cls, title) for hwnd, cls, title in windows}
        self.minimised = minimised
        self.calls: list[str] = []

    def FindWindowW(self, _cls, title):
        self.calls.append(f"find:{title}")
        return next((h for h, (_c, t) in self.windows.items() if t == title), 0)

    def GetClassNameW(self, hwnd, buffer, _size):
        buffer.value = self.windows[hwnd][0]

    def GetWindowTextW(self, hwnd, buffer, _size):
        buffer.value = self.windows[hwnd][1]

    def IsWindowVisible(self, _hwnd):
        return True

    def EnumWindows(self, callback, param):
        for hwnd in self.windows:
            if not callback(hwnd, param):
                break

    def IsIconic(self, _hwnd):
        return self.minimised

    def ShowWindow(self, hwnd, _cmd):
        self.calls.append(f"restore:{hwnd}")

    def SetForegroundWindow(self, hwnd):
        self.calls.append(f"front:{hwnd}")


WINDOW = (42, "WindowsForms10.Window.8.app.0.1", "Mercy AI")


def test_second_launch_brings_the_window_forward():
    user32 = FakeUser32([WINDOW], minimised=True)
    assert focus_existing_window(user32=user32) is True
    assert user32.calls == ["find:Mercy AI", "restore:42", "front:42"]


def test_second_launch_with_no_window_reports_it():
    assert focus_existing_window(user32=FakeUser32([])) is False


def test_an_explorer_folder_named_mercy_ai_is_not_the_program():
    explorer = (7, "CabinetWClass", "Mercy AI")
    user32 = FakeUser32([explorer, WINDOW])
    assert focus_existing_window(user32=user32) is True
    assert user32.calls[-1] == "front:42"
    assert focus_existing_window(user32=FakeUser32([explorer])) is False


def test_the_text_interface_is_found_behind_an_administrator_prefix():
    console = (9, "ConsoleWindowClass", "Administrator: Mercy AI")
    user32 = FakeUser32([console])
    assert focus_existing_window(user32=user32) is True
    assert user32.calls[-1] == "front:9"


@pytest.mark.parametrize("error", [183, 5])
def test_a_second_copy_is_refused_even_without_access(error):
    with pytest.raises(SingleInstanceError):
        acquire_single_instance(lambda: (None, error))


def test_the_region_explanation_follows_a_new_resolution(tmp_path, monkeypatch):
    path = tmp_path / "config.xml"
    config = Config(resolution="1440", **KEYS)
    write_config_atomic(config_to_xml(config), path)
    runner = FakeRunner()
    bridge = Bridge(
        config=config, config_path=path, assets=object(), runner=runner, loader=lambda _r: object()
    )
    runner.hub.fail("BitBlt failed")
    monkeypatch.setattr(Bridge, "_region_problem", lambda self: f"set to {self._config.resolution}")
    assert "set to 1440" in bridge.get_status()["error"]
    assert bridge.save_settings({"resolution": "1080"})["ok"]
    assert "set to 1080" in bridge.get_status()["error"]
