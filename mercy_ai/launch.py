# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Start Mercy AI: the window, the text interface, or a diagnostic.

``python -m mercy_ai`` and the built ``Mercy AI.exe`` both arrive here.

* No arguments opens the window.
* ``-cli`` (or ``--cli``) opens the text interface in a console.
* ``-monitors``, ``-calibrate``, ``-benchmark``, ``-capture-modes`` and
  ``-dump-region`` are diagnostics that print to a console and exit.
* ``-help`` and ``-version`` print in the terminal Mercy AI was started from.
* ``--self-check`` is for the build (see :mod:`mercy_ai.selfcheck`).

Every option works with one dash or two: ``-cli`` and ``--cli`` are the same.

The exe is a windowed program, so it starts with no console and Python sets
``sys.stdin``, ``sys.stdout`` and ``sys.stderr`` to ``None``. The text
interface and the diagnostics open a console of their own; the window gets
streams that discard, and any failure before it opens is shown in a message box
rather than printed where nobody can read it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import re
import sys
from pathlib import Path
from typing import NoReturn

from . import __version__
from .errors import MercyAIError
from .paths import CONFIG_PATH

log = logging.getLogger(__name__)

__all__ = ["ensure_streams", "help_text", "main", "parse_args", "report_failure"]

#: The three names a program may reasonably assume exist.
STREAMS = ("stdin", "stdout", "stderr")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class UsageError(Exception):
    """An option Mercy AI does not know, or one missing its value."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


#: What ``-help`` prints, section by section: ``(one dash, two dashes, what it does)``.
#: The parser below is built from the same table, so the two cannot drift.
OPTIONS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Open",
        (
            ("(no option)", "", "Open the window."),
            ("-cli", "--cli", "Open the text interface in a console."),
        ),
    ),
    (
        "Settings",
        (
            (
                "-config PATH",
                "--config PATH",
                "Use this config.xml instead of the one next to Mercy AI.",
            ),
            (
                "-log-level LEVEL",
                "--log-level LEVEL",
                "How much to log: DEBUG, INFO, WARNING (default) or ERROR.",
            ),
        ),
    ),
    (
        "Checks (print in a console, then exit)",
        (
            ("-monitors", "--monitors", "List the monitors Mercy AI can see."),
            (
                "-calibrate",
                "--calibrate",
                "Show the live health match while you play, to choose the switch point.",
            ),
            ("-benchmark", "--benchmark", "Time how fast the screen is read."),
            (
                "-capture-modes",
                "--capture-modes",
                "Time the screen grab with and without CAPTUREBLT.",
            ),
            (
                "-dump-region NAME",
                "--dump-region NAME",
                "Save what Mercy AI sees as NAME.<stage>.png.",
            ),
        ),
    ),
    (
        "Other",
        (
            ("-h  -help", "--h  --help", "Show this help."),
            ("-version", "--version", "Show the version."),
        ),
    ),
)


def help_text(width: int = 80) -> str:
    """The ``-help`` page: every option in both spellings, grouped."""
    import textwrap

    short = max(len(one) for _title, rows in OPTIONS for one, _two, _what in rows) + 3
    long = max(len(two) for _title, rows in OPTIONS for _one, two, _what in rows) + 3
    indent = 2 + short + long
    lines = [
        f"Mercy AI {__version__}",
        "Overwatch Mercy pocket-healing assistant.",
        "",
        "Usage",
        '  "Mercy AI.exe" [option]',
        "",
        "Every option works with one dash or two: -cli and --cli are the same.",
    ]
    for title, rows in OPTIONS:
        lines += ["", title]
        for one, two, what in rows:
            wrapped = textwrap.wrap(what, max(20, width - indent - 1))
            lines.append(f"  {one:<{short}}{two:<{long}}{wrapped[0]}")
            lines += [" " * indent + more for more in wrapped[1:]]
    return "\n".join(lines)


def _both(name: str) -> tuple[str, str]:
    return f"-{name}", f"--{name}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _Parser(prog="Mercy AI", add_help=False, allow_abbrev=False)
    parser.add_argument("-h", "--h", *_both("help"), dest="help", action="store_true")
    parser.add_argument(*_both("version"), dest="version", action="store_true")
    parser.add_argument(*_both("cli"), dest="cli", action="store_true")
    parser.add_argument(*_both("config"), dest="config", type=Path, default=CONFIG_PATH)
    parser.add_argument(
        *_both("log-level"),
        dest="log_level",
        default="WARNING",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    for name in ("monitors", "calibrate", "benchmark", "capture-modes"):
        parser.add_argument(*_both(name), dest=name.replace("-", "_"), action="store_true")
    parser.add_argument(*_both("dump-region"), dest="dump_region", type=Path)
    parser.add_argument(*_both("self-check"), dest="self_check", action="store_true")
    return parser.parse_args(argv)


def is_diagnostic(args: argparse.Namespace) -> bool:
    return bool(
        args.monitors or args.calibrate or args.benchmark or args.capture_modes or args.dump_region
    )


# --------------------------------------------------------------------------- #
# Streams and failures for a windowed build
# --------------------------------------------------------------------------- #


def ensure_streams(target: object = None) -> list[str]:
    """Replace missing standard streams with in-memory ones.  Returns what it fixed.

    Anything that writes to a ``None`` stream dies with "'NoneType' object has
    no attribute 'write'", shown as a bare "Unhandled exception in script"
    dialog because the traceback has nowhere to go either. ``StringIO`` keeps
    what was written, so it can still be shown (see :func:`report_failure`),
    and an empty stdin makes ``input()`` end cleanly with ``EOFError``.
    """
    target = target if target is not None else sys
    replaced = []
    for name in STREAMS:
        if getattr(target, name, None) is None:
            setattr(target, name, io.StringIO())
            replaced.append(name)
    return replaced


MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40


def _message_box(message: str, icon: int = MB_ICONERROR) -> None:  # pragma: no cover
    import ctypes

    ctypes.windll.user32.MessageBoxW(0, message, "Mercy AI", icon)


def report_failure(text: str, show: object = None) -> str:
    """Show a startup failure in a message box, since a windowed build has no console."""
    message = _ANSI.sub("", text).strip() or "Mercy AI could not start, and did not say why."
    (show or _message_box)(message)  # type: ignore[operator]
    return message


def _captured(names: list[str]) -> str:
    return "\n".join(
        stream.getvalue()
        for name in names
        if isinstance(stream := getattr(sys, name, None), io.StringIO)
    )


# --------------------------------------------------------------------------- #
# Starting up
# --------------------------------------------------------------------------- #


def _single_instance() -> str | None:
    """Take the one-copy-at-a-time lock.  Returns what to tell the player, or ``None``.

    Two copies fight over the same keys whichever face they wear, so the window
    and the text interface share one lock. If another copy is open it is
    brought to the front, which is all the player wanted, so there is nothing
    to say; only when it cannot be found is there a message.
    """
    from .winutil import SingleInstanceError, acquire_single_instance, focus_existing_window

    try:
        mutex = acquire_single_instance()
        del mutex  # the handle is kept alive by the process itself
    except SingleInstanceError as exc:
        return "" if focus_existing_window() else str(exc)
    return None


def _to_terminal(text: str) -> None:
    """Print *text* where the player typed the command.

    A run from source already has its terminal. The windowed exe prints into
    the console it was started from, and only when there is none (a shortcut
    with -help on it, say) opens a console of its own to show the text.
    """
    if not isinstance(sys.stdout, io.StringIO):
        print(text)
        return
    from .cli.console import ConsoleError, attach_console, print_to_parent_console

    if print_to_parent_console(text):
        return
    try:
        with attach_console(full_screen=False):
            print(text)
    except ConsoleError:
        _message_box(text, MB_ICONINFORMATION)


def _tell(message: str) -> None:
    """Show *message* where the player can read it: a box, or the terminal."""
    if isinstance(sys.stdout, io.StringIO):
        report_failure(message)
    else:
        print(message, file=sys.stderr)


def _run_gui(args: argparse.Namespace) -> int:
    if (problem := _single_instance()) is not None:
        if problem:
            _tell(problem)
        return 0
    try:
        from .ui.app import run_gui
    except ImportError as exc:
        raise MercyAIError(
            f"The window needs the 'pywebview' package, which is not installed ({exc}).\n"
            f"The text version works without it: run Mercy AI with -cli"
        ) from exc
    return run_gui(args.config)


def _run_diagnostic(args: argparse.Namespace) -> int:
    from .config import read_config
    from .diagnostics import benchmark, calibrate, compare_capture_modes, dump_region
    from .winutil import describe_monitors, detect_screen_height, list_monitors, primary_monitor

    if args.monitors:
        monitors = list_monitors()
        print(describe_monitors(monitors))
        primary = primary_monitor(monitors)
        print(f"\nprimary: {primary['width']}x{primary['height']}")
        detected = detect_screen_height(monitors)
        print(f"matching Mercy AI resolution: {detected or 'none'}")
        return 0

    if not args.config.is_file():
        raise MercyAIError(
            f"There is no config.xml yet ({args.config}). Open Mercy AI once and finish "
            f"setup, then try this again."
        )
    from .assets import load_assets
    from .ui.runner import apply_capture_settings

    config = read_config(args.config)
    apply_capture_settings(config)
    assets = load_assets(config.resolution)
    if args.dump_region:
        written = dump_region(assets, args.dump_region)
        print("Wrote:\n  " + "\n  ".join(str(path) for path in written))
    elif args.capture_modes:
        compare_capture_modes(assets)
    elif args.benchmark:
        benchmark(assets)
    elif args.calibrate:
        calibrate(assets, config)
    return 0


def _in_console(args: argparse.Namespace) -> int:
    """Run the text interface or a diagnostic in a console of its own."""
    from .cli.console import ConsoleError, attach_console

    # Before any console exists, so a second launch does not flash a window.
    if args.cli and (problem := _single_instance()) is not None:
        if problem:
            _tell(problem)
        return 0
    with contextlib.ExitStack() as stack:
        try:
            console = stack.enter_context(attach_console(full_screen=args.cli))
        except ConsoleError as exc:
            what = "The text interface" if args.cli else "The diagnostic"
            _tell(f"{what} could not start: {exc}")
            return 1
        try:
            if args.cli:
                from .cli import run_cli

                return run_cli(args.config, console, args.log_level)
            return _run_diagnostic(args)
        except KeyboardInterrupt:
            return 0
        except MercyAIError as exc:
            # Shown before the console is closed: a console this program
            # opened disappears with it.
            console.show_failure(str(exc))
            return 1
        except Exception:
            import traceback

            console.show_failure(traceback.format_exc())
            return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    replaced = ensure_streams()
    try:
        try:
            args = parse_args(argv)
        except UsageError as exc:
            _to_terminal(f'Mercy AI: {exc}.\nRun "Mercy AI.exe" -help to see every option.')
            return 2
        if args.help:
            _to_terminal(help_text())
            return 0
        if args.version:
            _to_terminal(f"Mercy AI {__version__}")
            return 0
        if args.self_check:
            from .selfcheck import self_check

            return self_check()

        from .logging_setup import setup_logging
        from .winutil import ensure_dpi_aware

        # Before anything reads screen sizes: the template coordinates are
        # physical pixels.
        dpi_mode = ensure_dpi_aware()
        if args.cli or is_diagnostic(args):
            return _in_console(args)
        setup_logging(args.log_level)
        log.info("Mercy AI %s starting (dpi=%s)", __version__, dpi_mode)
        return _run_gui(args)
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1
        if replaced and code:
            report_failure(_captured(replaced))
        return code
    except MercyAIError as exc:
        if replaced:
            report_failure(str(exc))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    except BaseException:
        if replaced:
            import traceback

            report_failure(traceback.format_exc())
        raise
