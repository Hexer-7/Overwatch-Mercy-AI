# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""What the text interface is showing, and the facts it shows.

:class:`Data` is what the :class:`~mercy_ai.ui.bridge.Bridge` reports: the
settings (as ``config_to_ui`` shapes them), the live status and the facts
about this computer. :class:`UIState` is everything else: which screen, which
row has focus, what is being typed, which message is up. Neither knows how to
draw itself or how to talk to the bridge, which is what keeps both testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["TABS", "Data", "Edit", "Effect", "Message", "Modal", "Setup", "UIState"]

#: The four tabs, in order. Settings > Advanced is a sub-screen of Settings.
TABS = ("Status", "Settings", "Help", "About")


@dataclass
class Data:
    config: dict[str, Any]
    status: dict[str, Any]
    env: dict[str, Any]
    version: str


@dataclass
class Edit:
    """A number being typed in."""

    field: str
    text: str = ""


@dataclass
class Message:
    """The one-line message above the footer."""

    text: str
    tone: str = "good"  # good | warn | error
    until: float | None = None  # None: stays until the next action


@dataclass
class Modal:
    """A box drawn over the screen: key capture, the trigger menu, quit."""

    kind: str  # capture | trigger | quit
    field: str = ""
    focus: int = 0
    problem: str = ""
    saving: bool = False
    in_setup: bool = False


@dataclass
class Setup:
    step: int = 1
    ack: bool = False
    heal: str = ""
    boost: str = ""
    resolution: str = ""
    redo: bool = False
    problem: str = ""


@dataclass
class Effect:
    """Something only the outside world can do: talk to the bridge, capture, quit."""

    kind: str  # save | run | pause | link | folder | capture | uncapture | quit
    payload: Any = None
    tag: Any = None


@dataclass
class UIState:
    cols: int = 80
    rows: int = 25
    now: float = 0.0
    screen: str = "main"  # setup | main
    tab: int = 0
    advanced: bool = False
    focus: dict[str, str] = field(default_factory=dict)
    scroll: dict[str, int] = field(default_factory=dict)
    setup: Setup = field(default_factory=Setup)
    edit: Edit | None = None
    #: Values stepped with the arrows, saved a moment after the last press.
    pending: dict[str, float] = field(default_factory=dict)
    pending_at: float = 0.0
    #: Values sent to the bridge whose answer has not come back yet.
    inflight: dict[str, Any] = field(default_factory=dict)
    #: A refusal shown under the row it belongs to.
    inline: dict[str, str] = field(default_factory=dict)
    modal: Modal | None = None
    message: Message | None = None
    #: Refusals from Start, shown in the Status problem panel.
    status_problems: list[str] = field(default_factory=list)
    busy: int = 0
    quit: bool = False
