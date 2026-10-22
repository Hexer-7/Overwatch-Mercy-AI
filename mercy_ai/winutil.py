# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Windows integration: DPI, monitors, elevation, single instance.

Each helper here guards against a way the program could otherwise fail
*silently*, with no error and no symptom beyond "it doesn't work in game".

The pure decision logic is separated from the Win32 calls so it can be tested
without a display, a game, or Windows at all.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from .geometry import Region
from .paths import RESOLUTIONS

log = logging.getLogger(__name__)

__all__ = [
    "WINDOW_TITLE",
    "SingleInstanceError",
    "acquire_single_instance",
    "describe_monitors",
    "detect_screen_height",
    "diagnose_region",
    "elevation_warning",
    "ensure_dpi_aware",
    "focus_existing_window",
    "is_elevated",
    "list_monitors",
    "primary_monitor",
    "region_is_on_monitor",
]

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
MUTEX_NAME = "Global\\MercyAI_v1"
#: The desktop window's title, which is how a second launch finds the first.
WINDOW_TITLE = "Mercy AI"
SW_RESTORE = 9


class SingleInstanceError(RuntimeError):
    """Another copy of Mercy AI already holds the mutex."""


# --------------------------------------------------------------------------- #
# DPI awareness
# --------------------------------------------------------------------------- #


def ensure_dpi_aware() -> str:
    """Declare this process DPI-aware before anything reads screen metrics.

    ``info.xml`` coordinates are **physical pixels**.  If Windows display scaling
    is not 100 % -- the default is 150 % or 200 % on most 4K laptops -- and the
    process is DPI-unaware, Windows virtualises coordinates and reports scaled
    metrics, so the capture lands on the wrong part of the screen and every score
    is meaningless.  ``mss`` does set awareness itself, but doing it explicitly
    and *first* removes the ordering dependency and lets us warn.

    Returns which API succeeded, for logging.
    """
    if os.name != "nt":
        return "n/a"
    try:
        import ctypes

        try:
            # 2 = PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+).
            # This returns an HRESULT; ctypes.windll does *not* raise on failure,
            # so the result has to be inspected. E_ACCESSDENIED means awareness
            # was already set (by a manifest, or by mss) -- which is fine.
            result = ctypes.windll.shcore.SetProcessDpiAwareness(2) & 0xFFFFFFFF
            if result not in (0x00000000, 0x80070005):
                ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()

        # Report what is actually in effect rather than what we asked for.
        try:
            awareness = ctypes.c_int()
            ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
            mode = {0: "unaware", 1: "system", 2: "per-monitor"}.get(
                awareness.value, str(awareness.value)
            )
        except (AttributeError, OSError):
            mode = "system"

        if mode == "unaware":
            log.warning(
                "This process is DPI-unaware; if Windows display scaling is not 100%% "
                "the capture region will be misaligned."
            )
        return mode
    except Exception:
        log.warning(
            "Could not set DPI awareness; capture may be misaligned if Windows "
            "display scaling is not 100%%."
        )
        return "none"


# --------------------------------------------------------------------------- #
# The region must be on the primary monitor
# --------------------------------------------------------------------------- #


def region_is_on_monitor(region: Region, monitor: dict[str, int]) -> bool:
    """Pure containment test, so it can be checked without a display."""
    return (
        region.x >= monitor["left"]
        and region.y >= monitor["top"]
        and region.right <= monitor["left"] + monitor["width"]
        and region.bottom <= monitor["top"] + monitor["height"]
    )


def list_monitors() -> list[dict[str, int]]:
    """Enumerate monitors. ``[0]`` is the whole virtual desktop, ``[1:]`` are screens."""
    import mss

    with mss.mss() as sct:
        return [dict(monitor) for monitor in sct.monitors]


def primary_monitor(monitors: list[dict[str, int]]) -> dict[str, int]:
    """Return the real primary monitor.

    ``mss.monitors[1]`` is **not** the primary -- it is simply whichever monitor
    ``EnumDisplayMonitors`` reported first, which on a multi-monitor setup is
    often a secondary screen.

    Windows does guarantee one thing: the primary monitor's top-left corner is
    the origin of the virtual desktop, so it is the monitor at (0, 0).
    """
    screens = monitors[1:] if len(monitors) > 1 else monitors
    for monitor in screens:
        if monitor.get("left") == 0 and monitor.get("top") == 0:
            return monitor
    # No monitor at the origin should be impossible on Windows; fall back rather
    # than raise, since this only feeds a warning.
    return screens[0]


def describe_monitors(monitors: list[dict[str, int]]) -> str:
    """Human-readable monitor layout, for the warning and for ``--monitors``."""
    screens = monitors[1:] if len(monitors) > 1 else monitors
    primary = primary_monitor(monitors)
    lines = []
    for index, monitor in enumerate(screens, start=1):
        mark = "  <- primary" if monitor is primary else ""
        lines.append(
            f"  monitor {index}: {monitor['width']}x{monitor['height']} "
            f"at ({monitor['left']}, {monitor['top']}){mark}"
        )
    return "\n".join(lines)


def diagnose_region(
    region: Region,
    monitors: list[dict[str, int]],
    resolution: str | None = None,
) -> str | None:
    """Explain what is wrong with the configured region, if anything.

    ``info.xml`` holds coordinates computed for a screen whose top-left is
    (0, 0) -- that is, the primary monitor.  So the useful questions are "does
    the region fit the primary?" and "does the primary's size match the
    configured resolution?", and the answer should name the actual numbers
    rather than send the user off to move windows around.
    """
    primary = primary_monitor(monitors)
    layout = describe_monitors(monitors)

    if not region_is_on_monitor(region, primary):
        if str(primary["height"]) in RESOLUTIONS:
            suggestion = (
                f"\nYour primary monitor is {primary['height']}p, so choose "
                f"{primary['height']} as the resolution in Settings."
            )
        else:
            # This screen is not one Mercy AI has templates for at all. That is
            # an ordinary thing to hit while setting the program up on one
            # computer to be used on another, and saying so stops the message
            # reading like an accusation about a setting the user got right.
            suggestion = (
                f"\nThis screen is {primary['width']}x{primary['height']}, and Mercy AI only "
                f"has templates for screens {', '.join(RESOLUTIONS[:-1])} "
                f"or {RESOLUTIONS[-1]} pixels tall.\n"
                f"If you are setting Mercy AI up here to run on a different computer, that is "
                f"expected: the settings are saved either way, but it cannot watch the screen "
                f"on this one."
            )
        elsewhere = [
            index
            for index, other in enumerate(monitors[1:], start=1)
            if region_is_on_monitor(region, other)
        ]
        note = ""
        if elsewhere:
            note = (
                f"\nIt would fit monitor {elsewhere[0]}, but Mercy AI reads coordinates "
                f"relative to the primary monitor, so Overwatch must run there."
            )
        return (
            f"The capture region ({region.width}x{region.height} at {region.x},{region.y}) "
            f"falls outside your primary monitor "
            f"({primary['width']}x{primary['height']}).\n"
            f"{layout}{note}{suggestion}"
        )

    if (
        resolution
        and str(primary["height"]) in RESOLUTIONS
        and str(primary["height"]) != resolution
    ):
        return (
            f"The resolution is set to {resolution}, but your primary "
            f"monitor is {primary['width']}x{primary['height']}.\n{layout}\n"
            f"If Overwatch runs fullscreen at your desktop resolution, "
            f"choose {primary['height']} as the resolution in Settings."
        )

    return None


# --------------------------------------------------------------------------- #
# Detect the screen resolution
# --------------------------------------------------------------------------- #


def detect_screen_height(monitors: list[dict[str, int]] | None = None) -> str | None:
    """Best-guess resolution name, preselected during first-run setup.

    Picking 1080 on a 1440p monitor produces zero matches with no error at all,
    and resolution is the very first thing a new user chooses.
    """
    try:
        if monitors is None:
            monitors = list_monitors()
        # The primary is the monitor at the origin, not monitors[1].
        height = primary_monitor(monitors)["height"]
    except Exception:
        log.debug("could not detect screen height", exc_info=True)
        return None
    return str(height) if str(height) in RESOLUTIONS else None


# --------------------------------------------------------------------------- #
# Elevation
# --------------------------------------------------------------------------- #


def is_elevated(probe: Callable[[], int] | None = None) -> bool | None:
    """True/False if it can be determined, ``None`` if not.

    The ``keyboard`` library's Windows backend needs administrator rights for its
    low-level hook to affect an elevated process, and Overwatch's anti-cheat runs
    elevated.  Without it, ``keyboard.press()`` succeeds *in Python* and does
    nothing *in the game* -- the most confusing possible failure, because there
    is no error anywhere.
    """
    if probe is None:
        if os.name != "nt":
            return None
        try:
            import ctypes

            probe = ctypes.windll.shell32.IsUserAnAdmin
        except Exception:
            return None
    try:
        return bool(probe())
    except Exception:
        return None


def elevation_warning(elevated: bool | None) -> str | None:
    if elevated is not False:
        return None
    return (
        "Not running as administrator. Key presses may not reach Overwatch. "
        "If nothing happens in game, restart Mercy AI with 'Run as administrator'."
    )


# --------------------------------------------------------------------------- #
# One instance at a time
# --------------------------------------------------------------------------- #


def acquire_single_instance(create_mutex: Callable[[], tuple[Any, int]] | None = None) -> Any:
    """Take a named mutex, refusing to start twice.

    Two instances fight over the same keys: one presses heal while the other
    presses damage boost, and the game receives a stream of contradictory input
    that looks exactly like a broken tool.  Easy to hit by double-clicking the
    exe.  The returned handle must stay referenced for the process lifetime.

    "Access denied" counts as already running too: it is what a copy without
    administrator rights gets when the elevated exe already holds the mutex.
    """
    if create_mutex is None:
        if os.name != "nt":
            return None

        def create_mutex() -> tuple[Any, int]:
            import ctypes

            # use_last_error: GetLastError read through a second ctypes call is
            # not guaranteed to still hold CreateMutexW's error.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            return handle, ctypes.get_last_error()

    handle, last_error = create_mutex()
    if last_error in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED):
        raise SingleInstanceError("Mercy AI is already running. Close the other copy first.")
    return handle


#: Window classes a running Mercy AI can have: the window (WinForms, through
#: pywebview), or the text interface in a console or in Windows Terminal. An
#: Explorer window showing a folder called "Mercy AI" has the same title and
#: must never be the one brought forward.
MERCY_WINDOW_CLASSES = ("WindowsForms10.", "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")


def _text(getter: Callable[..., Any], hwnd: Any) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(256)
    getter(hwnd, buffer, len(buffer))
    return buffer.value


def _is_mercy_window(user32: Any, hwnd: Any, title: str) -> bool:
    if not _text(user32.GetClassNameW, hwnd).startswith(MERCY_WINDOW_CLASSES):
        return False
    # An elevated console prefixes its title, "Administrator: Mercy AI" (in the
    # system language).
    shown = _text(user32.GetWindowTextW, hwnd)
    return shown == title or shown.endswith(": " + title)


def _find_mercy_window(user32: Any, title: str) -> Any:
    hwnd = user32.FindWindowW(None, title)
    if hwnd and _is_mercy_window(user32, hwnd, title):
        return hwnd
    import ctypes

    factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    found: list[Any] = []

    def visit(candidate: Any, _param: Any) -> bool:
        if user32.IsWindowVisible(candidate) and _is_mercy_window(user32, candidate, title):
            found.append(candidate)
            return False
        return True

    callback = factory(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(visit)
    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def focus_existing_window(title: str = WINDOW_TITLE, user32: Any = None) -> bool:
    """Bring an already-open Mercy AI to the front.  True if one was found.

    Someone who launches the program while it is already open (usually
    minimised and forgotten) wants that copy, so a second launch shows it and
    exits instead of reporting an error.
    """
    if user32 is None:
        if os.name != "nt":
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
        except Exception:
            return False
    try:
        hwnd = _find_mercy_window(user32, title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        log.debug("could not bring the existing window forward", exc_info=True)
        return False
