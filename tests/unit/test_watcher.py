"""
test_watcher.py — Unit tests for kport.watcher (Phase 4 event-driven port watcher).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from kport.watcher import (
    PORT_EVENT_ADDED,
    PORT_EVENT_REMOVED,
    PortEvent,
    PortWatcher,
    _diff,
    _make_key,
    _snapshot,
    fast_list_listening_linux,
    fast_list_listening_windows,
    watch_ports,
    watcher_platform_info,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binding(port=8080, proto="tcp", pid=1234, laddr="0.0.0.0:8080", family="IPv4", state="LISTEN"):
    b = MagicMock()
    b.port = port
    b.proto = proto
    b.pid = pid
    b.laddr = laddr
    b.family = family
    b.state = state
    b.process_name = "pytest"
    return b


# ---------------------------------------------------------------------------
# _make_key
# ---------------------------------------------------------------------------


def test_make_key_returns_tuple():
    b = _make_binding(port=9000, proto="tcp", pid=99, laddr="127.0.0.1:9000")
    key = _make_key(b)
    assert key == (9000, "tcp", 99, "127.0.0.1:9000")


# ---------------------------------------------------------------------------
# _snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_dict_keyed_by_binding():
    b1 = _make_binding(port=3000)
    b2 = _make_binding(port=3001)
    inspector = MagicMock()
    inspector.list_listening.return_value = [b1, b2]

    snap = _snapshot(inspector, proto="tcp")
    assert len(snap) == 2
    key1 = _make_key(b1)
    assert key1 in snap


def test_snapshot_returns_empty_on_inspector_error():
    inspector = MagicMock()
    inspector.list_listening.side_effect = OSError("permission denied")

    snap = _snapshot(inspector, proto="tcp")
    assert snap == {}


# ---------------------------------------------------------------------------
# _diff
# ---------------------------------------------------------------------------


def test_diff_detects_added_port():
    b = _make_binding(port=4000)
    old = {}
    new = {_make_key(b): b}
    events = _diff(old, new)
    assert len(events) == 1
    assert events[0].event == PORT_EVENT_ADDED
    assert events[0].port == 4000


def test_diff_detects_removed_port():
    b = _make_binding(port=4001)
    old = {_make_key(b): b}
    new = {}
    events = _diff(old, new)
    assert len(events) == 1
    assert events[0].event == PORT_EVENT_REMOVED
    assert events[0].port == 4001


def test_diff_no_change_returns_empty():
    b = _make_binding(port=5000)
    snap = {_make_key(b): b}
    events = _diff(snap, snap)
    assert events == []


def test_diff_mixed_events():
    b_old = _make_binding(port=6000)
    b_new = _make_binding(port=6001)
    old = {_make_key(b_old): b_old}
    new = {_make_key(b_new): b_new}
    events = _diff(old, new)
    assert len(events) == 2
    # removed events come first in sorted order
    removed = [e for e in events if e.event == PORT_EVENT_REMOVED]
    added = [e for e in events if e.event == PORT_EVENT_ADDED]
    assert len(removed) == 1
    assert len(added) == 1


def test_diff_event_has_timestamp():
    b = _make_binding(port=7000)
    events = _diff({}, {_make_key(b): b})
    assert events[0].timestamp > 0


# ---------------------------------------------------------------------------
# PortEvent.to_dict
# ---------------------------------------------------------------------------


def test_port_event_to_dict():
    ev = PortEvent(
        event=PORT_EVENT_ADDED,
        port=8888,
        proto="tcp",
        pid=42,
        process_name="myapp",
        laddr="0.0.0.0:8888",
        family="IPv4",
        state="LISTEN",
    )
    d = ev.to_dict()
    assert d["event"] == PORT_EVENT_ADDED
    assert d["port"] == 8888
    assert d["process_name"] == "myapp"
    assert "timestamp" in d


# ---------------------------------------------------------------------------
# PortWatcher — synchronous poll()
# ---------------------------------------------------------------------------


def test_port_watcher_poll_detects_change():
    b1 = _make_binding(port=9090)
    inspector = MagicMock()
    # First call: empty; second call: one binding
    inspector.list_listening.side_effect = [[], [b1]]

    watcher = PortWatcher(inspector, interval=10.0)
    # Seed initial snapshot
    watcher.poll()
    # Now the binding appears
    events2 = watcher.poll()
    assert events2[0].event == PORT_EVENT_ADDED
    assert events2[0].port == 9090


def test_port_watcher_poll_detects_removal():
    b1 = _make_binding(port=9091)
    inspector = MagicMock()
    inspector.list_listening.side_effect = [[b1], []]

    watcher = PortWatcher(inspector, interval=10.0)
    watcher.poll()  # seed
    events = watcher.poll()
    assert events[0].event == PORT_EVENT_REMOVED


# ---------------------------------------------------------------------------
# PortWatcher — background thread
# ---------------------------------------------------------------------------


def test_port_watcher_start_stop():
    inspector = MagicMock()
    inspector.list_listening.return_value = []

    watcher = PortWatcher(inspector, interval=0.1)
    watcher.start()
    assert watcher.is_alive()
    watcher.stop()
    assert not watcher.is_alive()


def test_port_watcher_on_event_callback():
    b = _make_binding(port=9500)
    call_log = []

    inspector = MagicMock()
    # Two calls: first empty (initial snapshot in start()), then one binding
    inspector.list_listening.side_effect = [[], [b], [b]]

    def cb(events):
        call_log.extend(events)

    watcher = PortWatcher(inspector, interval=0.05, on_event=cb)
    watcher.start()
    time.sleep(0.3)
    watcher.stop()

    assert any(e.port == 9500 and e.event == PORT_EVENT_ADDED for e in call_log)


def test_port_watcher_drain_events():
    b = _make_binding(port=9600)
    inspector = MagicMock()
    inspector.list_listening.side_effect = [[], [b], [b]]

    watcher = PortWatcher(inspector, interval=0.05)
    watcher.start()
    time.sleep(0.3)
    watcher.stop()

    batches = watcher.drain_events()
    assert isinstance(batches, list)
    # Subsequent drain is empty
    assert watcher.drain_events() == []


# ---------------------------------------------------------------------------
# watch_ports generator
# ---------------------------------------------------------------------------


def test_watch_ports_yields_events():
    b = _make_binding(port=9700)
    inspector = MagicMock()
    # Alternating: empty, then binding, then empty again
    inspector.list_listening.side_effect = [[], [b], []]

    gen = watch_ports(inspector, interval=0.05, proto="tcp", timeout=0.5)
    all_events = []
    for batch in gen:
        all_events.extend(batch)

    added = [e for e in all_events if e.event == PORT_EVENT_ADDED]
    assert len(added) >= 1


def test_watch_ports_timeout_stops_iteration():
    inspector = MagicMock()
    inspector.list_listening.return_value = []

    # With a tiny timeout the generator should stop quickly
    count = 0
    for _ in watch_ports(inspector, interval=0.05, timeout=0.15):
        count += 1
    # Could be 0 (no changes), but definitely stops
    assert count >= 0  # just ensures no infinite loop


# ---------------------------------------------------------------------------
# watcher_platform_info
# ---------------------------------------------------------------------------


def test_watcher_platform_info_returns_dict():
    info = watcher_platform_info()
    assert "platform" in info
    assert "interval_strategy" in info
    assert isinstance(info["win32_acceleration"], bool)
    assert isinstance(info["proc_net_available"], bool)


# ---------------------------------------------------------------------------
# fast_list_listening_linux (only meaningful on Linux, but importable anywhere)
# ---------------------------------------------------------------------------


def test_fast_list_listening_linux_returns_list():
    result = fast_list_listening_linux()
    assert isinstance(result, list)
    # On non-Linux, /proc/net/tcp won't exist → returns []


# ---------------------------------------------------------------------------
# fast_list_listening_windows (only meaningful on Windows, graceful elsewhere)
# ---------------------------------------------------------------------------


def test_fast_list_listening_windows_returns_list():
    result = fast_list_listening_windows()
    assert isinstance(result, list)
