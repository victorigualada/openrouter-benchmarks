"""Pytest plugin allowing live OpenRouter calls from inside the HA test harness.

Since Home Assistant 2026.5.0, `pytest_homeassistant_custom_component`'s
`pytest_runtest_setup` locks the network down twice over:

  1. `socket.getaddrinfo` / `gethostbyname` / `gethostbyname_ex` are patched to
     reject any host that is not an IP literal, raising
     `RuntimeError("DNS resolution disabled in tests")`.
  2. `pytest_socket.socket_allow_hosts(["127.0.0.1"])` plus `disable_socket()`
     reject the `connect()` even once a name has resolved.

The datasets repo's own `mock_allow_sockets` fixture only undoes (2). Both
failures reach us as the openai SDK's generic `APIConnectionError("Connection
error.")`, which `open_router.async_setup_entry` converts into
`ConfigEntryNotReady` - so the real cause never appears in the CI logs.

Enable with, from the datasets checkout:

    PYTHONPATH=<path to this dir> PYTEST_ADDOPTS="-p allow_real_dns" pytest ...

Two implementation constraints, both learned the hard way:

  * This must be a hook, not a fixture. The config entry is created inside the
    `conversation_agent_config_entry` fixture, so a fixture-based fix races
    against it and only wins some of the time.
  * The hook must NOT be marked `trylast`. Hook implementations run LIFO by
    registration, which places this plugin (loaded via `-p` at startup) after
    phcc (loaded from its `pytest11` entry point) and before
    `_pytest.runner.pytest_runtest_setup`, which is what actually runs the
    fixtures. `trylast` moves it after the runner, i.e. after the fixture has
    already failed.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
import pytest_socket
from pytest_homeassistant_custom_component import plugins as _phcc_plugins

_SAVED: tuple[Any, Any, Any] | None = None


@pytest.hookimpl
def pytest_runtest_setup() -> None:
    """Undo the harness network restrictions for the upcoming test."""
    global _SAVED

    _SAVED = (socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex)

    # Clears disable_socket() and the ["127.0.0.1"] allowlist.
    pytest_socket.pytest_runtest_teardown()

    real_getaddrinfo = _phcc_plugins._real_getaddrinfo
    socket.getaddrinfo = real_getaddrinfo
    socket.gethostbyname = lambda host, *args, **kwargs: real_getaddrinfo(
        host, None
    )[0][4][0]
    socket.gethostbyname_ex = lambda host, *args, **kwargs: (
        host,
        [],
        [real_getaddrinfo(host, None)[0][4][0]],
    )


@pytest.hookimpl
def pytest_runtest_teardown() -> None:
    """Restore the harness patches so non-benchmark tests stay sandboxed."""
    global _SAVED

    if _SAVED is not None:
        socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex = _SAVED
        _SAVED = None
