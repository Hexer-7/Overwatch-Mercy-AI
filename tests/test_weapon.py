# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The staff check: Flash Heal is pressed only while the staff is in Mercy's hand.

With the blaster out its key reloads the gun, and on another hero it is that
hero's key, so anything but a clear staff holds Flash Heal back.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np
import pytest

from mercy_ai.assets import load_assets
from mercy_ai.config import Config
from mercy_ai.flash_heal import FlashHealPlanner, FlashState
from mercy_ai.loop import ControlLoop
from mercy_ai.paths import RESOLUTIONS
from mercy_ai.vision import imread_checked
from mercy_ai.weapon import (
    READ_INTERVAL,
    STAFF_HOLD,
    StaffWatch,
    WeaponReading,
    edge_map,
    read_weapon,
)

KEYS = {"heal_btn": "p", "damage_boost_btn": "o"}
THRESHOLD = Config(resolution="1440", **KEYS).flash_heal_weapon_threshold


def _icons(resolution: str):
    icons = load_assets(resolution).weapon
    assert icons is not None
    return icons


# --------------------------------------------------------------------------- #
# Reading the icon
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("resolution", RESOLUTIONS)
def test_every_resolution_ships_both_icons_and_tells_them_apart(resolution, data_dir):
    icons = _icons(resolution)
    blaster = read_weapon(imread_checked(data_dir / resolution / "blaster.png"), icons)
    staff = read_weapon(imread_checked(data_dir / resolution / "staff.png"), icons)
    assert blaster.describe(THRESHOLD) == "blaster"
    assert staff.describe(THRESHOLD) == "staff"


def test_a_bgra_grab_reads_like_a_bgr_one(data_dir):
    frame = imread_checked(data_dir / "1440" / "staff.png")
    bgra = np.dstack([frame, np.full(frame.shape[:2], 255, np.uint8)])
    icons = _icons("1440")
    assert read_weapon(bgra, icons) == read_weapon(frame, icons)


@pytest.mark.parametrize("value", [0, 120, 255])
def test_a_blank_region_is_neither_weapon(value):
    icons = _icons("1440")
    frame = np.full((icons.region.height, icons.region.width, 4), value, np.uint8)
    assert read_weapon(frame, icons).describe(THRESHOLD) == "none"


def test_random_noise_is_neither_weapon(rng):
    icons = _icons("1440")
    for _ in range(20):
        frame = rng.integers(0, 256, (icons.region.height, icons.region.width, 3), np.uint8)
        assert read_weapon(frame, icons).describe(THRESHOLD) == "none"


def test_the_edge_map_sees_outlines_not_brightness():
    white = np.full((5, 7, 3), 255, np.uint8)
    # A flat region has no outline, however bright it is.
    assert not edge_map(white).any()
    # A grey line on white is an outline on both of its sides.
    white[:, 3] = 120
    edges = edge_map(white)
    assert edges[2].tolist() == [0, 0, 135, 0, 135, 0, 0]


def test_a_tie_is_neither_weapon():
    assert WeaponReading(blaster=0.8, staff=0.8).describe(0.6) == "none"
    assert WeaponReading(blaster=0.79, staff=0.8).describe(0.6) == "staff"
    assert WeaponReading(blaster=0.8, staff=0.79).describe(0.6) == "blaster"
    assert WeaponReading(blaster=0.1, staff=0.59).describe(0.6) == "none"


# --------------------------------------------------------------------------- #
# The watch: how often it looks, and how long a sighting counts
# --------------------------------------------------------------------------- #

STAFF = WeaponReading(blaster=0.3, staff=0.9)
BLASTER = WeaponReading(blaster=0.9, staff=0.3)
NEITHER = WeaponReading(blaster=0.2, staff=0.2)


class _Script:
    """A fake ``read`` that plays back readings, one per read, the last one repeating."""

    def __init__(self, *readings: WeaponReading) -> None:
        self.readings = list(readings)
        self.reads = 0

    def __call__(self, _frame, _icons) -> WeaponReading:
        reading = self.readings[min(self.reads, len(self.readings) - 1)]
        self.reads += 1
        return reading


def _watch(script: _Script) -> StaffWatch:
    return StaffWatch(_icons("1440"), lambda: None, threshold=THRESHOLD, read=script)


def test_the_icon_is_read_at_most_every_interval():
    script = _Script(STAFF)
    watch = _watch(script)
    for index in range(10):
        watch.staff_out(index * READ_INTERVAL / 4)
    assert script.reads == 3  # at 0, 1 and 2 intervals


def test_nothing_seen_is_no_staff():
    assert not _watch(_Script(NEITHER)).staff_out(0.0)


def test_one_unreadable_frame_does_not_drop_the_staff():
    watch = _watch(_Script(STAFF, NEITHER))
    assert watch.staff_out(0.0)
    # Gone from view, but not for long enough yet.
    assert watch.staff_out(STAFF_HOLD - 0.01)
    assert not watch.staff_out(STAFF_HOLD + 0.01)


def test_the_blaster_ends_the_staff_at_once():
    watch = _watch(_Script(STAFF, BLASTER))
    assert watch.staff_out(0.0)
    assert not watch.staff_out(READ_INTERVAL)


def test_confirm_reads_again_unless_it_just_did():
    script = _Script(STAFF, BLASTER)
    watch = _watch(script)
    assert watch.staff_out(0.0)
    assert watch.confirm(0.001)  # too soon to bother: same reading
    assert script.reads == 1
    assert not watch.confirm(0.01)  # a fresh look finds the blaster
    assert script.reads == 2


def test_reset_forgets_a_sighting():
    watch = _watch(_Script(STAFF, NEITHER))
    assert watch.staff_out(0.0)
    watch.reset()
    assert not watch.staff_out(0.01)


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #


def _low_planner() -> FlashHealPlanner:
    config = replace(
        Config(resolution="1440", **KEYS),
        flash_heal_on_health_drop=True,
        flash_heal_health_mode="low",
    )
    return FlashHealPlanner(config)


def _feed(planner, frames, start=0.0, step=1 / 60):
    """Frames of ``(staff, health)`` while healing; returns the press times."""
    pressed = []
    for index, (staff, health) in enumerate(frames):
        now = start + index * step
        action = planner.update(
            now=now,
            bomb_present=False,
            healing=True,
            boosting=False,
            health=health,
            staff=staff,
        )
        if action.press:
            pressed.append(now)
    return pressed


def test_nothing_fires_without_the_staff():
    planner = _low_planner()
    # With the blaster out, or on another hero, there is no beam ring, so the
    # health reads as empty: exactly what would fire the low-health trigger.
    assert _feed(planner, [(False, 0.0)] * 300) == []
    assert planner.describe(5.0) == "held (no staff)"


def test_back_on_the_staff_the_beam_settles_again_before_firing():
    planner = _low_planner()
    frames = [(False, 0.1)] * 120 + [(True, 0.1)] * 120
    (fired,) = _feed(planner, frames)
    # Not on the first staff frame: the half-second wait starts over there.
    assert fired >= 120 / 60 + 0.5
    assert planner.describe(fired) != "held (no staff)"


def test_a_veto_leaves_nothing_to_release_and_no_cooldown():
    planner = _low_planner()
    (fired,) = _feed(planner, [(True, 0.1)] * 60)
    planner.veto()
    assert planner.state is FlashState.IDLE
    later = _feed(planner, [(True, 0.1)] * 2, start=fired + 1 / 60)
    assert later == []  # still standing down: the beam has to settle again
    # Nothing was pressed, so no release is ever asked for.
    action = planner.update(
        now=fired + 0.1, bomb_present=False, healing=True, boosting=False, health=0.1
    )
    assert not action.release


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


class _State:
    def __init__(self) -> None:
        self.run = threading.Event()
        self.run.set()
        self.paused = threading.Event()
        self.shutdown = threading.Event()


class _Controller:
    def __init__(self) -> None:
        self.held: set[str] = set()
        self.presses: list[str] = []

    def press(self, key: str) -> None:
        self.held.add(key)
        self.presses.append(key)

    def release(self, key: str) -> None:
        self.held.discard(key)

    def release_all(self) -> None:
        self.held.clear()


class _Capture:
    def frames(self):
        return None, None


class _Assets:
    health = "health"
    bomb = "bomb"
    health_buffers = bomb_buffers = None


class _Weapon:
    """A fake StaffWatch: separate answers for the routine look and the last one."""

    def __init__(self, routine: bool, last: bool) -> None:
        self.routine, self.last = routine, last
        self.looks = 0

    def staff_out(self, _now: float) -> bool:
        self.looks += 1
        return self.routine

    def confirm(self, _now: float) -> bool:
        self.looks += 1
        return self.last

    def reset(self) -> None:
        pass


def _run(weapon, *, flash: bool = True, seconds: float = 2.0):
    clock = [0.0]
    config = Config(resolution="1440", **KEYS)
    if flash:
        config = replace(config, flash_heal_on_health_drop=True, flash_heal_health_mode="low")
    controller = _Controller()
    loop = ControlLoop(
        assets=_Assets(),
        config=config,
        state=_State(),
        controller=controller,
        capture=_Capture(),
        heal_key="p",
        boost_key="o",
        flash_key="r",
        weapon=weapon,
        clock=lambda: clock[0],
        sleep=lambda _s: None,
        # A badly hurt ally and no grenade: the low-health trigger will want to fire.
        score=lambda _frame, template, _buffers: 0.1 if template == "health" else 0.0,
    )
    outcomes = []
    for _ in range(round(seconds * 60)):
        outcomes.append(loop.step())
        clock[0] += 1 / 60
    return controller, outcomes


def test_the_loop_presses_flash_heal_with_the_staff_out():
    controller, outcomes = _run(_Weapon(routine=True, last=True))
    assert "r" in controller.presses
    assert not any(o.flash_held or o.no_staff for o in outcomes)


def test_the_loop_never_presses_flash_heal_without_the_staff():
    controller, outcomes = _run(_Weapon(routine=False, last=False))
    assert "r" not in controller.presses
    assert all(o.no_staff for o in outcomes)


def test_the_last_look_catches_a_swap_the_routine_check_missed():
    controller, outcomes = _run(_Weapon(routine=True, last=False))
    assert "r" not in controller.presses
    held = [o for o in outcomes if o.flash_held]
    assert held and not any(o.flash_fired for o in held)


def test_with_no_watch_flash_heal_works_as_before():
    controller, _ = _run(None)
    assert "r" in controller.presses


def test_with_flash_heal_off_the_weapon_is_never_looked_at():
    weapon = _Weapon(routine=True, last=True)
    controller, _ = _run(weapon, flash=False)
    assert weapon.looks == 0
    assert "r" not in controller.presses


def test_calibrate_says_whether_flash_heal_may_fire():
    from mercy_ai.diagnostics import render_calibration_line

    config = Config(resolution="1440", **KEYS)
    assert "weapon" not in render_calibration_line(0.5, 0.0, config)
    staff = render_calibration_line(0.5, 0.0, config, weapon=STAFF)
    assert staff.endswith("weapon=staff   R allowed")
    blaster = render_calibration_line(0.5, 0.0, config, weapon=BLASTER)
    assert blaster.endswith("weapon=blaster R held")
    neither = render_calibration_line(0.5, 0.0, config, weapon=NEITHER)
    assert neither.endswith("weapon=none    R held")
