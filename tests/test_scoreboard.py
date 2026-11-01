# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The scoreboard: Flash Heal waits while its key is down, then compares.

The scoreboard covers the health ring, which reads as a fall. While its key is
held nothing fires; on release the first frame that shows the ring again is
compared with the last one before the key went down.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_weapon import _Assets, _Capture, _Controller, _State

from mercy_ai.config import Config
from mercy_ai.errors import ConfigError
from mercy_ai.flash_heal import RECHECK_WINDOW, FlashHealPlanner
from mercy_ai.loop import ControlLoop
from mercy_ai.scoreboard import ScoreboardKey, virtual_key
from mercy_ai.ui.model import settings_problems

KEYS = {"heal_btn": "p", "damage_boost_btn": "o"}
STEP = 1 / 60

# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "vk"), [("tab", 0x09), ("TAB", 0x09), ("q", 0x51), ("7", 0x37), ("f5", 0x74)]
)
def test_common_keys_need_no_windows_call(name, vk):
    def never(*_args):
        raise AssertionError("looked up a key the table covers")

    assert virtual_key(name, scan_codes=never, map_scan=never) == vk


def test_other_keys_go_through_their_scan_code():
    calls = []

    def map_scan(code, mode):
        calls.append((code, mode))
        return 0xC0

    assert virtual_key("`", scan_codes=lambda _name: (41,), map_scan=map_scan) == 0xC0
    assert calls == [(41, 3)]


def test_an_unknown_key_is_a_config_error():
    def unknown(_name):
        raise ValueError("no such key")

    with pytest.raises(ConfigError, match="scoreboard key"):
        virtual_key("nope", scan_codes=unknown, map_scan=lambda *_a: 0)


def test_held_reads_the_high_bit():
    state = {0x09: 0}
    key = ScoreboardKey("tab", poll=lambda vk: state[vk])
    assert not key.held()
    state[0x09] = -32768  # GetAsyncKeyState's SHORT with only the down bit
    assert key.held()
    state[0x09] = 1  # pressed since the last call, but up now
    assert not key.held()


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #


def _planner(mode: str = "emergency", **fields) -> FlashHealPlanner:
    config = replace(
        Config(resolution="1440", **KEYS),
        flash_heal_on_health_drop=True,
        flash_heal_health_mode=mode,
        **fields,
    )
    return FlashHealPlanner(config)


class _Run:
    """Drives a planner frame by frame, healing, at 60 frames a second."""

    def __init__(self, planner: FlashHealPlanner) -> None:
        self.planner = planner
        self.frame = 0
        self.actions = []

    def go(self, health, frames=1, *, menu=False, bomb=False, staff=True, healing=True):
        for _ in range(frames):
            self.actions.append(
                self.planner.update(
                    now=self.frame * STEP,
                    bomb_present=bomb,
                    healing=healing,
                    boosting=False,
                    health=health,
                    staff=staff,
                    menu=menu,
                )
            )
            self.frame += 1
        return self

    def presses(self, since: int = 0) -> list[int]:
        return [i for i, a in enumerate(self.actions) if a.press and i >= since]


def test_nothing_fires_or_switches_while_the_scoreboard_is_open():
    run = _Run(_planner()).go(0.9, 60)
    # The ring disappears under the scoreboard: the shape of a sudden fall.
    run.go(0.0, 60, menu=True)
    assert run.presses() == []
    assert not any(a.force_heal_mode for a in run.actions)
    assert run.planner.describe(1.5) == "held (scoreboard)"


#: Frames the scoreboard takes to fade out at 60 a second, at the default 0.1 s.
FADE = round(Config(resolution="1440", **KEYS).flash_heal_scoreboard_delay / STEP)


def _after_fade(run: _Run, released: int) -> list[int]:
    """Presses after *released*; none may land while the scoreboard fades out."""
    presses = run.presses(released)
    assert all(p >= released + FADE for p in presses), presses
    return presses


def test_a_fall_under_the_scoreboard_fires_once_it_has_faded():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 60, menu=True)
    released = run.frame
    run.go(0.3, 20)
    (fired,) = _after_fade(run, released)
    assert fired <= released + FADE + 1  # the first frame after the fade
    assert run.actions[fired].reason == "health drop"


def test_a_fading_scoreboard_is_not_taken_for_a_fall():
    # The bug this delay fixes: half gone, the scoreboard scores like a fallen
    # ring, so a full ally read as hurt the moment the key came up.
    run = _Run(_planner()).go(0.95, 60).go(0.0, 60, menu=True)
    run.go(0.3, FADE).go(0.95, 30)
    assert run.presses() == []


def test_no_fall_means_no_flash_after_the_scoreboard():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 60, menu=True).go(0.8, 60)
    assert run.presses() == []


def test_a_fall_smaller_than_the_setting_does_not_fire():
    run = _Run(_planner(flash_heal_drop=0.5)).go(0.9, 60).go(0.0, 30, menu=True)
    run.go(0.45, 30)  # fell 0.45, the setting asks for 0.5
    assert run.presses() == []


def test_pressing_the_key_again_while_it_fades_keeps_waiting():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 30, menu=True)
    run.go(0.3, FADE // 2).go(0.0, 30, menu=True)
    released = run.frame
    run.go(0.3, 20)
    assert len(_after_fade(run, released)) == 1


def test_a_ring_still_covered_after_the_fade_waits_for_the_first_visible_frame():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 30, menu=True)
    run.go(0.0, FADE + 2)  # still nothing to see two frames after the fade
    visible = run.frame
    run.go(0.3, 5)
    assert run.presses() == [visible]


def test_normal_mode_fires_after_the_fade_when_below_the_line():
    run = _Run(_planner("low", flash_heal_low_health=0.4)).go(0.9, 60).go(0.0, 30, menu=True)
    released = run.frame
    run.go(0.3, 30)
    (fired,) = _after_fade(run, released)
    assert run.actions[fired].reason == "low health"


def test_normal_mode_already_fired_for_this_dip_does_not_fire_again():
    run = _Run(_planner("low")).go(0.9, 60).go(0.3, 60)
    assert len(run.presses()) == 1
    since = run.frame
    run.go(0.0, 30, menu=True).go(0.3, 30)
    assert run.presses(since) == []


def test_letting_go_of_the_beam_under_the_scoreboard_cancels_the_comparison():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 10, menu=True)
    run.go(0.0, 10, menu=True, healing=False).go(0.0, 10, menu=True)
    run.go(0.3, 10)  # maybe another ally: nothing to compare with
    assert run.presses() == []


def test_a_weapon_icon_covered_after_the_fade_does_not_lose_the_comparison():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 30, menu=True)
    run.go(0.3, FADE + 2, staff=False)
    back = run.frame
    run.go(0.3, 5)
    assert run.presses() == [back]


def test_the_comparison_gives_up_when_the_ring_never_comes_back():
    run = _Run(_planner()).go(0.9, 60).go(0.0, 30, menu=True)
    run.go(0.0, FADE + round(RECHECK_WINDOW / STEP) + 5)
    late = run.frame
    run.go(0.3, 5)  # the window is over: an ordinary sudden fall from here on
    assert late not in run.presses()


def test_a_covered_grenade_icon_does_not_end_the_grenade():
    run = _Run(_planner(flash_heal_trigger="expiry")).go(0.3, 60)
    run.go(0.3, 10, bomb=True)
    # The scoreboard hides the icon too: it must not read as the grenade ending.
    run.go(0.0, 30, menu=True, bomb=False)
    run.go(0.3, 10, bomb=True)
    assert run.presses() == []


def test_a_press_already_down_is_released_under_the_scoreboard():
    run = _Run(_planner()).go(0.9, 60).go(0.3, 1)
    assert run.presses() == [60]  # the key is down; the scoreboard opens next frame
    run.go(0.0, 10, menu=True)
    assert any(a.release for a in run.actions[-10:])


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


class _Board:
    def __init__(self, held: bool) -> None:
        self.down = held

    def held(self) -> bool:
        return self.down


def _loop(board, *, flash: bool = True):
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
        scoreboard=board,
        clock=lambda: clock[0],
        sleep=lambda _s: None,
        # A badly hurt ally: the low-health trigger wants to fire.
        score=lambda _frame, template, _buffers: 0.1 if template == "health" else 0.0,
    )

    def run(seconds: float):
        outcomes = []
        for _ in range(round(seconds * 60)):
            outcomes.append(loop.step())
            clock[0] += STEP
        return outcomes

    return controller, run


def test_the_loop_heals_but_never_flashes_while_the_scoreboard_is_held():
    controller, run = _loop(_Board(held=True))
    outcomes = run(2.0)
    assert controller.presses == ["p"]
    assert all(o.scoreboard for o in outcomes)


def test_the_loop_fires_once_the_scoreboard_is_let_go():
    board = _Board(held=True)
    controller, run = _loop(board)
    run(2.0)
    board.down = False
    run(0.25)  # past the fade-out
    assert "r" in controller.presses


def test_with_flash_heal_off_the_key_is_never_read():
    class Untouchable:
        def held(self):
            raise AssertionError("read the scoreboard key with Flash Heal off")

    controller, run = _loop(Untouchable(), flash=False)
    run(0.5)
    assert "r" not in controller.presses


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def _flash_config(**fields) -> Config:
    return replace(
        Config(resolution="1440", **KEYS),
        flash_heal_on_health_drop=True,
        **fields,
    )


def test_the_scoreboard_key_must_not_be_a_key_mercy_ai_holds():
    (problem,) = settings_problems(_flash_config(scoreboard_btn="p"), {"scoreboard_btn"})
    assert problem == "P is already your heal key. Pick a different scoreboard key."


def test_the_scoreboard_key_only_counts_while_it_is_used():
    assert (
        settings_problems(_flash_config(scoreboard_btn="p", flash_heal_scoreboard_hold=False)) == []
    )
    off = replace(_flash_config(scoreboard_btn="p"), flash_heal_on_health_drop=False)
    assert settings_problems(off) == []


def test_turning_the_wait_on_names_where_the_key_is_set():
    (problem,) = settings_problems(
        _flash_config(scoreboard_btn="p"), {"flash_heal_scoreboard_hold"}
    )
    assert "Settings > Advanced > Flash Heal" in problem
