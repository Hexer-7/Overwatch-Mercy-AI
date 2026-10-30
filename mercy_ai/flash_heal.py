# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Flash Heal: a burst of healing when the ally needs it most.

Three triggers, any of which can be on:

**After Ana's grenade.** ``data/*/ana_bomb.png`` is the anti-heal debuff on
your ally.  :class:`~mercy_ai.loop.ControlLoop` switches to damage boost while
it is on screen, because healing is nullified and boosting is the only output
that does anything.  A Flash Heal cast *during* the grenade is worse than none:
the burst lands inside the anti-heal and the cooldown is gone.  So the key is
pressed the moment the grenade wears off, when healing works again -- and only
if the ally is then below :data:`EXPIRY_MAX_HEALTH`.

**Emergency.** The health score falls sharply within :data:`DROP_WINDOW`.

**Low health.** The health score stays below a fixed line.  Simpler than the
emergency check and blind to how fast the fall was; fires once per dip and is
armed again when the ally climbs back above the line.

Neither health trigger fires while the grenade is seen: they wait it out, and
the beam stays on the damage boost it switched to.  This holds with the switch
to damage boost turned off too: the grenade is still watched for Flash Heal.

**Only with the staff out.**  Flash Heal's key does something else the rest of
the time: with the Caduceus Blaster in hand it reloads the gun, and on another
hero it is that hero's key.  The loop reports whether the staff is seen
(:mod:`mercy_ai.weapon`) and, while it is not, nothing fires and nothing is
remembered: the readings taken meanwhile describe no beam, so they are
dropped, and the beam settles again once the staff is back.

Why this is a state machine with no I/O
---------------------------------------
Timing bugs are invisible in a live match and expensive to get wrong, so every
decision lives here, behind an injected clock, where it can be driven directly
with fake timestamps.  The loop's only job is to report what it sees and obey
the returned :class:`FlashAction`.

Erring late is safe, erring early is not
----------------------------------------
The grenade trigger waits until the icon is *gone* for :data:`CONFIRM_FRAMES`
frames rather than predicting when it will end, so the burst lands a few
milliseconds after anti-heal ends.  Late costs a little healing; early would
waste the whole cooldown on a nullified cast.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

__all__ = [
    "CONFIRM_FRAMES",
    "DROP_WINDOW",
    "EXPIRY_MAX_HEALTH",
    "FLASH_HOLD",
    "LOW_HEALTH_REARM",
    "REFIRE_GAP",
    "FlashAction",
    "FlashHealPlanner",
    "FlashState",
]

#: Consecutive frames the icon must be seen (or unseen) before the transition is
#: believed.  The bomb score sits near its 0.74 threshold, and a single
#: flickering frame would otherwise look like a fresh grenade and fire again.
#: At a few hundred frames a second this costs ~12 ms of latency.
CONFIRM_FRAMES = 2

#: How long the key is held.  Released on a later frame, never with ``sleep``,
#: because the loop must not block.
FLASH_HOLD = 0.05

#: Window over which a health-score fall counts as "sudden".
DROP_WINDOW = 0.3

#: Minimum gap between two presses.  This is *not* the ability's cooldown -- the
#: game enforces that, and pressing the key early costs nothing.  It exists only
#: so a sustained drop cannot hammer the key hundreds of times a second.
REFIRE_GAP = 0.35

#: The expiry trigger only fires when the ally's health score is below this.
#: An ally who is already close to full when the anti-heal wears off gets
#: little from the burst, and the beam alone tops them up -- so the cast is
#: kept for when it matters. (Score, not hit points: 1.0 is a full ring.)
EXPIRY_MAX_HEALTH = 0.5

#: How far above the low-health line the ally must climb before that trigger
#: can fire again.  Without it a score wobbling on the line would fire on
#: every dip.
LOW_HEALTH_REARM = 0.05

#: The two ways the health trigger can work.
HEALTH_MODES = ("emergency", "low")


class FlashState(Enum):
    """Where the planner is in its cycle."""

    IDLE = "idle"
    HOLDING = "holding"  # key is down, waiting to release it
    COOLDOWN = "cooldown"  # fired recently, ability unavailable


@dataclass(frozen=True)
class FlashAction:
    """What the loop should do this frame.  All three default to "nothing"."""

    press: bool = False
    release: bool = False
    #: Ask the main decision to stay in (or return to) heal mode this frame.
    force_heal_mode: bool = False
    #: Why a press happened, for the log.  ``None`` when nothing fired.
    reason: str | None = None


class FlashHealPlanner:
    """Decides when to press Flash Heal.  Pure logic: no screen, no keyboard.

    Feed it what the loop already knows each frame and obey the result::

        action = planner.update(
            now=now, bomb_present=..., healing=..., boosting=..., health=...
        )
        if action.force_heal_mode:
            boost_wanted = False
        if action.press:
            controller.press(flash_key)
    """

    def __init__(
        self,
        config: Config,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.perf_counter
        self.trigger: str = str(config.flash_heal_trigger).lower()
        self.on_health_drop: bool = bool(config.flash_heal_on_health_drop)
        self.health_mode: str = str(config.flash_heal_health_mode).lower()
        self.low_health: float = float(config.flash_heal_low_health)
        self.cooldown: float = float(config.flash_heal_cooldown)
        self.min_heal_time: float = float(config.flash_heal_min_heal_time)
        self.drop: float = float(config.flash_heal_drop)
        self.settle: float = float(config.flash_heal_settle)
        self.max_fall_rate: float = float(config.flash_heal_max_fall_rate)
        self.blank_floor: float = float(config.flash_heal_blank_floor)
        self.key_configured: bool = bool(config.flash_heal_btn)

        self.state = FlashState.IDLE

        self._seen = 0  # consecutive frames with the icon present
        self._unseen = 0  # consecutive frames without it
        self._bomb_confirmed = False  # debounced view of "grenade is on my ally"
        self._bomb_started: float | None = None
        self._fired_this_bomb = False
        self._healing_since: float | None = None
        self._pressed_at: float | None = None
        self._last_fire: float | None = None
        self._history: list[tuple[float, float]] = []  # (timestamp, health score)
        self._watching_since: float | None = None  # when the trigger went down
        self._below = 0  # consecutive healing frames under the low-health line
        self._low_fired = False  # fired for this dip; cleared above the line
        self._no_staff = False  # standing down because the staff is not seen
        self._fire_before: float | None = None  # _last_fire before the latest press

    # -- introspection ----------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        """False unless a key is configured *and* some trigger is on.

        Both halves matter: a trigger with no key would press nothing, and a key
        with no trigger should stay completely inert.
        """
        if not self.key_configured:
            return False
        return self.trigger == "expiry" or self.on_health_drop

    @property
    def needs_bomb(self) -> bool:
        """True when the planner must see the grenade score on *every* frame.

        The loop normally skips scoring the grenade once the health bar has
        already decided to boost.  That is fine for the boost decision
        -- ``or`` short-circuits -- but it would hide a grenade thrown at an ally
        who happens to be at full health, and the planner would never arm.

        The health triggers need it too, to wait out the grenade, and they
        need it whether or not the grenade switches the beam to damage boost.
        """
        return self.enabled

    def describe(self, now: float | None = None) -> str:
        """One short phrase for the loop's status callback."""
        if not self.enabled:
            return "off"
        if self._no_staff:
            return "held (no staff)"
        now = self._clock() if now is None else now
        if self.state is FlashState.COOLDOWN and self._last_fire is not None:
            waiting = max(self.cooldown, REFIRE_GAP) - (now - self._last_fire)
            if waiting > 0.5:
                return f"waiting {waiting:.0f}s"
            return "ready"
        if self.state is FlashState.HOLDING:
            return "firing"
        return "ready"

    # -- main entry point --------------------------------------------------- #

    def update(
        self,
        *,
        now: float,
        bomb_present: bool,
        healing: bool,
        boosting: bool,
        health: float = 0.0,
        staff: bool = True,
    ) -> FlashAction:
        """Advance one frame and say what to do.

        *staff* is False while the staff is not seen: nothing fires then.
        """
        if not self.enabled:
            return FlashAction()

        self._track_healing(now, healing)
        self._track_health(now, healing, health)
        self._track_low(healing, health)
        rising, falling = self._debounce(bomb_present)

        # A held key is released purely on elapsed time, before anything else,
        # so a state change can never strand it down.
        if self.state is FlashState.HOLDING:
            if self._pressed_at is not None and now - self._pressed_at >= FLASH_HOLD:
                self.state = FlashState.COOLDOWN
                self._pressed_at = None
                return FlashAction(release=True)
            return FlashAction()

        if not staff:
            self._stand_down()
            return FlashAction()
        self._no_staff = False

        if self.state is FlashState.COOLDOWN:
            if self._last_fire is not None and now - self._last_fire >= self.cooldown:
                self.state = FlashState.IDLE
            else:
                return FlashAction()

        if rising:
            self._on_grenade_start(now)

        if (
            falling
            and self.trigger == "expiry"
            and health < EXPIRY_MAX_HEALTH
            and self._may_fire(now, healing, boosting)
        ):
            return self._fire(now, "grenade expired")

        # The health triggers wait out the grenade: healing is nullified, so a
        # burst now heals nothing and wastes the cooldown. Asking for heal mode
        # would also drop the boost the grenade switched to. A first unconfirmed
        # frame counts too -- erring late is safe.
        if self._bomb_confirmed or self._seen > 0:
            return FlashAction()

        if self.on_health_drop and self.health_mode == "low":
            if self._low_and_settled(now):
                if self._may_fire(now, healing, boosting, per_grenade=False):
                    self._low_fired = True
                    return self._fire(now, "low health")
                return FlashAction(force_heal_mode=True)
            return FlashAction()

        if self.on_health_drop and self._sudden_drop():
            if self._may_fire(now, healing, boosting, per_grenade=False):
                # Forget the fall that caused this. Otherwise the same samples
                # keep satisfying _sudden_drop for the rest of the window and it
                # fires again the moment REFIRE_GAP elapses, on evidence it has
                # already acted upon.
                self._history.clear()
                self._watching_since = now
                return self._fire(now, "health drop")
            return FlashAction(force_heal_mode=True)

        return FlashAction()

    def veto(self) -> None:
        """Take back the press :meth:`update` just asked for; it never happened.

        For the loop's last look at the weapon right before pressing: if the
        staff turned out to be gone after all, the key is not pressed, so there
        is nothing to release and no cooldown to wait out.
        """
        if self.state is FlashState.HOLDING:
            self.state = FlashState.IDLE
            self._pressed_at = None
            self._last_fire = self._fire_before
            log.info("flash: press held back, the staff is not out")
        self._stand_down()

    def disable(self) -> None:
        """Never fire again, for a loop that has no Flash Heal key to press."""
        self.trigger = "off"
        self.on_health_drop = False

    def reset(self) -> None:
        """Return to a clean state.

        Called when the loop releases everything (pause, idle, focus lost).  The
        cooldown deliberately survives: the ability really is still on cooldown
        in game, whatever the tool is doing.
        """
        self._seen = self._unseen = 0
        self._bomb_confirmed = False
        self._bomb_started = None
        self._fired_this_bomb = False
        self._healing_since = None
        self._pressed_at = None
        self._history.clear()
        # The trigger has been released, so the next press must settle again.
        self._watching_since = None
        self._below = 0
        self._low_fired = False
        if self.state is FlashState.HOLDING:
            self.state = FlashState.COOLDOWN

    # -- internals ---------------------------------------------------------- #

    def _stand_down(self) -> None:
        """Forget what was seen; the staff is not out, so it described no beam."""
        self._no_staff = True
        self.reset()

    def _debounce(self, bomb_present: bool) -> tuple[bool, bool]:
        """Return ``(rising, falling)`` edges of the *confirmed* grenade state."""
        if bomb_present:
            self._seen += 1
            self._unseen = 0
        else:
            self._unseen += 1
            self._seen = 0

        rising = falling = False
        if not self._bomb_confirmed and self._seen >= CONFIRM_FRAMES:
            self._bomb_confirmed = True
            rising = True
        elif self._bomb_confirmed and self._unseen >= CONFIRM_FRAMES:
            self._bomb_confirmed = False
            falling = True
        return rising, falling

    def _track_healing(self, now: float, healing: bool) -> None:
        if healing:
            if self._healing_since is None:
                self._healing_since = now
        else:
            self._healing_since = None

    def _track_health(self, now: float, healing: bool, health: float) -> None:
        """Record the score, but only while it means something.

        Two guards, both of which stop a false "emergency":

        **Only while healing.** The score measures how complete Mercy's beam ring
        is. Break the beam and it falls to zero -- a bigger fall than any amount
        of damage -- so recording it while not healing turns *letting go of the
        trigger* into the strongest emergency signal there is.

        **Not for the first ``settle`` seconds.** The ring animates as the beam
        connects, sweeping up from empty. Any dip in that animation would read
        as a sudden fall and fire a flash the moment the trigger went down, or a
        burst of them under rapid clicking.
        """
        if not healing:
            self._watching_since = None
            self._history.clear()
            return
        if self._watching_since is None:
            self._watching_since = now
            return
        if now - self._watching_since < self.settle:
            return
        self._history.append((now, health))
        cutoff = now - DROP_WINDOW
        while self._history and self._history[0][0] < cutoff:
            self._history.pop(0)

    def _track_low(self, healing: bool, health: float) -> None:
        """Count frames under the low-health line, and re-arm above it.

        A score at or under ``blank_floor`` is a covered ring, not a nearly dead
        ally, so it never counts as low.
        """
        if not healing:
            self._below = 0
            self._low_fired = False
            return
        if health > self.low_health + LOW_HEALTH_REARM:
            self._low_fired = False
        covered = self.blank_floor > 0.0 and health <= self.blank_floor
        self._below = self._below + 1 if health < self.low_health and not covered else 0

    def _low_and_settled(self, now: float) -> bool:
        """True when the low-health trigger should fire this frame.

        Only once the beam has been healing for a moment: the ring sweeps up
        from empty as the beam connects, and that sweep would otherwise read as
        an ally on almost no health the instant the trigger went down.
        """
        if self._low_fired or self._below < CONFIRM_FRAMES or self._healing_since is None:
            return False
        return now - self._healing_since >= max(self.settle, self.min_heal_time)

    def _on_grenade_start(self, now: float) -> None:
        # The beam must already have been healing this ally, not merely have
        # glanced at them as the grenade landed.
        healing_for = 0.0 if self._healing_since is None else now - self._healing_since
        if healing_for < self.min_heal_time:
            log.debug(
                "flash: grenade seen but the beam had only been healing %.2fs (< %.2fs)",
                healing_for,
                self.min_heal_time,
            )
            return
        self._bomb_started = now
        self._fired_this_bomb = False
        log.info("flash: grenade detected at %.3f", now)

    def _may_fire(
        self, now: float, healing: bool, boosting: bool, *, per_grenade: bool = True
    ) -> bool:
        """Every precondition, in one place.

        ``per_grenade`` is False for the health-drop trigger, which is not tied
        to a grenade at all. ``_fired_this_bomb`` is set by *any* fire and is
        only cleared when a new grenade appears or the trigger is released, so
        applying it to health drops would let the feature fire only **once** per
        press of the mouse.
        """
        if per_grenade and self._fired_this_bomb:
            return False
        gap = max(self.cooldown, REFIRE_GAP)
        if self._last_fire is not None and now - self._last_fire < gap:
            return False
        # Flash Heal needs the beam in heal mode. If the switch has not landed,
        # skip this grenade rather than spend twelve seconds on a nullified cast.
        if not healing or boosting:
            log.debug(
                "flash: skipped, beam not in heal mode (healing=%s boosting=%s)", healing, boosting
            )
            return False
        return True

    def _fire(self, now: float, reason: str) -> FlashAction:
        self.state = FlashState.HOLDING
        self._pressed_at = now
        self._fire_before = self._last_fire
        self._last_fire = now
        self._fired_this_bomb = True
        # How long the grenade was on screen before the press, for the log.
        delay = (
            ""
            if self._bomb_started is None
            else f", {now - self._bomb_started:.3f}s after detection"
        )
        log.info("flash: FIRE (%s) at %.3f%s", reason, now, delay)
        return FlashAction(press=True, force_heal_mode=True, reason=reason)

    def _sudden_drop(self) -> bool:
        """True when the score fell like damage, rather than like a covered screen.

        The size of the fall is the easy half: ``_track_health`` has already
        trimmed the history to ``DROP_WINDOW``, so the whole buffer *is* the
        window and the peak-minus-latest comparison needs no timestamps.

        The hard half is that a fall of a given size has two entirely different
        causes, and only one of them is worth an ability. Two traces recorded in
        game -- one of ordinary damage, one with the scoreboard held open over
        the reticle -- separate completely on **shape**:

        =========================  ==============  ==============
        ..                         real damage     view blocked
        =========================  ==============  ==============
        fastest single frame       16.2 /s         56 to 124 /s
        frames above 30 /s         0               13
        lowest score reached       0.138           0.000 exactly
        =========================  ==============  ==============

        So two guards, either of which is enough on its own:

        **Too fast to be damage.** Health cannot leave a bar at 56 units of
        score per second. A leaderboard appearing over the reticle can, because
        the ring does not fall -- it is replaced.

        **Nothing left to see.** A score of exactly zero means the colour mask
        matched no pixels at all, which is a hidden ring rather than a nearly
        dead ally: an ally at 1 HP still draws one, and the damage trace bottomed
        out at 0.138 while healing.

        Both are configurable, and setting either to 0 turns it off.
        """
        if len(self._history) < 2:
            return False
        latest = self._history[-1][1]
        if self.blank_floor > 0.0 and latest <= self.blank_floor:
            return False
        if 0.0 < self.max_fall_rate < self._fastest_fall():
            return False
        peak = max(score for _stamp, score in self._history)
        return (peak - latest) >= self.drop

    def _fastest_fall(self) -> float:
        """The steepest single-frame fall in the window, in score units a second.

        Per second rather than per frame on purpose: the same drop is twice as
        many frames at 165 fps as at 80, and a threshold in frames would mean
        something different on every machine.
        """
        fastest = 0.0
        for (before_at, before), (after_at, after) in pairwise(self._history):
            gap = after_at - before_at
            if gap > 0:
                fastest = max(fastest, (before - after) / gap)
        return fastest
