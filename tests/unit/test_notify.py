"""
Unit tests for desktop notification dispatch (tests/unit/test_notify.py).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kport.notify import notify as _desktop_notify


@pytest.mark.unit
def test_notify_dispatches_to_backend():
    with patch("kport.notify._dispatch") as mock_dispatch:
        _desktop_notify("Title", "Message")
    mock_dispatch.assert_called_once_with("Title", "Message")
