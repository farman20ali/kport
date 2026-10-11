"""
Tests for main() exit codes, graceful interrupt, JSON error envelopes,
and verifying that no internal code or tracebacks leak into stdout/stderr.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kport.cli import (
    EXIT_INTERRUPTED,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_PORT_FREE,
    main,
)
from tests.conftest import FakeInspector


@pytest.mark.cli
def test_main_keyboard_interrupt_text_mode(capsys):
    """Ctrl+C in text mode prints a friendly message to stderr and exits with 130 without stacktrace."""
    with patch("kport.cli.get_inspector", side_effect=KeyboardInterrupt):
        rc = main(["inspect", "8080"])
    assert rc == EXIT_INTERRUPTED
    captured = capsys.readouterr()
    assert "Interrupted" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


@pytest.mark.cli
def test_main_keyboard_interrupt_json_mode(capsys):
    """Ctrl+C in JSON mode outputs a versioned JSON error envelope with code 'interrupted'."""
    with patch("kport.cli.get_inspector", side_effect=KeyboardInterrupt):
        rc = main(["inspect", "8080", "--json"])
    assert rc == EXIT_INTERRUPTED
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["schema_version"] == 1
    assert envelope["error"]["code"] == "interrupted"
    assert "Traceback" not in captured.err


@pytest.mark.cli
def test_main_unexpected_exception_hides_traceback_by_default(capsys):
    """An unexpected exception hides the traceback unless --debug is set."""
    with patch("kport.cli.get_inspector", side_effect=RuntimeError("internal boom")):
        rc = main(["inspect", "8080"])
    assert rc != EXIT_OK
    captured = capsys.readouterr()
    assert "Unexpected error: internal boom" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.cli
def test_main_unexpected_exception_json_envelope(capsys):
    """An unexpected exception in JSON mode outputs clean JSON without traceback."""
    with patch("kport.cli.get_inspector", side_effect=RuntimeError("internal boom")):
        rc = main(["inspect", "8080", "--json"])
    assert rc != EXIT_OK
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["schema_version"] == 1
    assert envelope["error"]["code"] == "unexpected_error"
    assert "Traceback" not in captured.err


@pytest.mark.cli
def test_main_invalid_port_json_envelope(capsys):
    """Invalid port in JSON mode outputs schema envelope with exit code 2."""
    with patch("kport.cli.get_inspector", return_value=FakeInspector()):
        rc = main(["inspect", "99999", "--json"])
    assert rc == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["schema_version"] == 1
    assert envelope["error"]["code"] == "invalid_port"


@pytest.mark.cli
def test_main_free_port_exit_code(capsys):
    """Inspecting a free port exits with EXIT_PORT_FREE (5)."""
    with patch("kport.cli.get_inspector", return_value=FakeInspector(listening=[])), \
         patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = main(["inspect", "65535", "--json"])
    assert rc == EXIT_PORT_FREE
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == 1
    assert envelope["data"]["type"] == "free"
