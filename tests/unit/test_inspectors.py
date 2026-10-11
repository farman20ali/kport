"""
Inspector backend tests (tests/unit/test_inspectors.py).

Tests get_inspector() backend selection, PsutilInspector protocol
filtering (tcp/udp/both), FallbackInspector proto filtering,
and PortBinding proto defaults.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kport.inspectors.base import PortBinding
from kport.inspectors.psutil_impl import PsutilInspector
from kport.inspectors.system_impl import FallbackInspector
from tests.conftest import _binding


@pytest.mark.unit
class TestInspectorBackendSelection:
    """get_inspector() returns correct backend based on psutil availability."""

    def test_returns_fallback_inspector_when_psutil_unavailable(self):
        with patch("kport.inspectors._psutil_accessible", return_value=False):
            from kport.inspectors import get_inspector
            inspector = get_inspector()
        assert isinstance(inspector, FallbackInspector)

    def test_returns_psutil_inspector_when_psutil_available(self):
        with patch("kport.inspectors._psutil_accessible", return_value=True):
            from kport.inspectors import get_inspector
            inspector = get_inspector()
        assert isinstance(inspector, PsutilInspector)


@pytest.mark.unit
class TestPortBindingProtocol:
    """PortBinding.proto field defaults and valid values."""

    def test_proto_defaults_to_tcp(self):
        b = PortBinding(
            port=5353, family="inet", laddr="0.0.0.0:5353",
            pid=42, process_name="mdnsd", state="LISTEN",
        )
        assert b.proto == "tcp"

    def test_proto_can_be_set_to_udp(self):
        b = PortBinding(
            port=5353, family="inet", laddr="0.0.0.0:5353",
            pid=42, process_name="mdnsd", state="UDP", proto="udp",
        )
        assert b.proto == "udp"


@pytest.mark.unit
class TestPsutilInspectorProtoFiltering:
    """PsutilInspector.list_listening filters by tcp/udp/both."""

    def _tcp_conn(self):
        c = MagicMock()
        c.laddr = MagicMock(ip="0.0.0.0", port=8080)
        c.family = MagicMock(name="AF_INET")
        c.status = "LISTEN"
        c.pid = 111
        return c

    def _udp_conn(self):
        c = MagicMock()
        c.laddr = MagicMock(ip="0.0.0.0", port=5353)
        c.family = MagicMock(name="AF_INET")
        c.status = ""
        c.pid = 222
        return c

    def test_tcp_only_excludes_udp_ports(self):
        inspector = PsutilInspector()
        with patch("psutil.net_connections", return_value=[self._tcp_conn()]), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.name.return_value = "node"
            result = inspector.list_listening(proto="tcp")
        assert all(b.proto == "tcp" for b in result)
        assert all(b.port != 5353 for b in result)

    def test_udp_only_returns_udp_ports(self):
        inspector = PsutilInspector()
        with patch("psutil.net_connections", return_value=[self._udp_conn()]), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.name.return_value = "mdnsd"
            result = inspector.list_listening(proto="udp")
        assert all(b.proto == "udp" for b in result)
        assert any(b.port == 5353 for b in result)

    def test_both_returns_tcp_and_udp(self):
        tcp = self._tcp_conn()
        udp = self._udp_conn()

        def fake_net_connections(kind):
            return [tcp] if kind == "tcp" else [udp]

        inspector = PsutilInspector()
        with patch("psutil.net_connections", side_effect=fake_net_connections), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.name.return_value = "svc"
            result = inspector.list_listening(proto="both")
        protos = {b.proto for b in result}
        assert "tcp" in protos
        assert "udp" in protos

    def test_find_bindings_on_port_filters_by_proto(self):
        udp_conn = MagicMock()
        udp_conn.laddr = MagicMock(ip="0.0.0.0", port=5353)
        udp_conn.family = MagicMock(name="AF_INET")
        udp_conn.status = ""
        udp_conn.pid = 999
        inspector = PsutilInspector()
        with patch("psutil.net_connections", return_value=[udp_conn]), \
             patch("psutil.Process") as mock_proc:
            mock_proc.return_value.name.return_value = "mdnsd"
            bindings = inspector.find_bindings_on_port(5353, proto="udp")
        assert len(bindings) == 1
        assert bindings[0].proto == "udp"


@pytest.mark.unit
class TestFallbackInspectorProtoFiltering:
    """FallbackInspector.list_listening filters tcp/udp/both from native results."""

    def test_filters_tcp_from_mixed_results(self):
        inspector = FallbackInspector()
        inspector.system = "Linux"
        tcp_b = _binding(8080, proto="tcp")
        udp_b = _binding(5353, proto="udp")
        with patch(
            "kport.inspectors.system_impl._list_listening_linux_native",
            return_value=[tcp_b, udp_b],
        ):
            tcp_only = inspector.list_listening(proto="tcp")
            udp_only = inspector.list_listening(proto="udp")
            both = inspector.list_listening(proto="both")
        assert all(b.proto == "tcp" for b in tcp_only)
        assert all(b.proto == "udp" for b in udp_only)
        assert len(both) == 2


@pytest.mark.unit
class TestInspectorProcessNameMatching:
    """Tests for find_pids_by_name() matching correctness and self-exclusion."""

    def test_psutil_find_pids_by_name(self):
        inspector = PsutilInspector()

        p1 = MagicMock()
        p1.info = {"pid": 101, "name": "java"}
        p2 = MagicMock()
        p2.info = {"pid": 102, "name": "java-helper"}
        p3 = MagicMock()
        p3.info = {"pid": 103, "name": "sh"}
        p4 = MagicMock()
        p4.info = {"pid": 104, "name": "kport"}

        mock_iter = [p1, p2, p3, p4]

        # 1. Non-exact match (matches name, name-helper, excludes sh/kport)
        with patch("psutil.process_iter", return_value=mock_iter), \
             patch("os.getpid", return_value=9999):
            pids = inspector.find_pids_by_name("java", exact=False)
        assert pids == [101, 102]

        # 2. Exact match
        with patch("psutil.process_iter", return_value=mock_iter), \
             patch("os.getpid", return_value=9999):
            pids = inspector.find_pids_by_name("java", exact=True)
        assert pids == [101]

        # 3. Self-PID exclusion
        with patch("psutil.process_iter", return_value=mock_iter), \
             patch("os.getpid", return_value=101):
            pids = inspector.find_pids_by_name("java", exact=False)
        assert pids == [102]

    def test_fallback_pgrep_find_pids_by_name(self):
        inspector = FallbackInspector()
        inspector.system = "Linux"

        mock_proc = MagicMock()
        mock_proc.stdout = "101\n102\n"

        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch.object(inspector, "_run_subprocess", return_value=mock_proc) as mock_run, \
             patch("os.getpid", return_value=9999):
            pids = inspector.find_pids_by_name("java", exact=False)

        assert pids == [101, 102]
        mock_run.assert_called_once_with(["pgrep", "-i", "java"])

    def test_fallback_ps_ef_find_pids_by_name(self):
        inspector = FallbackInspector()
        inspector.system = "Linux"

        ps_output = (
            "UID        PID  PPID  C STIME TTY          TIME CMD\n"
            "root       101     1  0 12:00 ?        00:00:00 java -jar app.jar\n"
            "root       102   101  0 12:00 ?        00:00:00 java-helper\n"
            "root       103   100  0 12:00 ?        00:00:00 sh -c java\n"
            "root       104   100  0 12:00 ?        00:00:00 kport -kp java\n"
        )
        mock_proc = MagicMock()
        mock_proc.stdout = ps_output

        with patch("shutil.which", return_value=None), \
             patch.object(inspector, "_run_subprocess", return_value=mock_proc), \
             patch("os.getpid", return_value=9999):
            pids = inspector.find_pids_by_name("java", exact=False)

        assert pids == [101, 102]

