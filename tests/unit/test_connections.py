from unittest.mock import MagicMock, patch

from kport.cli import handle_connections
from kport.inspectors.base import ConnectionInfo
from kport.inspectors.psutil_impl import PsutilInspector
from kport.inspectors.system_impl import FallbackInspector, _parse_proc_net_connections


def test_connection_info_dataclass():
    """Verify ConnectionInfo dataclass initialization and field defaults."""
    conn = ConnectionInfo(
        pid=1234,
        process_name="test-proc",
        proto="tcp",
        local_address="127.0.0.1",
        local_port=8080,
        remote_address="*",
        remote_port=None,
        state="LISTEN",
    )
    assert conn.pid == 1234
    assert conn.process_name == "test-proc"
    assert conn.proto == "tcp"
    assert conn.local_address == "127.0.0.1"
    assert conn.local_port == 8080
    assert conn.remote_address == "*"
    assert conn.remote_port is None
    assert conn.state == "LISTEN"


def test_list_connections_psutil_and_fallback_live():
    """Smoke test: Query connections on the host machine using both backends."""
    # Verifies that both inspectors run and execute native calls without crashing.
    p_inspector = PsutilInspector()
    p_conns = p_inspector.list_connections()
    assert isinstance(p_conns, list)

    f_inspector = FallbackInspector()
    f_conns = f_inspector.list_connections()
    assert isinstance(f_conns, list)


def test_linux_proc_net_tcp_parsing(tmp_path):
    """Verify proc net tcp connection string parses states and address hex strings correctly."""
    # 127.0.0.1:8080 -> 127.0.0.1:80, state ESTABLISHED (01)
    # 0.0.0.0:80 -> 0.0.0.0:0, state LISTEN (0A)
    fake_content = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:1F90 0200007F:0050 01 00000000:00000000 00:00000000 00000000  1000        0 12345 1 abcdef\n"
        "   1: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12346 1 abcdef\n"
    )
    tcp_file = tmp_path / "tcp"
    tcp_file.write_text(fake_content)

    res = _parse_proc_net_connections(str(tcp_file), "IPv4")
    assert len(res) == 2

    # 127.0.0.1:8080 -> 127.0.0.2:80 (0200007F little-endian = 127.0.0.2), state ESTABLISHED
    assert res[0][0] == "127.0.0.1"
    assert res[0][1] == 8080
    assert res[0][2] == "127.0.0.2"
    assert res[0][3] == 80
    assert res[0][4] == "ESTABLISHED"
    assert res[0][5] == 12345

    # 0.0.0.0:80 LISTEN -> remote masked to *
    assert res[1][0] == "0.0.0.0"
    assert res[1][1] == 80
    assert res[1][2] == "*"
    assert res[1][3] == 0
    assert res[1][4] == "LISTEN"
    assert res[1][5] == 12346


def test_handle_connections_cli_filtering(capsys):
    """Verify handle_connections formats, prints, and filters connection details correctly."""
    mock_conns = [
        ConnectionInfo(100, "java", "tcp", "127.0.0.1", 8080, "127.0.0.1", 5432, "ESTABLISHED"),
        ConnectionInfo(200, "python", "tcp", "127.0.0.1", 5000, "*", None, "LISTEN"),
        ConnectionInfo(100, "java", "tcp", "127.0.0.1", 8081, "*", None, "LISTEN"),
    ]
    inspector = MagicMock()
    inspector.list_connections.return_value = mock_conns

    # 1. No filters
    args = MagicMock(pid=None, process=None, port=None, state=None, json=False)
    status = handle_connections(args, inspector)
    assert status == 0
    out, _ = capsys.readouterr()
    assert "java" in out
    assert "python" in out
    assert "3 connection(s) found." in out

    # 2. Filter by PID
    args = MagicMock(pid=200, process=None, port=None, state=None, json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    assert "python" in out
    assert "java" not in out
    assert "1 connection(s) found." in out

    # 3. Filter by process name (case-insensitive substring)
    args = MagicMock(pid=None, process="PyTh", port=None, state=None, json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    assert "python" in out
    assert "java" not in out

    # 4. Filter by port (matches local or remote)
    args = MagicMock(pid=None, process=None, port=5432, state=None, json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    assert "5432" in out
    assert "8080" in out
    assert "8081" not in out

    # 5. Filter by state
    args = MagicMock(pid=None, process=None, port=None, state="established", json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    assert "ESTABLISHED" in out
    assert "LISTEN" not in out

    # 6. JSON output verification
    args = MagicMock(pid=None, process=None, port=None, state=None, json=True)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    assert '"schema_version": 1' in out
    assert '"command": "connections"' in out
    assert '"count": 3' in out


def test_long_process_name_does_not_merge_with_local_column(capsys):
    """Regression: process names longer than the PROCESS column width must be
    truncated with an ellipsis so the LOCAL column is always visually separate."""
    long_name = "language_server_windows_x64.exe"  # 32 chars, exceeds 24-wide column
    conn = ConnectionInfo(
        pid=7032, process_name=long_name, proto="tcp",
        local_address="127.0.0.1", local_port=56362,
        remote_address="127.0.0.1", remote_port=51876, state="ESTABLISHED",
    )
    inspector = MagicMock()
    inspector.list_connections.return_value = [conn]

    args = MagicMock(pid=None, process=None, port=None, state=None, json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()

    # The LOCAL address must appear on the same data line, not merged with name
    data_line = [l for l in out.splitlines() if "127.0.0.1:56362" in l]
    assert data_line, "LOCAL address not found in output"
    # The full raw name must NOT appear; the column must be truncated
    assert long_name not in data_line[0], (
        f"Long process name was not truncated; columns ran together: {data_line[0]!r}"
    )
    # The local address must not be immediately adjacent to end of the (truncated) name
    # i.e. there must be whitespace before '127.0.0.1:56362'
    idx = data_line[0].find("127.0.0.1:56362")
    assert idx > 0 and data_line[0][idx - 1] == " ", (
        f"No space before LOCAL address — columns merged: {data_line[0]!r}"
    )


def test_pid_zero_and_time_wait_round_trip(capsys):
    """PID 0 / TIME_WAIT must be preserved in both text and JSON output,
    and must be selectable by --state TIME_WAIT without crashing."""
    import json as json_mod

    tw = ConnectionInfo(
        pid=0, process_name=None, proto="tcp",
        local_address="192.168.1.5", local_port=51883,
        remote_address="172.64.144.52", remote_port=443, state="TIME_WAIT",
    )
    other = ConnectionInfo(
        pid=1234, process_name="python.exe", proto="tcp",
        local_address="0.0.0.0", local_port=8000,
        remote_address="*", remote_port=None, state="LISTEN",
    )
    inspector = MagicMock()
    inspector.list_connections.return_value = [tw, other]

    # Text output: PID 0 shown as "0", no process name shown as "-", state preserved
    args = MagicMock(pid=None, process=None, port=None, state=None, json=False)
    handle_connections(args, inspector)
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if "51883" in l]
    assert lines, "TIME_WAIT connection not in output"
    assert "TIME_WAIT" in lines[0]
    # PID 0 must appear as "0", not suppressed or replaced
    assert lines[0].startswith("0 ") or lines[0].startswith("0\t")

    # JSON output: pid=0 preserved as integer 0, state preserved
    args_j = MagicMock(pid=None, process=None, port=None, state=None, json=True)
    handle_connections(args_j, inspector)
    out_j, _ = capsys.readouterr()
    parsed = json_mod.loads(out_j)
    tw_entry = next(c for c in parsed["data"]["connections"] if c["local_port"] == 51883)
    assert tw_entry["pid"] == 0, f"PID 0 not preserved in JSON: {tw_entry['pid']!r}"
    assert tw_entry["state"] == "TIME_WAIT"
    assert tw_entry["process_name"] is None

    # --state TIME_WAIT must select ONLY the TIME_WAIT row
    args_f = MagicMock(pid=None, process=None, port=None, state="TIME_WAIT", json=False)
    handle_connections(args_f, inspector)
    out_f, _ = capsys.readouterr()
    assert "TIME_WAIT" in out_f
    assert "python.exe" not in out_f
    assert "1 connection(s) found." in out_f

    # --state ESTABLISHED must NOT include TIME_WAIT
    args_e = MagicMock(pid=None, process=None, port=None, state="ESTABLISHED", json=False)
    handle_connections(args_e, inspector)
    out_e, _ = capsys.readouterr()
    assert "TIME_WAIT" not in out_e


def test_disappearing_process_does_not_crash():
    """Process lookup must not crash when a process exits between connection
    enumeration and name resolution (normal race condition)."""
    import psutil

    # A PID that is almost certainly not a real process
    ghost_pid = 999999

    # Simulate the psutil inspector encountering a NoSuchProcess mid-lookup
    with patch("psutil.net_connections") as mock_conns, \
         patch("psutil.Process") as mock_proc:
        mock_laddr = MagicMock(); mock_laddr.ip = "127.0.0.1"; mock_laddr.port = 9999
        raw = MagicMock(); raw.pid = ghost_pid; raw.laddr = mock_laddr
        raw.raddr = None; raw.status = "LISTEN"
        mock_conns.return_value = [raw]
        mock_proc.side_effect = psutil.NoSuchProcess(ghost_pid)

        from kport.inspectors.psutil_impl import PsutilInspector
        inspector = PsutilInspector()
        conns = inspector.list_connections()  # must not raise

    assert isinstance(conns, list)
    assert len(conns) == 1
    assert conns[0].pid == ghost_pid
    assert conns[0].process_name is None  # resolved to None gracefully
