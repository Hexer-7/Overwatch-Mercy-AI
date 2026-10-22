# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Starting the program: arguments, streams and failures in a windowed build."""

from __future__ import annotations

import io
import types

import pytest

from mercy_ai.launch import (
    OPTIONS,
    UsageError,
    ensure_streams,
    help_text,
    is_diagnostic,
    main,
    parse_args,
    report_failure,
)


@pytest.mark.parametrize("flag", ["-cli", "--cli"])
def test_both_spellings_open_the_text_interface(flag):
    assert parse_args([flag]).cli is True


def test_no_arguments_opens_the_window():
    args = parse_args([])
    assert args.cli is False
    assert not is_diagnostic(args)


def test_a_diagnostic_is_recognised():
    assert is_diagnostic(parse_args(["--monitors"]))


def test_missing_streams_are_replaced_and_present_ones_kept():
    target = types.SimpleNamespace(stdin=None, stdout=io.StringIO(), stderr=None)
    kept = target.stdout
    assert ensure_streams(target) == ["stdin", "stderr"]
    assert target.stdout is kept
    assert isinstance(target.stdin, io.StringIO)


def test_a_failure_is_shown_without_colour_codes():
    shown = []
    message = report_failure("\x1b[31mNo templates for 1440.\x1b[0m\n", show=shown.append)
    assert shown == [message] == ["No templates for 1440."]


def test_an_empty_failure_still_says_something():
    shown = []
    report_failure("  \n", show=shown.append)
    assert "could not start" in shown[0]


@pytest.mark.parametrize(
    ("argv", "name", "value"),
    [
        (["-h"], "help", True),
        (["--h"], "help", True),
        (["-help"], "help", True),
        (["--help"], "help", True),
        (["-version"], "version", True),
        (["-monitors"], "monitors", True),
        (["-calibrate"], "calibrate", True),
        (["--capture-modes"], "capture_modes", True),
        (["-log-level", "debug"], "log_level", "DEBUG"),
    ],
)
def test_every_option_takes_one_dash_or_two(argv, name, value):
    assert getattr(parse_args(argv), name) == value


def test_the_help_lists_every_option_in_both_spellings():
    text = help_text()
    for _title, rows in OPTIONS:
        for one, two, what in rows:
            assert one in text and two in text
            assert what.split()[0] in text
    for line in text.splitlines():
        assert len(line) <= 80


def test_help_prints_in_the_terminal(capsys):
    assert main(["-help"]) == 0
    out = capsys.readouterr().out
    assert "-cli" in out and "--cli" in out and "-calibrate" in out


def test_an_unknown_option_says_how_to_get_help(capsys):
    with pytest.raises(UsageError):
        parse_args(["--nope"])
    assert main(["--nope"]) == 2
    assert "-help" in capsys.readouterr().out
