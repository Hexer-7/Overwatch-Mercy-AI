# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Reading and writing config.xml."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from mercy_ai.config import Config, config_to_xml, read_config, write_config_atomic
from mercy_ai.config_schema import COMMENTS, DEFAULTS, FIELD_ORDER, LIMITS

KEYS = {"heal_btn": "p", "damage_boost_btn": "o"}


def test_an_out_of_range_dial_is_clamped_once_on_read(tmp_path, caplog):
    path = tmp_path / "config.xml"
    config = replace(Config(resolution="1440", **KEYS), threshold_percent=20)
    write_config_atomic(config_to_xml(config), path)
    config = read_config(path)
    assert config.threshold_percent == 10.0
    caplog.clear()
    with caplog.at_level("WARNING", logger="mercy_ai"):
        for _ in range(100):
            _ = config.threshold
    assert not caplog.records


def test_a_failed_write_leaves_the_previous_config_intact(tmp_path, monkeypatch):
    path = tmp_path / "config.xml"
    good = Config(resolution="1440", **KEYS)
    write_config_atomic(config_to_xml(good), path)
    before = path.read_bytes()

    def refuse(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("mercy_ai.config.os.replace", refuse)
    with pytest.raises(OSError):
        write_config_atomic(config_to_xml(replace(good, theme="dark")), path)
    assert path.read_bytes() == before


def test_every_setting_is_in_every_table():
    tags = set(FIELD_ORDER) - {"config_version"}
    assert len(tags) == len(fields(Config))
    assert set(DEFAULTS) == tags - {"heal_btn", "damage_boost_btn", "resolution"}
    assert set(COMMENTS) == tags
    assert {tag for tag, _low, _high in LIMITS.values()} <= tags
