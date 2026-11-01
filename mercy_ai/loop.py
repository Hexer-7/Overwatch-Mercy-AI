# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The control loop: capture the screen, score it, and drive the beam keys.

Key behaviour that looks like a bug but is intended
---------------------------------------------------
The heal key is pressed once and then held for the whole time the trigger is
down, and damage boost is pressed *on top of it* without releasing heal.
Switching back to healing only releases damage boost, so both keys come up only
when the trigger is released.  This is how the beam behaves correctly in game;
do not "fix" it.

The action cooldown (``config.action_cooldown``) restarts only on a *press*,
never on the damage-boost release, so a boost-to-heal transition with heal
already held may switch straight back on the next frame.  A full health bar
decides the boost on its own, without consulting the Ana-bomb score.

Flash Heal is pressed only while the staff is out: with the blaster its key
reloads the gun, and on another hero it is that hero's key.  ``weapon`` (a
:class:`~mercy_ai.weapon.StaffWatch`) is asked every frame while Flash Heal is
on, and again right before a press.

While the scoreboard key is down (``scoreboard``, a
:class:`~mercy_ai.scoreboard.ScoreboardKey`) Flash Heal decides nothing; the beam
keeps switching between heal and damage boost as usual.

Every external dependency is injected: ``clock=``, ``sleep=``, ``score=``,
``capture``, ``controller``, ``focus_gate``, ``weapon`` and ``scoreboard``.  With fakes for these the loop
runs deterministically with no screen, keyboard or game, and each
:meth:`ControlLoop.step` returns a :class:`StepOutcome` describing exactly what
it did.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import Config, wants_boost
from .flash_heal import FlashHealPlanner
from .vision import score_region

log = logging.getLogger(__name__)


def _no_status(*_args: object) -> None:
    """The status sink when nobody is listening."""


__all__ = ["ControlLoop", "FpsMeter", "StepOutcome"]

#: How long to block when there is nothing to do.  Short enough that a pause
#: toggle or a window switch is noticed promptly, long enough to be free.
IDLE_WAIT = 0.10
UNFOCUSED_SLEEP = 0.10
PAUSED_SLEEP = 0.05

#: How often the ``status`` callback is called, about 10 Hz.  Faster updates
#: would cost CPU without being readable.
STATUS_INTERVAL = 0.10


class FpsMeter:
    """Exponential moving average of the loop rate."""

    def __init__(self, alpha: float = 0.05, clock: Callable[[], float] | None = None) -> None:
        self.alpha = alpha
        self.value = 0.0
        self._clock = clock or time.perf_counter
        self._last: float | None = None

    def tick(self) -> float:
        now = self._clock()
        if self._last is None:
            self._last = now
            return self.value
        delta = now - self._last
        self._last = now
        if delta > 0:
            instant = 1.0 / delta
            self.value = (
                instant if self.value == 0.0 else (self.value + self.alpha * (instant - self.value))
            )
        return self.value


@dataclass(frozen=True)
class StepOutcome:
    """What one iteration did, returned by :meth:`ControlLoop.step`."""

    kind: str  # "idle" | "paused" | "unfocused" | "scored"
    health: float = 0.0
    bomb: float = 0.0
    bomb_scored: bool = False
    boosting: bool = False
    healing: bool = False
    slept: float = 0.0
    pressed: tuple[Any, ...] = field(default_factory=tuple)
    released: tuple[Any, ...] = field(default_factory=tuple)
    flash_fired: bool = False
    flash_reason: str | None = None
    #: The staff was not seen, so Flash Heal stood down this frame.
    no_staff: bool = False
    #: A press was asked for but held back because the staff was not out.
    flash_held: bool = False
    #: The scoreboard key was down, so Flash Heal decided nothing this frame.
    scoreboard: bool = False


class ControlLoop:
    """Capture, score, decide, act."""

    def __init__(
        self,
        *,
        assets: Any,
        config: Config,
        state: Any,
        controller: Any,
        capture: Any,
        heal_key: Any,
        boost_key: Any,
        flash_key: Any = None,
        focus_gate: Any | None = None,
        weapon: Any | None = None,
        scoreboard: Any | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        status: Callable[..., None] | None = None,
        score: Callable[..., float] | None = None,
    ) -> None:
        self.assets = assets
        self.config = config
        self.state = state
        self.controller = controller
        self.capture = capture
        self.heal_key = heal_key
        self.boost_key = boost_key
        self.flash_key = flash_key
        self.focus_gate = focus_gate
        self.weapon = weapon
        self.scoreboard = scoreboard
        # perf_counter is monotonic and sub-microsecond.  time.time is the wall
        # clock: an NTP correction can move it backwards, making the elapsed time
        # negative and freezing the action cooldown until the clock catches up.
        # Its Windows resolution (~15.6 ms) is also coarse next to that cooldown.
        self._clock = clock or time.perf_counter
        self._sleep = sleep or time.sleep
        self._status = status or _no_status
        self._score = score or score_region

        self.damage_boost = False
        self.heal = False
        self._last_action = self._clock()
        self._last_status = 0.0
        self.fps = FpsMeter(clock=self._clock)
        self.iterations = 0

        # Flash Heal is inert unless a key and a trigger are both configured, so
        # with the shipped defaults this changes nothing the loop does.
        self.flash = FlashHealPlanner(config, clock=self._clock)
        if self.flash.enabled and flash_key is None:
            log.warning("flash_heal is configured but no key was resolved; disabling it.")
            self.flash.disable()

    # -- helpers ----------------------------------------------------------- #

    def _release_everything(self) -> tuple[Any, ...]:
        released: tuple[Any, ...] = ()
        if self.damage_boost or self.heal:
            # Release every held key once either beam key is down; releasing a
            # key that is not pressed is harmless.
            released = tuple(sorted(self.controller.held, key=repr))
            self.controller.release_all()
            self.damage_boost = False
            self.heal = False
        # Reset unconditionally: the planner may hold state (an armed timer, a
        # pending release) even on a frame where no key was down.
        self.flash.reset()
        if self.weapon is not None:
            self.weapon.reset()
        return released

    # -- one iteration ----------------------------------------------------- #

    def step(self) -> StepOutcome:
        frame_start = self._clock()
        self.iterations += 1

        # --- idle: trigger not held ---------------------------------------- #
        if not self.state.run.is_set():
            released = self._release_everything()
            self.state.run.wait(IDLE_WAIT)
            return StepOutcome(kind="idle", slept=IDLE_WAIT, released=released)

        # --- paused -------------------------------------------------------- #
        #
        # Pausing with the trigger still held leaves ``run`` set, so this branch
        # must release the keys and sleep rather than spin on a busy loop.
        if self.state.paused.is_set():
            released = self._release_everything()
            self._sleep(PAUSED_SLEEP)
            return StepOutcome(kind="paused", slept=PAUSED_SLEEP, released=released)

        # --- focus gate ----------------------------------------------------- #
        #
        # Sleep while another window has focus so the loop costs nothing when
        # the game is in the background, and release the keys so alt-tabbing
        # away mid-heal does not leave one held.
        gate = self.focus_gate if self.config.focus_gate else None
        if gate is not None and not gate.is_focused():
            released = self._release_everything()
            self._sleep(UNFOCUSED_SLEEP)
            return StepOutcome(kind="unfocused", slept=UNFOCUSED_SLEEP, released=released)

        # --- vision --------------------------------------------------------- #
        health_frame, bomb_frame = self.capture.frames()
        health = self._score(health_frame, self.assets.health, self.assets.health_buffers)

        bomb = 0.0
        bomb_scored = False
        boost_wanted = wants_boost(
            health,
            self.config.threshold,
            self.damage_boost,
            self.config.hysteresis,
            self.config.dynamic_heal_drop if self.config.dynamic_heal else 0.0,
        )
        # The bomb score is computed only when it can change the outcome: when
        # health has not already asked for boost and bomb detection is on, or
        # whenever Flash Heal is on: it waits the grenade out either way.
        decides_boost = self.config.bomb_detection and not boost_wanted
        if decides_boost or self.flash.needs_bomb:
            bomb = self._score(bomb_frame, self.assets.bomb, self.assets.bomb_buffers)
            bomb_scored = True
            if decides_boost:
                boost_wanted = bomb >= self.config.bomb_threshold

        # --- Flash Heal ------------------------------------------------------ #
        #
        # Asked before the act block so that ``force_heal_mode`` can steer this
        # frame's beam decision: Flash Heal only works in heal mode, and the
        # planner requests the switch MODE_SWITCH_LEAD ahead of the press so the
        # action cooldown has time to apply it.
        flash_now = self._clock()
        watch = self.weapon if self.flash.enabled else None
        no_staff = watch is not None and not watch.staff_out(flash_now)
        menu = self.scoreboard is not None and self.flash.enabled and self.scoreboard.held()
        flash = self.flash.update(
            now=flash_now,
            bomb_present=bomb >= self.config.bomb_threshold,
            healing=self.heal,
            boosting=self.damage_boost,
            health=health,
            staff=not no_staff,
            menu=menu,
        )
        if flash.force_heal_mode:
            boost_wanted = False

        # --- act ------------------------------------------------------------ #
        pressed: list[Any] = []
        released_keys: list[Any] = []
        now = self._clock()
        if now - self._last_action >= self.config.action_cooldown:
            if boost_wanted:
                if not self.damage_boost:
                    # Heal is deliberately NOT released here: damage boost is
                    # overlaid on a still-held heal (see the module docstring).
                    self.controller.press(self.boost_key)
                    pressed.append(self.boost_key)
                    self.damage_boost = True
                    self._last_action = now
            else:
                if self.damage_boost:
                    self.controller.release(self.boost_key)
                    released_keys.append(self.boost_key)
                    self.damage_boost = False
                    # A release does not restart the cooldown.
                if not self.heal:
                    self.controller.press(self.heal_key)
                    pressed.append(self.heal_key)
                    self.heal = True
                    self._last_action = now

        # The flash key is pressed after the beam decision, so heal mode is
        # already applied this frame. It never touches ``_last_action``: Flash
        # Heal is a separate ability and must not delay a weapon switch.
        #
        # One last look at the weapon first: the routine check above can be up
        # to READ_INTERVAL old, and a press without the staff is a reload or
        # another hero's ability.
        flash_held = False
        if flash.press and watch is not None and not watch.confirm(self._clock()):
            self.flash.veto()
            flash_held = no_staff = True
        elif flash.press and self.flash_key is not None:
            self.controller.press(self.flash_key)
            pressed.append(self.flash_key)
        elif flash.release and self.flash_key is not None:
            self.controller.release(self.flash_key)
            released_keys.append(self.flash_key)

        # --- present -------------------------------------------------------- #
        rate = self.fps.tick()
        if now - self._last_status >= STATUS_INTERVAL:
            self._last_status = now
            try:
                self._status(
                    self.state.paused.is_set(),
                    health,
                    bomb,
                    rate,
                    self.damage_boost,
                    self.flash.describe(now) if self.flash.enabled else None,
                )
            except Exception:  # pragma: no cover - a status callback must never kill the loop
                log.debug("status line write failed", exc_info=True)

        # --- pace ------------------------------------------------------------ #
        slept = 0.0
        if self.config.target_fps > 0:
            # Frame-rate cap is opt-in; 0 (unlimited) is the default.
            budget = 1.0 / self.config.target_fps
            remaining = budget - (self._clock() - frame_start)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining

        return StepOutcome(
            kind="scored",
            health=health,
            bomb=bomb,
            bomb_scored=bomb_scored,
            boosting=self.damage_boost,
            healing=self.heal,
            slept=slept,
            flash_fired=flash.press and not flash_held,
            flash_reason=None if flash_held else flash.reason,
            no_staff=no_staff,
            flash_held=flash_held,
            scoreboard=menu,
            pressed=tuple(pressed),
            released=tuple(released_keys),
        )

    def run(self, max_iterations: int | None = None) -> int:
        """Drive :meth:`step` until shutdown, or for *max_iterations* (tests)."""
        try:
            while not self.state.shutdown.is_set():
                if max_iterations is not None and self.iterations >= max_iterations:
                    break
                self.step()
        finally:
            self.controller.release_all()
        return self.iterations
