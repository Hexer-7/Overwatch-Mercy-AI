# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The object the page calls into.

Every method here is reachable from JavaScript as ``pywebview.api.<name>`` and
returns plain JSON; the text interface calls the same methods directly.  It is
glue and stays glue: the decisions live in
:mod:`mercy_ai.ui.model` (pure) and :mod:`mercy_ai.ui.runner` (the loop), and
this module only sequences them and writes the file.

Two rules hold throughout:

* **Nothing raises across the bridge.**  An exception on this side becomes a
  rejected promise in the page, which in practice means a control that silently
  stops working.  Every entry point returns ``{"ok": false, "problems": [...]}``
  instead, and the page has somewhere to show it.
* **Nothing is written until it validates.**  ``config.xml`` is the file the
  program cannot start without, so a Settings tab must never be able to
  corrupt it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Config, config_to_xml, read_config, write_config_atomic
from ..errors import MercyAIError
from .model import (
    UNBINDABLE_KEY,
    config_to_ui,
    normalise_key,
    range_problems,
    settings_problems,
    typed_problems,
    ui_to_config,
)
from .runner import LoopRunner

log = logging.getLogger(__name__)

__all__ = ["LINKS", "Bridge"]

#: Everywhere the About tab links to. The page asks by name, never by address,
#: so nothing in the page can make the program open anything else.
LINKS: dict[str, str] = {
    "instagram": "https://www.instagram.com/_1_B/",
    "github": "https://github.com/Hexer-7",
    "repo": "https://github.com/Hexer-7/Overwatch-Mercy-AI",
}


def _open_in_shell(path: Path) -> None:
    """Show *path* to the user with whatever the OS uses for it.

    ``os.startfile`` is Windows-only and this program is Windows-only, but the
    fallback keeps the module importable (and testable) on non-Windows test runs.
    """
    if sys.platform == "win32":
        os.startfile(path)
    else:  # pragma: no cover - non-Windows test runs only
        subprocess.Popen(["xdg-open", str(path)])


def _explorer() -> str:  # pragma: no cover - Windows only
    """The full path of the real ``explorer.exe``.

    Never the bare name: Windows looks for that in the program's own folder
    and the current directory before the system ones, and Mercy AI usually
    sits in Downloads or on the Desktop, where anything can drop a file of
    that name -- which this elevated program would then start as
    administrator. The folder comes from the system call, not ``%WINDIR%``,
    which a user can override.
    """
    import ctypes

    buffer = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, len(buffer)):
        raise OSError("could not find the Windows folder")
    return str(Path(buffer.value) / "explorer.exe")


def _open_url(url: str) -> None:
    """Open *url* in the player's own browser.

    Mercy AI runs as administrator, and a browser started straight from it
    would run as administrator too. Handing the address to ``explorer.exe``
    opens it through the desktop's own shell instead, which is not elevated.
    """
    if sys.platform == "win32":
        subprocess.Popen([_explorer(), url])
    else:  # pragma: no cover - non-Windows test runs only
        webbrowser.open(url)


class Bridge:
    """Exposed to the web view as ``pywebview.api``.

    **Every attribute on this object is private, and that is load-bearing.**
    When pywebview builds the JavaScript proxy it walks ``dir()`` of the api
    object and recurses into every public attribute that is not callable,
    looking for more methods to expose (``webview/util.py``,
    ``inject_pywebview``).  Handing it the ``Window`` -- or the assets, which are
    numpy arrays -- sends it into the WinForms object graph, where
    ``AccessibilityObject.Bounds.Empty.Empty.Empty...`` recurses forever because
    pythonnet returns a freshly boxed object each time and the id-based cycle
    guard never fires.  The symptom is a window that renders its HTML and then
    never fires ``pywebviewready``: a blank page with no error anywhere.

    So the public surface of this class *is* the JS API, exactly, and nothing
    else.  ``loader`` and ``opener`` are injected so the whole surface can be
    tested without OpenCV decoding template images or Explorer windows opening.
    """

    def __init__(
        self,
        *,
        config: Config,
        config_path: Path,
        assets: Any,
        runner: LoopRunner,
        environment: dict[str, Any] | None = None,
        loader: Any = None,
        opener: Any = None,
        url_opener: Any = None,
        window: Any = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._assets = assets
        self._runner = runner
        self._environment = environment or {}
        self._loader = loader
        self._opener = opener or _open_in_shell
        self._url_opener = url_opener or _open_url
        self._window = window
        self._shown = threading.Event()
        # Set when the window opened in setup because config.xml was missing or
        # unreadable: finishing setup is then *meant* to write a fresh file over
        # it. Otherwise an unreadable file is the player's hand edit, and saving
        # over it would silently throw that edit away.
        self._setup_replaces_file = bool(self._environment.get("needs_setup"))
        # Every call from the page arrives on a thread of its own. Saves and
        # starts take turns, so two quick clicks cannot interleave reading the
        # file, writing it and restarting the loop: the second sees what the
        # first stored.
        self._busy = threading.RLock()
        # The last loop error and the region explanation worked out for it.
        self._explained: tuple[str | None, str | None] = (None, None)

    def _attach(self, window: Any) -> None:
        """Give the bridge the window it lives in, after ``create_window``."""
        self._window = window

    # -- first paint --------------------------------------------------------- #

    def ready(self) -> dict[str, Any]:
        """Called by the page once it has drawn itself.  Reveals the window.

        The window is created hidden. A web view paints white until its first
        frame, so showing it immediately gives the empty flash and the
        assembling-in-stages look that makes a desktop program feel like a web
        page loading. Native applications do not do that, and neither does this
        one: nothing appears until there is something worth seeing.
        """
        self._show_once()
        return {"ok": True}

    def _show_once(self) -> bool:
        """Show the window at most once.  Returns whether this call did it."""
        if self._window is None or self._shown.is_set():
            return False
        self._shown.set()
        try:
            self._window.show()
        except Exception:  # pragma: no cover - platform specific
            log.debug("could not show the window", exc_info=True)
        return True

    def _watchdog(self, _window: Any = None, timeout: float = 4.0) -> None:
        """Reveal the window even if the page never says it is ready.

        Holding the window back until the page reports in means a JavaScript
        error before that point would leave the program running with no window
        at all -- invisible, unclosable except through Task Manager. That is a
        far worse failure than a brief flash, so the wait always ends.
        """
        if self._shown.wait(timeout):
            return
        log.warning("the page did not report ready within %.1fs; showing anyway", timeout)
        self._show_once()

    # -- reading ------------------------------------------------------------ #

    def get_state(self) -> dict[str, Any]:
        """One call the page can poll: settings, live status and the machine."""
        return {
            "config": config_to_ui(self._config),
            "status": self._runner.hub.snapshot(),
            "env": self._environment,
            "version": __version__,
        }

    def get_status(self) -> dict[str, Any]:
        """Just the live half.  Polled ~10x a second, so it stays small.

        A loop that died is the one case worth spending extra work on: the raw
        exception from the capture layer names a Win32 call and lists four
        possible causes without saying which. The region check knows which, so
        it is appended. It runs once per error, not on every poll: it walks the
        monitors and logs.
        """
        snapshot = self._runner.hub.snapshot()
        error = snapshot.get("error")
        if not error:
            self._explained = (None, None)
        else:
            if self._explained[0] != error:
                self._explained = (error, self._region_problem())
            if reason := self._explained[1]:
                snapshot["error"] = f"{error}\n\n{reason}"
        return snapshot

    # -- writing ------------------------------------------------------------ #

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate, write ``config.xml``, and restart the loop if it was running."""
        with self._busy:
            return self._save(payload or {})

    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        base, broken = self._current_config()
        if broken:
            # Nothing is written: the file holds the player's edit, mistake and
            # all, and saving would replace every line of it.
            return {"ok": False, "problems": [broken], "config": config_to_ui(base)}
        # A box that is empty, not a number, or outside its range is refused,
        # and the stored config is handed back so the box shows the value that
        # is really in effect.
        if problems := typed_problems(payload):
            return {"ok": False, "problems": problems, "config": config_to_ui(base)}
        try:
            candidate = ui_to_config(base, payload)
        except MercyAIError as exc:
            return {"ok": False, "problems": [str(exc)], "config": config_to_ui(base)}
        if problems := range_problems(payload, candidate):
            return {"ok": False, "problems": problems, "config": config_to_ui(base)}
        return self._commit(candidate, base, set(payload))

    def _current_config(self) -> tuple[Config, str | None]:
        """The settings as config.xml has them right now, or why it cannot say.

        Some settings live only in the file, and the player may edit it while
        the window is open, so every save starts from the file rather than the
        copy read at start-up, which would silently undo that edit. With no file
        yet (first run), or in the setup that replaces an unreadable one, the
        window's copy is used.

        Returns ``(config, None)``, or the window's copy plus a sentence for the
        player when the file exists but has a mistake in it.
        """
        if self._setup_replaces_file or not self._config_path.is_file():
            return self._config, None
        try:
            return read_config(self._config_path), None
        except (MercyAIError, OSError, ValueError, OverflowError) as exc:
            return self._config, (
                f"Nothing was saved, because config.xml has a mistake in it: {exc} "
                f"Fix it in config.xml (next to Mercy AI.exe) or undo your edit, then try again."
            )

    def _commit(
        self, candidate: Config, base: Config | None = None, changed: set[str] | None = None
    ) -> dict[str, Any]:
        """Write *candidate* and apply it.  *base* is what it was built on."""
        problems = settings_problems(candidate, changed or set())
        if problems:
            return {"ok": False, "problems": problems, "config": config_to_ui(base or self._config)}

        # Reloading the templates is only needed when the resolution moved, and
        # it is the one part of applying settings that can fail on its own -- a
        # missing data/<res> folder -- so it happens before anything is written:
        # a resolution with nothing to match against is refused, not saved.
        assets = self._assets
        if assets is None or candidate.resolution != self._config.resolution:
            try:
                assets = self._load_assets(candidate.resolution)
            except MercyAIError as exc:
                return {
                    "ok": False,
                    "problems": [str(exc)],
                    "config": config_to_ui(base or self._config),
                }

        try:
            write_config_atomic(config_to_xml(candidate), self._config_path)
        except OSError as exc:
            return {"ok": False, "problems": [f"Could not write {self._config_path}: {exc}"]}
        # The file on disk is now a good one, so setup no longer replaces it and
        # the window stops reporting the old read error.
        self._setup_replaces_file = False
        self._environment["needs_setup"] = False
        self._environment["config_error"] = None
        self._environment.pop("asset_error", None)

        self._config = candidate
        self._assets = assets
        # The region explanation was worked out for the old settings.
        self._explained = (None, None)
        try:
            self._runner.restart(candidate, assets)
        except MercyAIError as exc:
            return {"ok": False, "problems": [str(exc)], "config": config_to_ui(candidate)}
        return {"ok": True, "problems": [], "config": config_to_ui(candidate)}

    def _load_assets(self, resolution: str) -> Any:
        if self._loader is not None:
            return self._loader(resolution)
        from ..assets import load_assets

        return load_assets(resolution)

    # -- the loop ------------------------------------------------------------ #

    def set_running(self, running: bool) -> dict[str, Any]:
        """Start or stop watching the screen, from the window's own control."""
        with self._busy:
            return self._set_running(running)

    def _set_running(self, running: bool) -> dict[str, Any]:
        if running and self._assets is None:
            return {
                "ok": False,
                "problems": [
                    f"The templates for {self._config.resolution}p could not be loaded, "
                    f"so there is nothing to match against. Pick another resolution."
                ],
            }
        if running and (blocker := self._region_problem()):
            return {"ok": False, "problems": [blocker]}
        try:
            if running:
                self._runner.start()
            else:
                self._runner.stop()
        except MercyAIError as exc:
            self._runner.hub.fail(str(exc))
            return {"ok": False, "problems": [str(exc)]}
        except Exception as exc:
            log.exception("could not change the run state")
            self._runner.hub.fail(str(exc) or exc.__class__.__name__)
            return {"ok": False, "problems": [str(exc) or exc.__class__.__name__]}
        return {"ok": True, "problems": [], "status": self._runner.hub.snapshot()}

    def _region_problem(self) -> str | None:
        """Say why the screen grab is going to fail, *before* it fails.

        Without this, pressing Start on a mismatched resolution produces a raw
        Windows error about ``BitBlt`` failing, which names a graphics call the
        user has never heard of, offers four possible causes, and buries the
        real one -- the configured resolution is a screen *height*, and theirs
        is not it.

        Any failure to diagnose is swallowed: a broken diagnostic must never be
        the thing that stops the program from running.
        """
        if self._assets is None:
            return None
        try:
            from ..geometry import union_region
            from ..winutil import diagnose_region, list_monitors

            region = union_region(self._assets.geometry.health, self._assets.geometry.bomb)
            problem = diagnose_region(region, list_monitors(), self._config.resolution)
            # Logged at INFO, and logged either way. "Did the pre-flight run, and
            # what did it decide?" is otherwise unanswerable from a bug report,
            # which is exactly the question this check was added to settle.
            log.info(
                "region check for %s (%dx%d at %d,%d): %s",
                self._config.resolution,
                region.width,
                region.height,
                region.x,
                region.y,
                "ok" if problem is None else problem.splitlines()[0],
            )
            return problem
        except Exception:
            log.debug("could not check the capture region", exc_info=True)
            return None

    def toggle_pause(self) -> dict[str, Any]:
        return {"ok": True, "paused": self._runner.toggle_pause()}

    # -- odds and ends -------------------------------------------------------- #

    def capture_key(self, code: str, key: str = "") -> dict[str, Any]:
        """Name the key the user just pressed, or say it cannot be bound."""
        name = normalise_key(str(code or ""), str(key or ""))
        if name is None:
            return {
                "ok": False,
                "problems": [UNBINDABLE_KEY],
            }
        return {"ok": True, "key": name}

    def open_link(self, name: str) -> dict[str, Any]:
        """Open one of :data:`LINKS` in the browser, by its name."""
        url = LINKS.get(str(name or ""))
        if url is None:
            return {"ok": False, "problems": ["That link is not one Mercy AI knows."]}
        try:
            self._url_opener(url)
        except OSError as exc:
            return {"ok": False, "problems": [f"Could not open {url}: {exc}"]}
        return {"ok": True, "problems": []}

    def open_folder(self) -> dict[str, Any]:
        return self._reveal(self._config_path.parent, "The program folder is missing.")

    def _reveal(self, path: Path, missing: str) -> dict[str, Any]:
        if not path.exists():
            return {"ok": False, "problems": [missing]}
        try:
            self._opener(path)
        except OSError as exc:
            return {"ok": False, "problems": [str(exc)]}
        return {"ok": True, "problems": []}

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def close(self) -> None:
        """Stop the loop first, then the window.

        Order matters: the loop holds real keys down in the game, and destroying
        the window first would leave that teardown racing a process exit.
        """
        try:
            self._runner.stop()
        finally:
            if self._window is not None:
                self._window.destroy()
