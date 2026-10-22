# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The console under the text interface.

The shipped exe is a windowed, always-elevated program, so it starts with no
console at all. :class:`WindowsConsole` makes one (``AllocConsole``; never
``AttachConsole``, because a shell does not wait for a windowed program and
both would read the same keys), switches it to a full-screen raw mode, and
puts everything back on the way out. :class:`PosixConsole` does the same job
in a Unix terminal, so the interface can be tried from source anywhere, and
:class:`FakeConsole` stands in for both in the tests and the self-check.

All three draw a frame (a list of lines) and return keys as short names:
``"up"``, ``"enter"``, ``"shift+tab"``, ``"ctrl+c"``, ``"resize"``, or the
character itself. Letters are reported by the key's position, not by the
character the keyboard layout gives, so ``q`` and ``p`` work on an Arabic
layout too.

Frames are written with ``WriteConsoleW`` (UTF-16, so the code page does not
matter), only the rows that changed, and never the bottom-right cell, which
would scroll the screen. While the interface is on screen, ``sys.stdout`` and
``sys.stderr`` point at memory: a stray print or a library's traceback must
not tear the frame.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Protocol

from ..winutil import WINDOW_TITLE
from .text import char_width, strip_ansi

__all__ = [
    "Console",
    "ConsoleError",
    "FakeConsole",
    "FrameDiffer",
    "PosixConsole",
    "WindowsConsole",
    "attach_console",
    "decode_ansi",
    "decode_key",
    "print_to_parent_console",
]

IS_WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------- #
# Win32 types and structures, with fixed-width fields so the layouts (and the
# key decoding) can be tested on any platform
# --------------------------------------------------------------------------- #

WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
SHORT = ctypes.c_int16
UINT = ctypes.c_uint32
WCHAR16 = ctypes.c_uint16  # ctypes.c_wchar is 4 bytes on Linux; keep the layout honest
HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p


class COORD(ctypes.Structure):
    _fields_ = [("X", SHORT), ("Y", SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", SHORT), ("Top", SHORT), ("Right", SHORT), ("Bottom", SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", BOOL),
        ("wRepeatCount", WORD),
        ("wVirtualKeyCode", WORD),
        ("wVirtualScanCode", WORD),
        ("UnicodeChar", WCHAR16),
        ("dwControlKeyState", DWORD),
    ]


class MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("dwMousePosition", COORD),
        ("dwButtonState", DWORD),
        ("dwControlKeyState", DWORD),
        ("dwEventFlags", DWORD),
    ]


class WINDOW_BUFFER_SIZE_RECORD(ctypes.Structure):
    _fields_ = [("dwSize", COORD)]


class _EVENT(ctypes.Union):
    _fields_ = [
        ("KeyEvent", KEY_EVENT_RECORD),
        ("MouseEvent", MOUSE_EVENT_RECORD),
        ("WindowBufferSizeEvent", WINDOW_BUFFER_SIZE_RECORD),
        ("MenuEvent", UINT),
        ("FocusEvent", BOOL),
    ]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", WORD), ("Event", _EVENT)]


class CONSOLE_CURSOR_INFO(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("bVisible", BOOL)]


class CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("nFont", DWORD),
        ("dwFontSize", COORD),
        ("FontFamily", UINT),
        ("FontWeight", UINT),
        ("FaceName", WCHAR16 * 32),
    ]


assert ctypes.sizeof(KEY_EVENT_RECORD) == 16
assert ctypes.sizeof(INPUT_RECORD) == 20
assert ctypes.sizeof(CONSOLE_FONT_INFOEX) == 84

# Constants -------------------------------------------------------------- #
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING = 1, 2, 3
CONSOLE_TEXTMODE_BUFFER = 1
ERROR_ACCESS_DENIED = 5
ATTACH_PARENT_PROCESS = 0xFFFFFFFF  # (DWORD)-1
WAIT_OBJECT_0 = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# input modes
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_INSERT_MODE = 0x0020
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
# output modes
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
DISABLE_NEWLINE_AUTO_RETURN = 0x0008

#: Raw full-screen input: no line editing, no echo, Ctrl+C as a key, QuickEdit
#: off (ENABLE_EXTENDED_FLAGS is what makes clearing QuickEdit stick), resize
#: events on, VT input off so keys arrive as KEY_EVENT records, not escapes.
RAW_INPUT_MODE = ENABLE_EXTENDED_FLAGS | ENABLE_WINDOW_INPUT
VT_OUTPUT_MODE = (
    ENABLE_PROCESSED_OUTPUT
    | ENABLE_WRAP_AT_EOL_OUTPUT
    | ENABLE_VIRTUAL_TERMINAL_PROCESSING
    | DISABLE_NEWLINE_AUTO_RETURN
)

KEY_EVENT, MOUSE_EVENT, WINDOW_BUFFER_SIZE_EVENT = 0x1, 0x2, 0x4
RIGHT_ALT, LEFT_ALT, RIGHT_CTRL, LEFT_CTRL, SHIFT = 0x1, 0x2, 0x4, 0x8, 0x10
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT = 0, 1, 2
CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 5, 6
GA_ROOTOWNER = 3
TMPF_TRUETYPE = 0x04
CJK_CODEPAGES = frozenset({932, 936, 949, 950})

#: Seconds the close handler may spend. Windows allows SPI_GETHUNGAPPTIMEOUT
#: (5000 ms by default) for CTRL_CLOSE_EVENT and then kills the process.
CLOSE_BUDGET = 3.5


class ConsoleError(RuntimeError):
    """A console could not be opened or put into the mode the interface needs."""


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])")


def fit(line: str, width: int) -> str:
    """Cut or pad *line* to exactly *width* cells, keeping its colour codes."""
    out: list[str] = []
    used = 0
    for token in _TOKEN_RE.split(line):
        if not token:
            continue
        if token.startswith("\x1b["):
            out.append(token)
            continue
        for char in token:
            w = char_width(char)
            if used + w > width:
                break
            out.append(char)
            used += w
        else:
            continue
        break
    out.append("\x1b[0m")
    out.append(" " * (width - used))
    return "".join(out)


class FrameDiffer:
    """Turn a whole frame into the fewest writes that make the screen match it.

    Each row is padded to the exact width, so no erase-in-line is ever needed
    (``\\x1b[K`` in a pending-wrap state would erase the last cell). The last
    row stops one cell short so the bottom-right cell is never written, which
    is what could scroll the buffer. Every frame is wrapped in DEC mode 2026
    (synchronized output): Windows Terminal 1.23+ presents it atomically, and
    conhost ignores the unknown private mode.
    """

    def __init__(self, sync: bool = True) -> None:
        self.sync = sync
        self.prev: list[str] | None = None
        self.size: tuple[int, int] | None = None

    def invalidate(self) -> None:
        self.prev = None

    def fitted(self, lines: Sequence[str], cols: int, rows: int) -> list[str]:
        return [
            fit(lines[i] if i < len(lines) else "", max(0, cols - (1 if i == rows - 1 else 0)))
            for i in range(rows)
        ]

    def changes(self, lines: Sequence[str], cols: int, rows: int) -> list[tuple[int, str]]:
        """(row, fitted line) for every row that differs; all rows after a resize."""
        new = self.fitted(lines, cols, rows)
        full = self.prev is None or self.size != (cols, rows)
        rows_out = [
            (i, line)
            for i, line in enumerate(new)
            if full or self.prev[i] != line  # type: ignore[index]
        ]
        self.prev, self.size = new, (cols, rows)
        return rows_out

    def render(self, lines: Sequence[str], cols: int, rows: int) -> str:
        full = self.prev is None or self.size != (cols, rows)
        parts = ["\x1b[0m\x1b[2J"] if full else []
        parts += [f"\x1b[{i + 1};1H{line}" for i, line in self.changes(lines, cols, rows)]
        if not parts:
            return ""
        body = "".join(parts)
        return f"\x1b[?2026h{body}\x1b[?2026l" if self.sync else body


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #

VK_NAMES = {
    0x08: "backspace", 0x0D: "enter", 0x1B: "esc", 0x20: "space",
    0x21: "pgup", 0x22: "pgdn", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2D: "insert", 0x2E: "delete",
    **{0x70 + i: f"f{i + 1}" for i in range(12)},
}  # fmt: skip
VK_TAB = 0x09
#: Modifier and lock keys on their own are not keys the interface uses.
VK_IGNORED = frozenset(
    {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0x90, 0x91, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
)
#: Number pad digits and the decimal key, for typing numbers.
VK_NUMPAD = {**{0x60 + i: str(i) for i in range(10)}, 0x6E: "."}
VK_PERIOD, VK_COMMA = 0xBE, 0xBC


def decode_key(down: bool, vk: int, char: int, state: int) -> str | None:
    """One console key record as a key name, or ``None`` to ignore it."""
    if not down or vk in VK_IGNORED:
        return None
    if vk == VK_TAB:
        return "shift+tab" if state & SHIFT else "tab"
    ctrl = bool(state & (LEFT_CTRL | RIGHT_CTRL))
    alt = bool(state & (LEFT_ALT | RIGHT_ALT))
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if ctrl and not alt and 0x41 <= vk <= 0x5A:
        return "ctrl+" + chr(vk).lower()
    if not ctrl and not alt:
        # By position, whatever the layout: P is "p" on an Arabic layout too.
        if 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if 0x30 <= vk <= 0x39 and not state & SHIFT:
            return chr(vk)
        if vk in VK_NUMPAD:
            return VK_NUMPAD[vk]
        if vk in (VK_PERIOD, VK_COMMA) and not state & SHIFT:
            return "."
    if char and not 0xD800 <= char <= 0xDFFF:
        text = chr(char)
        if text.isprintable():
            return text
    return None


def decode_record(record: INPUT_RECORD) -> list[str]:
    """The keys in one console input record (a held key repeats)."""
    if record.EventType == WINDOW_BUFFER_SIZE_EVENT:
        return ["resize"]
    if record.EventType != KEY_EVENT:
        return []
    key = record.Event.KeyEvent
    name = decode_key(
        bool(key.bKeyDown), key.wVirtualKeyCode, key.UnicodeChar, key.dwControlKeyState
    )
    if name is None:
        return []
    return [name] * max(1, min(int(key.wRepeatCount), 4))


#: Escape sequences a Unix terminal sends.
_ANSI_KEYS = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "[H": "home", "[F": "end", "OH": "home", "OF": "end",
    "[1~": "home", "[4~": "end", "[5~": "pgup", "[6~": "pgdn",
    "[2~": "insert", "[3~": "delete", "[Z": "shift+tab",
    "OP": "f1", "OQ": "f2", "OR": "f3", "OS": "f4",
}  # fmt: skip


def decode_ansi(data: str) -> list[str]:
    """The keys in a chunk read from a Unix terminal."""
    keys: list[str] = []
    index = 0
    while index < len(data):
        char = data[index]
        if char == "\x1b":
            rest = data[index + 1 :]
            match = re.match(r"(\[[0-9;]*[A-Za-z~]|O[A-Za-z])", rest)
            if match:
                keys.append(_ANSI_KEYS.get(match.group(1), ""))
                index += 1 + len(match.group(1))
                continue
            keys.append("esc")
        elif char in "\r\n":
            keys.append("enter")
        elif char == "\t":
            keys.append("tab")
        elif char in "\x7f\x08":
            keys.append("backspace")
        elif char == "\x03":
            keys.append("ctrl+c")
        elif char == " ":
            keys.append("space")
        elif char.isprintable():
            keys.append(char.lower() if char.isascii() and char.isalpha() else char)
        index += 1
    return [key for key in keys if key]


# --------------------------------------------------------------------------- #
# The interface, and the fake
# --------------------------------------------------------------------------- #


class Console(Protocol):
    unicode: bool
    color: bool

    def size(self) -> tuple[int, int]: ...
    def read_key(self, timeout: float) -> str | None: ...
    def write(self, lines: Sequence[str]) -> None: ...
    def invalidate(self) -> None: ...
    def flush_input(self) -> None: ...
    def is_foreground(self) -> bool: ...
    def show_failure(self, text: str) -> None: ...


class FakeConsole:
    """A scripted console for tests. Never touches the operating system.

    Each item of *keys* is returned by one ``read_key`` call; ``None`` stands
    for a tick with no key. When the script runs out ``exhausted`` turns True.
    """

    def __init__(
        self, cols: int = 100, rows: int = 30, keys: Iterable[str | None] = (), unicode: bool = True
    ) -> None:
        self.cols, self.rows, self.unicode, self.color = cols, rows, unicode, True
        self._keys: deque[str | None] = deque(keys)
        self.exhausted = False
        self.frames: list[list[str]] = []
        self.screen: list[str] = []
        self.failures: list[str] = []
        self._differ = FrameDiffer(sync=False)

    def size(self) -> tuple[int, int]:
        return self.cols, self.rows

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self._keys.appendleft("resize")

    def feed(self, *keys: str | None) -> None:
        self._keys.extend(keys)

    def read_key(self, timeout: float = 0.0) -> str | None:
        if self._keys:
            return self._keys.popleft()
        self.exhausted = True
        return None

    def write(self, lines: Sequence[str]) -> None:
        self.screen = self._differ.fitted(lines, self.cols, self.rows)
        self.frames.append(list(self.screen))

    def invalidate(self) -> None:
        self._differ.invalidate()

    def flush_input(self) -> None:
        # A test's script stands for keys pressed later, so it is kept.
        self.flushes = getattr(self, "flushes", 0) + 1

    def is_foreground(self) -> bool:
        return True

    def show_failure(self, text: str) -> None:
        self.failures.append(text)

    def text(self) -> str:
        return "\n".join(strip_ansi(line).rstrip() for line in self.screen).rstrip("\n")


# --------------------------------------------------------------------------- #
# A Unix terminal (running from source on another platform)
# --------------------------------------------------------------------------- #


class PosixConsole:
    """The same interface in a Unix terminal: raw mode, alternate screen."""

    def __init__(self) -> None:
        self.unicode = True
        self.color = "NO_COLOR" not in os.environ
        self._fd = sys.stdin.fileno()
        self._saved: Any = None
        self._out = sys.stdout
        self._differ = FrameDiffer(sync=False)
        self._pending: deque[str] = deque()
        self._last_size: tuple[int, int] | None = None
        self._streams: dict[str, Any] = {}

    def open(self) -> None:
        if sys.platform == "win32":  # this class is for Unix terminals
            raise ConsoleError("PosixConsole needs a Unix terminal.")
        import termios
        import tty

        self._saved = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        self._out.write("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[0m\x1b[2J")
        self._out.flush()
        for name in ("stdout", "stderr"):
            self._streams[name] = getattr(sys, name)
            setattr(sys, name, io.StringIO())

    def close(self) -> None:
        if sys.platform == "win32":  # this class is for Unix terminals
            raise ConsoleError("PosixConsole needs a Unix terminal.")
        import termios

        for name, stream in self._streams.items():
            setattr(sys, name, stream)
        self._streams.clear()
        with contextlib.suppress(Exception):
            self._out.write("\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
            self._out.flush()
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def size(self) -> tuple[int, int]:
        size = os.get_terminal_size(self._out.fileno())
        return size.columns, size.lines

    def read_key(self, timeout: float) -> str | None:
        import select

        if self._pending:
            return self._pending.popleft()
        ready, _w, _x = select.select([self._fd], [], [], max(0.0, timeout))
        if ready:
            data = os.read(self._fd, 1024).decode("utf-8", "replace")
            self._pending.extend(decode_ansi(data))
            if self._pending:
                return self._pending.popleft()
        size = self.size()
        if size != self._last_size:
            self._last_size = size
            return "resize"
        return None

    def write(self, lines: Sequence[str]) -> None:
        cols, rows = self.size()
        self._last_size = (cols, rows)
        out = self._differ.render(lines, cols, rows)
        if out:
            self._out.write(out)
            self._out.flush()

    def invalidate(self) -> None:
        self._differ.invalidate()

    def flush_input(self) -> None:
        if sys.platform == "win32":  # this class is for Unix terminals
            raise ConsoleError("PosixConsole needs a Unix terminal.")
        import termios

        self._pending.clear()
        termios.tcflush(self._fd, termios.TCIFLUSH)

    def is_foreground(self) -> bool:
        return True

    def show_failure(self, text: str) -> None:
        self.close()
        self._out.write(text.rstrip() + "\n")
        self._out.flush()


# --------------------------------------------------------------------------- #
# The Windows console
# --------------------------------------------------------------------------- #

_API: Any = None
_KEEPALIVE: list[Any] = []  # ctypes callbacks must outlive every possible call


def _api() -> Any:
    """Declare every Win32 function once, on private WinDLL instances.

    Private instances, not ``ctypes.windll.kernel32``: that object is shared
    with winutil, keyboard, pynput and mss, and argtypes set here would change
    how their calls are converted.
    """
    global _API
    if _API is not None:
        return _API
    if not IS_WINDOWS:
        raise ConsoleError("A console window needs Windows.")
    from types import SimpleNamespace

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    P = ctypes.POINTER
    api = SimpleNamespace()

    def bind(dll: Any, name: str, restype: Any, *argtypes: Any) -> None:
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = restype, list(argtypes)
        setattr(api, name, fn)

    bind(k32, "AllocConsole", BOOL)
    bind(k32, "AttachConsole", BOOL, DWORD)
    bind(k32, "FreeConsole", BOOL)
    bind(k32, "GetConsoleWindow", HWND)
    bind(
        k32,
        "CreateFileW",
        HANDLE,
        ctypes.c_wchar_p,
        DWORD,
        DWORD,
        ctypes.c_void_p,
        DWORD,
        DWORD,
        HANDLE,
    )
    bind(k32, "CloseHandle", BOOL, HANDLE)
    bind(k32, "GetConsoleMode", BOOL, HANDLE, P(DWORD))
    bind(k32, "SetConsoleMode", BOOL, HANDLE, DWORD)
    bind(k32, "GetConsoleScreenBufferInfo", BOOL, HANDLE, P(CONSOLE_SCREEN_BUFFER_INFO))
    bind(k32, "WriteConsoleW", BOOL, HANDLE, ctypes.c_wchar_p, DWORD, P(DWORD), ctypes.c_void_p)
    bind(k32, "ReadConsoleInputW", BOOL, HANDLE, P(INPUT_RECORD), DWORD, P(DWORD))
    bind(k32, "GetNumberOfConsoleInputEvents", BOOL, HANDLE, P(DWORD))
    bind(k32, "FlushConsoleInputBuffer", BOOL, HANDLE)
    bind(k32, "WaitForSingleObject", DWORD, HANDLE, DWORD)
    bind(k32, "SetConsoleTitleW", BOOL, ctypes.c_wchar_p)
    bind(k32, "GetConsoleTitleW", DWORD, ctypes.c_wchar_p, DWORD)
    bind(k32, "GetConsoleOutputCP", UINT)
    bind(k32, "GetCurrentConsoleFontEx", BOOL, HANDLE, BOOL, P(CONSOLE_FONT_INFOEX))
    bind(k32, "SetCurrentConsoleFontEx", BOOL, HANDLE, BOOL, P(CONSOLE_FONT_INFOEX))
    bind(
        k32,
        "CreateConsoleScreenBuffer",
        HANDLE,
        DWORD,
        DWORD,
        ctypes.c_void_p,
        DWORD,
        ctypes.c_void_p,
    )
    bind(k32, "SetConsoleActiveScreenBuffer", BOOL, HANDLE)
    bind(k32, "SetConsoleScreenBufferSize", BOOL, HANDLE, COORD)
    bind(k32, "SetConsoleCursorPosition", BOOL, HANDLE, COORD)
    bind(k32, "SetConsoleTextAttribute", BOOL, HANDLE, WORD)
    bind(k32, "GetConsoleCursorInfo", BOOL, HANDLE, P(CONSOLE_CURSOR_INFO))
    bind(k32, "SetConsoleCursorInfo", BOOL, HANDLE, P(CONSOLE_CURSOR_INFO))
    bind(k32, "FillConsoleOutputCharacterW", BOOL, HANDLE, WCHAR16, DWORD, COORD, P(DWORD))
    bind(k32, "FillConsoleOutputAttribute", BOOL, HANDLE, WORD, DWORD, COORD, P(DWORD))
    api.PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(BOOL, DWORD)
    bind(k32, "SetConsoleCtrlHandler", BOOL, api.PHANDLER_ROUTINE, BOOL)
    bind(u32, "GetClassNameW", ctypes.c_int, HWND, ctypes.c_wchar_p, ctypes.c_int)
    bind(u32, "GetForegroundWindow", HWND)
    bind(u32, "GetAncestor", HWND, HWND, UINT)
    _API = api
    return api


def _run_with_budget(action: Callable[[], None] | None, seconds: float) -> None:
    """Run *action* on a helper thread and stop waiting after *seconds*.

    The control handler must return before Windows' deadline even if a lock
    in the loop is held; a daemon helper that overruns simply dies with us.
    """
    if action is None:
        return
    worker = threading.Thread(target=action, name="mercy-console-cleanup", daemon=True)
    worker.start()
    worker.join(seconds)


_ANSI_TO_WIN = (0, 4, 2, 6, 1, 5, 3, 7)  # ANSI colour index -> Windows FOREGROUND_* bits


class WindowsConsole:
    """A full-screen console for a windowed, elevated exe.

    ``open()``: AllocConsole (or reuse the console a source run already has;
    never AttachConsole), open CONIN$/CONOUT$ directly, save modes, raw input,
    VT output on the alternate screen (or a private Win32 screen buffer when
    VT cannot be enabled), cursor hidden, title set, Ctrl handler installed.
    ``close()`` undoes all of it in reverse and is safe to call twice.

    *on_interrupt* runs (budgeted) on Ctrl+Break, and on Ctrl+C if it ever
    arrives as a signal; *on_close* runs on CTRL_CLOSE_EVENT before Windows
    ends the process. Both must release every held game key; neither may write
    to the console. Afterwards ``read_key`` returns "interrupt".
    """

    def __init__(
        self,
        title: str = WINDOW_TITLE,
        ascii_mode: bool | None = None,
        on_interrupt: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.color = "NO_COLOR" not in os.environ
        self.full_screen = True
        self.title = title
        self._ascii_forced = ascii_mode
        self.on_interrupt, self.on_close = on_interrupt, on_close
        self.unicode = False
        self.vt = False
        self.host = "none"  # "conhost" | "conpty" | "unknown"
        self.allocated = False
        self.quit_requested = threading.Event()
        self.closing = threading.Event()
        self._in: Any = None
        self._out: Any = None  # where frames go (CONOUT$, or the legacy buffer)
        self._conout: Any = None
        self._legacy_buffer: Any = None
        self._saved: dict[str, Any] = {}
        self._pending: deque[str] = deque()
        self._differ = FrameDiffer()
        self._last_size: tuple[int, int] | None = None
        self._streams: dict[str, Any] = {}
        self._ctrl_cb: Any = None
        self._open = False

    # -- lifecycle -------------------------------------------------------- #

    def open(self, full_screen: bool = True) -> None:
        api = _api()
        self.full_screen = full_screen
        if api.AllocConsole():
            self.allocated = True
        elif ctypes.get_last_error() != ERROR_ACCESS_DENIED:  # 5 = "already has a console"
            raise ConsoleError(
                f"Windows refused to open a console (error {ctypes.get_last_error()})."
            )
        self._in = self._open_device("CONIN$")
        self._conout = self._out = self._open_device("CONOUT$")
        self.host = self._detect_host()
        self._saved["in_mode"] = self._get_mode(self._in)
        self._saved["out_mode"] = self._get_mode(self._out)
        buf = ctypes.create_unicode_buffer(1024)
        api.GetConsoleTitleW(buf, len(buf))
        self._saved["title"] = buf.value
        self._install_ctrl_handler()
        self._open = True
        if not full_screen:
            # A diagnostic prints ordinary lines: point the standard streams
            # at this console. VT is turned on so a readout can redraw its
            # line in place; close() puts the saved mode back.
            api.SetConsoleMode(
                self._out, self._saved["out_mode"] | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            self.set_title(self.title)
            self._streams = {name: getattr(sys, name) for name in ("stdin", "stdout", "stderr")}
            rebind_std_streams()
            return
        try:
            if not api.SetConsoleMode(self._in, RAW_INPUT_MODE):
                raise ConsoleError("Could not switch the console to raw input.")
            self.vt = bool(api.SetConsoleMode(self._out, VT_OUTPUT_MODE))
            if self.vt:
                # alt screen, hide cursor, no autowrap, default colours, clear
                self._emit("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[0m\x1b[2J\x1b[H")
            else:
                self._open_legacy_buffer()
            self.unicode = self._decide_unicode()
            self.set_title(self.title)
            self._guard_streams()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        api = _api()
        if not self.full_screen:
            if self.allocated:
                # The window closes with the process, so give the player a
                # chance to read what was printed.
                with contextlib.suppress(Exception, KeyboardInterrupt):
                    sys.stdout.write("\nPress Enter to close this window.")
                    sys.stdout.flush()
                    sys.stdin.readline()
            for name, stream in self._streams.items():
                setattr(sys, name, stream)
            self._streams.clear()
        try:
            if self.vt:
                self._emit("\x1b[?2026l\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
            elif self._legacy_buffer is not None:
                api.SetConsoleActiveScreenBuffer(self._conout)
                api.CloseHandle(self._legacy_buffer)
                self._legacy_buffer, self._out = None, self._conout
        except Exception:
            pass
        for handle, key in ((self._in, "in_mode"), (self._conout, "out_mode")):
            if key in self._saved:
                api.SetConsoleMode(
                    handle, self._saved[key] | (ENABLE_EXTENDED_FLAGS if key == "in_mode" else 0)
                )
        api.SetConsoleTitleW(self._saved.get("title", ""))
        api.FlushConsoleInputBuffer(self._in)
        self._restore_streams()
        if self._ctrl_cb is not None:
            api.SetConsoleCtrlHandler(self._ctrl_cb, False)  # the reference stays in _KEEPALIVE
        for handle in (self._in, self._conout):
            if handle:
                api.CloseHandle(handle)
        self._in = self._out = self._conout = None
        if self.allocated:
            api.FreeConsole()  # the window disappears; show errors *before* close()

    def show_message_and_wait(self, text: str, seconds: float = 600.0) -> None:
        """Leave full-screen, print *text* in the normal buffer, wait for a key.

        A console the process allocated vanishes when the process exits, so a
        fatal error must be shown here or it is never seen. Call before close().
        """
        if not self._open:
            return
        if self.vt:
            self._emit("\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
        elif self._legacy_buffer is not None:
            _api().SetConsoleActiveScreenBuffer(self._conout)
            self._out = self._conout
        self._emit(
            text.replace("\r\n", "\n").replace("\n", "\r\n")
            + "\r\n\r\nPress any key to close this window."
        )
        self.flush_input()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self.quit_requested.is_set():
            key = self.read_key(0.25)
            if key not in (None, "resize"):
                break

    # -- the Console interface ------------------------------------------- #

    def size(self) -> tuple[int, int]:
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not _api().GetConsoleScreenBufferInfo(self._out, ctypes.byref(info)):
            return self._last_size or (80, 25)
        w = info.srWindow
        return max(1, w.Right - w.Left + 1), max(1, w.Bottom - w.Top + 1)

    def read_key(self, timeout: float) -> str | None:
        """Next key, "resize", "interrupt", or None after *timeout* seconds."""
        if self.quit_requested.is_set() and "interrupt" not in self._pending:
            self.quit_requested.clear()
            return "interrupt"
        if self._pending:
            return self._pending.popleft()
        api = _api()
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if api.WaitForSingleObject(self._in, int(remaining * 1000) + 1) != WAIT_OBJECT_0:
                break
            self._drain_records()
            if self.quit_requested.is_set():
                self.quit_requested.clear()
                return "interrupt"
        if self._pending:
            return self._pending.popleft()
        size = self.size()  # catches window-only resizes that raise no event
        if size != self._last_size:
            self._last_size = size
            return "resize"
        return None

    def write(self, lines: Sequence[str]) -> None:
        cols, rows = self.size()
        self._last_size = (cols, rows)
        if self.vt:
            out = self._differ.render(lines, cols, rows)
            if out:
                self._emit(out)
        else:
            for row, line in self._differ.changes(lines, cols, rows):
                self._legacy_line(row, line)

    def invalidate(self) -> None:
        self._differ.invalidate()

    def flush_input(self) -> None:
        self._pending.clear()
        if self._in:
            _api().FlushConsoleInputBuffer(self._in)

    def drain_input(self, quiet: float = 0.12, limit: float = 0.5) -> None:
        """Discard input until *quiet* seconds pass with none (after a capture)."""
        api, end = _api(), time.monotonic() + limit
        while time.monotonic() < end:
            if api.WaitForSingleObject(self._in, int(quiet * 1000)) != WAIT_OBJECT_0:
                break
            api.FlushConsoleInputBuffer(self._in)
        self._pending.clear()

    def set_title(self, title: str) -> None:
        self.title = title
        _api().SetConsoleTitleW(title)

    def is_foreground(self) -> bool:
        """Is this console's window the one the user is typing into?"""
        api = _api()
        hwnd, fg = api.GetConsoleWindow(), api.GetForegroundWindow()
        if not hwnd or not fg or fg == hwnd:
            return True
        root = api.GetAncestor(hwnd, GA_ROOTOWNER)
        if root and root != hwnd:
            return fg == root  # Windows Terminal: the pseudo window is owned by the tab's window
        return self.host != "conhost"  # a ConPTY host whose window we cannot name: assume yes

    # -- internals ---------------------------------------------------------- #

    def _open_device(self, name: str) -> Any:
        # Opened by name, not GetStdHandle: correct even if a parent passed
        # redirected std handles, and independent of what AllocConsole set.
        handle = _api().CreateFileW(
            name,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise ConsoleError(f"Could not open {name} (error {ctypes.get_last_error()}).")
        return handle

    def _get_mode(self, handle: Any) -> int:
        mode = DWORD()
        if not _api().GetConsoleMode(handle, ctypes.byref(mode)):
            raise ConsoleError("That handle is not a console.")
        return int(mode.value)

    def _detect_host(self) -> str:
        api = _api()
        hwnd = api.GetConsoleWindow()
        if not hwnd:
            return "unknown"
        buf = ctypes.create_unicode_buffer(64)
        api.GetClassNameW(hwnd, buf, len(buf))
        if buf.value == "ConsoleWindowClass":
            return "conhost"
        if buf.value == "PseudoConsoleWindow":
            return "conpty"  # Windows Terminal (default-terminal handoff) or another ConPTY host
        return "unknown"

    def _decide_unicode(self) -> bool:
        if self._ascii_forced is not None:
            return not self._ascii_forced
        if not self.vt:
            return False  # "Use legacy console" or pre-1511 Windows 10: raster fonts likely
        if self.host != "conhost":
            return True  # Windows Terminal renders every glyph the CLI uses at width 1
        api = _api()
        if api.GetConsoleOutputCP() in CJK_CODEPAGES:
            return False  # conhost draws box characters double-width there
        info = CONSOLE_FONT_INFOEX()
        info.cbSize = ctypes.sizeof(info)
        if not api.GetCurrentConsoleFontEx(self._out, False, ctypes.byref(info)):
            return False
        if info.FontFamily & TMPF_TRUETYPE:
            return True
        if self.allocated:  # our own window: switch raster -> Consolas, then re-check
            face = "Consolas"
            for i in range(32):
                info.FaceName[i] = ord(face[i]) if i < len(face) else 0
            info.dwFontSize = COORD(0, 16)
            info.FontFamily, info.FontWeight = 54, 400  # FF_MODERN | FIXED_PITCH | TMPF_TRUETYPE
            api.SetCurrentConsoleFontEx(self._out, False, ctypes.byref(info))
            api.GetCurrentConsoleFontEx(self._out, False, ctypes.byref(info))
            return bool(info.FontFamily & TMPF_TRUETYPE)
        return False

    def _emit(self, text: str) -> None:
        """One WriteConsoleW per chunk: UTF-16 straight in, code page irrelevant."""
        api, written = _api(), DWORD()
        step = 16000
        for start in range(0, len(text), step):
            chunk = text[start : start + step]
            units = len(chunk.encode("utf-16-le")) // 2
            api.WriteConsoleW(self._out, chunk, units, ctypes.byref(written), None)

    def _drain_records(self) -> None:
        api, count = _api(), DWORD()
        if not api.GetNumberOfConsoleInputEvents(self._in, ctypes.byref(count)) or not count.value:
            return
        records = (INPUT_RECORD * min(int(count.value), 128))()
        read = DWORD()
        if not api.ReadConsoleInputW(self._in, records, len(records), ctypes.byref(read)):
            return
        for record in records[: read.value]:
            self._pending.extend(decode_record(record))

    def _install_ctrl_handler(self) -> None:
        api = _api()

        def handler(ctrl_type: int) -> int:  # runs on a thread Windows creates
            try:
                if ctrl_type in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
                    if not self.full_screen:
                        # A diagnostic stops on Ctrl+C the ordinary way
                        # (KeyboardInterrupt); it holds no game keys.
                        return 0
                    _run_with_budget(self.on_interrupt, 1.0)
                    self.quit_requested.set()
                    return 1  # handled: the CRT's SIGINT/SIGBREAK path never runs
                if ctrl_type in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
                    self.closing.set()
                    self.quit_requested.set()
                    _run_with_budget(self.on_close or self.on_interrupt, CLOSE_BUDGET)
                    return 1  # Windows terminates the process as soon as this returns
            except BaseException:  # an exception here would print and return 0
                return 1
            return 0

        self._ctrl_cb = api.PHANDLER_ROUTINE(handler)
        _KEEPALIVE.append(self._ctrl_cb)
        if not api.SetConsoleCtrlHandler(self._ctrl_cb, True):
            raise ConsoleError("Could not install the console close handler.")

    def _guard_streams(self) -> None:
        """While full-screen, stray print()s and tracebacks must not hit the screen.

        The keyboard library prints tracebacks from its hook thread, and any
        StreamHandler would too. They go to memory; close() restores the old
        streams, and rebind_std_streams() points them at the console if the
        caller wants an ordinary text console afterwards.
        """
        for name in ("stdout", "stderr"):
            self._streams[name] = getattr(sys, name)
            setattr(sys, name, io.StringIO())

    def _restore_streams(self) -> None:
        for name, stream in self._streams.items():
            setattr(sys, name, stream)
        self._streams.clear()

    def show_failure(self, text: str) -> None:
        if self.full_screen:
            self.show_message_and_wait(text)
        else:
            with contextlib.suppress(Exception):
                sys.stdout.write(text.rstrip() + "\n")

    # -- legacy (no VT) renderer ------------------------------------------- #

    def _open_legacy_buffer(self) -> None:
        api = _api()
        info = CONSOLE_SCREEN_BUFFER_INFO()
        api.GetConsoleScreenBufferInfo(self._conout, ctypes.byref(info))
        self._saved["attrs"] = int(info.wAttributes)
        buf = api.CreateConsoleScreenBuffer(
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            CONSOLE_TEXTMODE_BUFFER,
            None,
        )
        if not buf or buf == INVALID_HANDLE_VALUE:
            raise ConsoleError(
                "This console supports neither VT sequences nor a second screen buffer."
            )
        self._legacy_buffer = self._out = buf
        api.SetConsoleMode(buf, ENABLE_PROCESSED_OUTPUT)  # no wrap: the last cell can't scroll
        w = info.srWindow
        api.SetConsoleScreenBufferSize(buf, COORD(w.Right - w.Left + 1, w.Bottom - w.Top + 1))
        cursor = CONSOLE_CURSOR_INFO(25, 0)
        api.SetConsoleCursorInfo(buf, ctypes.byref(cursor))
        api.SetConsoleActiveScreenBuffer(buf)

    def _legacy_line(self, row: int, line: str) -> None:
        api, written = _api(), DWORD()
        base = self._saved.get("attrs", 0x07)
        fg, bg, bold, reverse = base & 0x0F, (base >> 4) & 0x0F, False, False
        api.SetConsoleCursorPosition(self._out, COORD(0, row))
        for token in _TOKEN_RE.split(line):
            if not token:
                continue
            if token.startswith("\x1b["):
                for code in (int(c) if c else 0 for c in token[2:-1].split(";")):
                    if code == 0:
                        fg, bg, bold, reverse = base & 0x0F, (base >> 4) & 0x0F, False, False
                    elif code == 1:
                        bold = True
                    elif code == 7:
                        reverse = True
                    elif 30 <= code <= 37 or 90 <= code <= 97:
                        fg = _ANSI_TO_WIN[code % 10] | (8 if code >= 90 else 0)
                    elif 40 <= code <= 47 or 100 <= code <= 107:
                        bg = _ANSI_TO_WIN[code % 10] | (8 if code >= 100 else 0)
                    elif code == 39:
                        fg = base & 0x0F
                    elif code == 49:
                        bg = (base >> 4) & 0x0F
                continue
            f, b = (bg, fg) if reverse else (fg, bg)
            api.SetConsoleTextAttribute(self._out, (f | (8 if bold else 0)) | (b << 4))
            units = len(token.encode("utf-16-le")) // 2
            api.WriteConsoleW(self._out, token, units, ctypes.byref(written), None)


def rebind_std_streams() -> None:  # pragma: no cover - Windows only
    """Point sys.stdin/stdout/stderr at the console, for a plain-text phase.

    ``open("CONOUT$")`` gives io._WindowsConsoleIO, which writes through
    WriteConsoleW and takes UTF-8 from the text layer, so no SetConsoleOutputCP
    is needed. Only replaces streams that are None or StringIO (a windowed
    build); a source run keeps the console streams Python already gave it.
    """
    for name, device, mode in (
        ("stdin", "CONIN$", "r"),
        ("stdout", "CONOUT$", "w"),
        ("stderr", "CONOUT$", "w"),
    ):
        current = getattr(sys, name)
        if current is None or isinstance(current, io.StringIO):
            setattr(sys, name, open(device, mode, encoding="utf-8", errors="replace"))  # noqa: SIM115, PTH123
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)


def _ancestor_pids(levels: int = 3) -> list[int]:
    """The ids of the processes above this one, nearest first."""
    try:
        import psutil

        return [parent.pid for parent in psutil.Process().parents()[:levels]]
    except Exception:
        return []


def print_to_parent_console(text: str) -> bool:  # pragma: no cover - Windows only
    """Print *text* in the console Mercy AI was started from, if there is one.

    For ``-help`` and ``-version``, which print and exit at once: the shell
    gets its prompt back first (it does not wait for a windowed program), so
    nothing reads keys from the console at the same time and attaching is safe
    here, unlike for the text interface. Returns ``False`` when there is no
    console to print in, for example when started by a double-click.

    The built exe is two processes: PyInstaller's launcher, which has no
    console, starts this one. So the parent is tried first and then the
    processes above it, which is where the command prompt is.
    """
    if not IS_WINDOWS:
        return False
    api = _api()
    for pid in [ATTACH_PARENT_PROCESS, *_ancestor_pids()]:
        if not api.AttachConsole(pid):
            continue
        try:
            handle = api.CreateFileW(
                "CONOUT$",
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if not handle or handle == INVALID_HANDLE_VALUE:
                continue
            try:
                # The prompt is already on screen, so start on a fresh line.
                out = "\r\n" + text.rstrip().replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"
                units = len(out.encode("utf-16-le")) // 2
                if api.WriteConsoleW(handle, out, units, ctypes.byref(DWORD()), None):
                    return True
            finally:
                api.CloseHandle(handle)
        finally:
            api.FreeConsole()
    return False


@contextlib.contextmanager
def attach_console(full_screen: bool = True) -> Iterator[Any]:
    """A console for the text interface (*full_screen*) or for a diagnostic.

    On Windows this opens the console window; elsewhere it uses the terminal
    the program was started from. Everything is put back on the way out.
    """
    console: Any
    if IS_WINDOWS:
        console = WindowsConsole()
        console.open(full_screen=full_screen)
    elif full_screen:
        if not sys.stdin.isatty():
            raise ConsoleError("The text interface needs a terminal.")
        console = PosixConsole()
        console.open()
    else:
        console = _PlainTerminal()
    try:
        yield console
    finally:
        console.close()


class _PlainTerminal:
    """A diagnostic run from a Unix terminal prints to it as usual."""

    def close(self) -> None:
        pass

    def show_failure(self, text: str) -> None:
        print(text.rstrip(), file=sys.stderr)
