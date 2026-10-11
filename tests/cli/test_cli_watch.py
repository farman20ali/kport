"""
CLI watch mode tests (tests/cli/test_cli_watch.py).

Tests the `kport watch` command: --until free, --until occupied,
state transitions, --timeout, and interval behaviour.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kport.cli import handle_product_command
from tests.conftest import FakeInspector, _args, _binding


@pytest.mark.cli
def test_watch_until_free_satisfied_immediately_when_port_is_free():
    inspector = FakeInspector()
    args = _args(command="watch", port=8080, until="free", interval=0.01)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == 0


@pytest.mark.cli
def test_watch_until_occupied_satisfied_immediately_when_port_is_occupied():
    inspector = FakeInspector(
        bindings_on_port={8080: [_binding(8080, pid=10, name="node")]},
        pids_on_port={8080: [10]},
    )
    args = _args(command="watch", port=8080, until="occupied", interval=0.01)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == 0


@pytest.mark.cli
def test_watch_until_free_polls_until_port_clears():
    polls = [0]

    class TransitionInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            polls[0] += 1
            return [100] if polls[0] == 1 else []

        def find_bindings_on_port(self, port, proto="tcp"):
            return [_binding(port, pid=100)] if polls[0] == 1 else []

    inspector = TransitionInspector()
    args = _args(command="watch", port=8080, until="free", interval=0.01)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == 0
    assert polls[0] >= 2


@pytest.mark.cli
def test_watch_times_out_when_condition_never_met(capsys):
    inspector = FakeInspector(
        bindings_on_port={8080: [_binding(8080, pid=10, name="node")]},
        pids_on_port={8080: [10]},
    )
    args = _args(command="watch", port=8080, until="free", timeout=0.05, interval=0.01, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == 1
    lines = [l.strip() for l in capsys.readouterr().out.splitlines() if l.strip()]
    last_event = json.loads(lines[-1])
    assert last_event["event"] == "timeout"
    assert last_event["success"] is False
