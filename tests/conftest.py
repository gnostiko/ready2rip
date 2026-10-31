# SPDX-License-Identifier: GPL-3.0-or-later
"""Pytest configuration for ready2rip."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--network',
        action='store_true',
        default=False,
        help='Run tests marked with @pytest.mark.network',
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line('markers', 'network: tests that hit the public internet')


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption('--network'):
        return
    skip_network = pytest.mark.skip(reason='needs --network')
    for item in items:
        if 'network' in item.keywords:
            item.add_marker(skip_network)
