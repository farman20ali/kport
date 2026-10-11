"""
OS system commands fallback inspector implementation for kport.
Leverages platform binaries (lsof, ss, netstat, tasklist, powershell) to inspect states.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import time
from typing import Any

from .base import BaseInspector, ConnectionInfo, PortBinding, ProcessInfo

# --- Linux-native /proc parsing helpers ---


def parse_ipv4(hex_str: str) -> str:
    """Parse little-endian hex IPv4 string from /proc/net/tcp."""
    b = bytes.fromhex(hex_str)
    return socket.inet_ntop(socket.AF_INET, b[::-1])


def parse_ipv6(hex_str: str) -> str:
    """Parse hex IPv6 representation from /proc/net/tcp6."""
    b = bytes.fromhex(hex_str)
    unpacked = struct.unpack("<IIII", b)
    packed = struct.pack(">IIII", *unpacked)
    return socket.inet_ntop(socket.AF_INET6, packed)


def _parse_proc_net_file(filename: str, family: str) -> list[tuple[str, int, int]]:
    """Parse socket information from /proc/net/tcp* or /proc/net/udp*."""
    sockets = []
    if not os.path.exists(filename):
        return sockets
    try:
        with open(filename, "r") as f:
            lines = f.readlines()
        if len(lines) <= 1:
            return sockets
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            local_addr = parts[1]
            state = parts[3]

            if "tcp" in filename and state != "0A":
                continue

            inode = int(parts[9])
            if inode == 0:
                continue

            try:
                ip_hex, port_hex = local_addr.split(":")
                port = int(port_hex, 16)
                if family == "IPv4":
                    ip = parse_ipv4(ip_hex)
                else:
                    ip = parse_ipv6(ip_hex)
                sockets.append((ip, port, inode))
            except (ValueError, IndexError):
                continue
    except OSError:
        pass
    return sockets


def _parse_proc_net_connections(filename: str, family: str) -> list[tuple[str, int, str, int, str, int]]:
    """Parse socket information (local, remote, state, inode) from /proc/net/tcp*."""
    sockets = []
    if not os.path.exists(filename):
        return sockets
    try:
        with open(filename, "r") as f:
            lines = f.readlines()
        if len(lines) <= 1:
            return sockets
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            local_addr = parts[1]
            remote_addr = parts[2]
            state_hex = parts[3]
            inode = int(parts[9])

            state_map = {
                "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
                "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
                "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK",
                "0A": "LISTEN", "0B": "CLOSING"
            }
            state = state_map.get(state_hex, f"STATE_{state_hex}")

            try:
                l_ip_hex, l_port_hex = local_addr.split(":")
                l_port = int(l_port_hex, 16)
                r_ip_hex, r_port_hex = remote_addr.split(":")
                r_port = int(r_port_hex, 16)

                if family == "IPv4":
                    l_ip = parse_ipv4(l_ip_hex)
                    r_ip = parse_ipv4(r_ip_hex)
                else:
                    l_ip = parse_ipv6(l_ip_hex)
                    r_ip = parse_ipv6(r_ip_hex)

                if state == "LISTEN" or r_ip in ("0.0.0.0", "::"):
                    r_ip = "*"
                    r_port = 0

                sockets.append((l_ip, l_port, r_ip, r_port, state, inode))
            except (ValueError, IndexError):
                continue
    except OSError:
        pass
    return sockets


def _get_linux_inode_to_pid_map() -> dict[int, int]:
    """Map socket inodes to owning process PIDs on Linux."""
    inode_to_pid = {}
    try:
        for pid_str in os.listdir("/proc"):
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        link = os.readlink(f"{fd_dir}/{fd}")
                        if link.startswith("socket:["):
                            inode = int(link[8:-1])
                            inode_to_pid[inode] = pid
                    except (OSError, ValueError):
                        continue
            except OSError:
                continue
    except OSError:
        pass
    return inode_to_pid


_BOOT_TIME: float | None = None


def _get_boot_time() -> float:
    global _BOOT_TIME
    if _BOOT_TIME is not None:
        return _BOOT_TIME
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime "):
                    _BOOT_TIME = float(line.split()[1])
                    return _BOOT_TIME
    except Exception:
        pass
    _BOOT_TIME = 0.0
    return _BOOT_TIME


def _get_linux_process_info(pid: int) -> ProcessInfo | None:
    """Retrieve process information natively on Linux from /proc."""
    try:
        stat_info = os.stat(f"/proc/{pid}")
        uid = stat_info.st_uid
        try:
            import pwd

            user = pwd.getpwuid(uid).pw_name
        except (KeyError, ImportError):
            user = str(uid)

        try:
            with open(f"/proc/{pid}/comm", "r", errors="ignore") as f:
                name = f.read().strip()
        except OSError:
            name = ""

        cmdline = None
        try:
            with open(f"/proc/{pid}/cmdline", "r", errors="ignore") as f:
                content = f.read()
                if content:
                    cmdline = [arg for arg in content.split("\x00") if arg]
        except OSError:
            pass

        if not name and cmdline:
            name = os.path.basename(cmdline[0])

        ppid = None
        try:
            with open(f"/proc/{pid}/status", "r", errors="ignore") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
        except (OSError, ValueError, IndexError):
            pass

        cwd = None
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            pass

        start_time = None
        try:
            with open(f"/proc/{pid}/stat", "r", errors="ignore") as f:
                stat_content = f.read().strip()
            rpar_idx = stat_content.rfind(")")
            if rpar_idx != -1:
                fields = stat_content[rpar_idx + 2:].split()
                if len(fields) >= 20:
                    ticks = int(fields[19])
                    try:
                        sc_clk_tck = os.sysconf("SC_CLK_TCK")
                    except (AttributeError, ValueError):
                        sc_clk_tck = 100
                    btime = _get_boot_time()
                    if btime > 0:
                        start_time = btime + (ticks / sc_clk_tck)
        except Exception:
            pass

        if start_time is None:
            try:
                start_time = float(stat_info.st_mtime)
            except Exception:
                pass

        return ProcessInfo(
            pid=pid,
            name=name,
            cmdline=cmdline,
            user=user,
            ppid=ppid,
            cwd=cwd,
            start_time=start_time,
        )
    except OSError:
        return None


def _list_listening_linux_native() -> list[PortBinding] | None:
    """Get active listening sockets natively on Linux via /proc/net."""
    if platform.system() != "Linux":
        return None

    inode_to_pid = _get_linux_inode_to_pid_map()

    # C3 fix: build a pid→info map once instead of calling _get_linux_process_info
    # once per socket (which would be 4× redundant for each PID binding on TCP4/6 + UDP4/6).
    unique_pids = {pid for pid in inode_to_pid.values() if pid}
    pid_to_info: dict[int, ProcessInfo | None] = {
        pid: _get_linux_process_info(pid) for pid in unique_pids
    }

    def _make_binding(
        ip: str, port: int, inode: int, family: str, state: str, proto: str = "tcp"
    ) -> PortBinding:
        pid = inode_to_pid.get(inode)
        info = pid_to_info.get(pid) if pid else None
        pname = info.name if info else None
        laddr = f"[{ip}]:{port}" if family == "IPv6" else f"{ip}:{port}"
        return PortBinding(
            port=port,
            family=family,
            laddr=laddr,
            pid=pid,
            process_name=pname,
            state=state,
            proto=proto,
        )

    bindings = []
    for ip, port, inode in _parse_proc_net_file("/proc/net/tcp", "IPv4"):
        bindings.append(_make_binding(ip, port, inode, "IPv4", "LISTEN", "tcp"))
    for ip, port, inode in _parse_proc_net_file("/proc/net/tcp6", "IPv6"):
        bindings.append(_make_binding(ip, port, inode, "IPv6", "LISTEN", "tcp"))
    # UDP has no state — entries are active bindings, not connections.
    for ip, port, inode in _parse_proc_net_file("/proc/net/udp", "IPv4"):
        bindings.append(_make_binding(ip, port, inode, "IPv4", "UDP", "udp"))
    for ip, port, inode in _parse_proc_net_file("/proc/net/udp6", "IPv6"):
        bindings.append(_make_binding(ip, port, inode, "IPv6", "UDP", "udp"))

    return bindings


def _list_listening_proc_pid_net(pid: int) -> list[PortBinding]:
    """
    Read port bindings from /proc/<pid>/net/tcp and /proc/<pid>/net/tcp6.

    This is the per-process network namespace view. Snap-packaged and Docker
    processes live in their own netns — their sockets don't appear in the
    host /proc/net/tcp inode map, but are visible here without root in most
    snap/container configurations because the file is readable by the pid's
    owner or root.
    """
    if platform.system() != "Linux":
        return []

    bindings: list[PortBinding] = []
    for filename, family in [
        (f"/proc/{pid}/net/tcp", "IPv4"),
        (f"/proc/{pid}/net/tcp6", "IPv6"),
    ]:
        try:
            for ip, port, _inode in _parse_proc_net_file(filename, family):
                laddr = f"[{ip}]:{port}" if family == "IPv6" else f"{ip}:{port}"
                bindings.append(
                    PortBinding(
                        port=port,
                        family=family,
                        laddr=laddr,
                        pid=pid,
                        process_name=None,
                        state="LISTEN",
                    )
                )
        except (OSError, ValueError, IndexError):
            pass
    return bindings


# --- Windows-native ctypes parsing helpers ---

# Only define Windows API structs and ctypes setup if on Windows
if platform.system() == "Windows":
    import ctypes
    from ctypes import wintypes

    AF_INET = 2
    AF_INET6 = 23
    TCP_TABLE_OWNER_PID_ALL = 5
    UDP_TABLE_OWNER_PID = 1

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_byte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_byte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_UDPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_UDP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_byte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]


def _get_extended_tcp_table_ipv4() -> list[PortBinding]:
    bindings = []
    if platform.system() != "Windows":
        return bindings

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_TCPROW_OWNER_PID),
        )
        row = entry_ptr.contents

        state_map = {
            1: "CLOSED",
            2: "LISTEN",
            3: "SYN_SENT",
            4: "SYN_RECV",
            5: "ESTABLISHED",
            6: "FIN_WAIT1",
            7: "FIN_WAIT2",
            8: "CLOSE_WAIT",
            9: "CLOSING",
            10: "LAST_ACK",
            11: "TIME_WAIT",
            12: "DELETE_TCB",
        }
        state = state_map.get(row.dwState, f"STATE_{row.dwState}")

        if state != "LISTEN":
            continue

        ip_bytes = struct.pack("<I", row.dwLocalAddr)
        ip = socket.inet_ntop(socket.AF_INET, ip_bytes)
        port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        pid = int(row.dwOwningPid)
        bindings.append(
            PortBinding(
                port=port,
                family="IPv4",
                laddr=f"{ip}:{port}",
                pid=pid,
                process_name=None,
                state=state,
            )
        )

    return bindings


def _get_extended_tcp_table_ipv6() -> list[PortBinding]:
    bindings = []
    if platform.system() != "Windows":
        return bindings

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_TCP6ROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_TCP6ROW_OWNER_PID),
        )
        row = entry_ptr.contents

        state_map = {
            1: "CLOSED",
            2: "LISTEN",
            3: "SYN_SENT",
            4: "SYN_RECV",
            5: "ESTABLISHED",
            6: "FIN_WAIT1",
            7: "FIN_WAIT2",
            8: "CLOSE_WAIT",
            9: "CLOSING",
            10: "LAST_ACK",
            11: "TIME_WAIT",
            12: "DELETE_TCB",
        }
        state = state_map.get(row.dwState, f"STATE_{row.dwState}")

        if state != "LISTEN":
            continue

        ip_bytes = bytes(row.ucLocalAddr)
        ip = socket.inet_ntop(socket.AF_INET6, ip_bytes)
        port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        pid = int(row.dwOwningPid)
        bindings.append(
            PortBinding(
                port=port,
                family="IPv6",
                laddr=f"[{ip}]:{port}",
                pid=pid,
                process_name=None,
                state=state,
            )
        )

    return bindings


def _get_extended_udp_table_ipv4() -> list[PortBinding]:
    bindings = []
    if platform.system() != "Windows":
        return bindings

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedUdpTable(
        None, ctypes.byref(size), True, AF_INET, UDP_TABLE_OWNER_PID, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedUdpTable(
        buf, ctypes.byref(size), True, AF_INET, UDP_TABLE_OWNER_PID, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_UDPROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_UDPROW_OWNER_PID),
        )
        row = entry_ptr.contents

        ip_bytes = struct.pack("<I", row.dwLocalAddr)
        ip = socket.inet_ntop(socket.AF_INET, ip_bytes)
        port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        pid = int(row.dwOwningPid)
        bindings.append(
            PortBinding(
                port=port,
                family="IPv4",
                laddr=f"{ip}:{port}",
                pid=pid,
                process_name=None,
                state="UDP",
            )
        )

    return bindings


def _get_extended_udp_table_ipv6() -> list[PortBinding]:
    bindings = []
    if platform.system() != "Windows":
        return bindings

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedUdpTable(
        None, ctypes.byref(size), True, AF_INET6, UDP_TABLE_OWNER_PID, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedUdpTable(
        buf, ctypes.byref(size), True, AF_INET6, UDP_TABLE_OWNER_PID, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_UDP6ROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_UDP6ROW_OWNER_PID),
        )
        row = entry_ptr.contents

        ip_bytes = bytes(row.ucLocalAddr)
        ip = socket.inet_ntop(socket.AF_INET6, ip_bytes)
        port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        pid = int(row.dwOwningPid)
        bindings.append(
            PortBinding(
                port=port,
                family="IPv6",
                laddr=f"[{ip}]:{port}",
                pid=pid,
                process_name=None,
                state="UDP",
            )
        )

    return bindings


def _get_extended_tcp_connections_ipv4() -> list[ConnectionInfo]:
    connections = []
    if platform.system() != "Windows":
        return connections

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    state_map = {
        1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RECV",
        5: "ESTABLISHED", 6: "FIN_WAIT1", 7: "FIN_WAIT2",
        8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK",
        11: "TIME_WAIT", 12: "DELETE_TCB",
    }

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_TCPROW_OWNER_PID),
        )
        row = entry_ptr.contents
        state = state_map.get(row.dwState, f"STATE_{row.dwState}")

        l_ip_bytes = struct.pack("<I", row.dwLocalAddr)
        l_ip = socket.inet_ntop(socket.AF_INET, l_ip_bytes)
        l_port = socket.ntohs(row.dwLocalPort & 0xFFFF)

        r_ip_bytes = struct.pack("<I", row.dwRemoteAddr)
        r_ip = socket.inet_ntop(socket.AF_INET, r_ip_bytes)
        r_port = socket.ntohs(row.dwRemotePort & 0xFFFF)

        if state == "LISTEN" or r_ip == "0.0.0.0":
            r_ip = "*"
            r_port = None

        pid = int(row.dwOwningPid)
        connections.append(
            ConnectionInfo(
                pid=pid,
                process_name=None,
                proto="tcp",
                local_address=l_ip,
                local_port=l_port,
                remote_address=r_ip,
                remote_port=r_port,
                state=state,
            )
        )
    return connections


def _get_extended_tcp_connections_ipv6() -> list[ConnectionInfo]:
    connections = []
    if platform.system() != "Windows":
        return connections

    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    res = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0
    )

    buf = ctypes.create_string_buffer(size.value)
    res = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET6, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if res != 0:
        return []

    num_entries = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    entry_size = ctypes.sizeof(MIB_TCP6ROW_OWNER_PID)
    offset = ctypes.sizeof(wintypes.DWORD)

    state_map = {
        1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RECV",
        5: "ESTABLISHED", 6: "FIN_WAIT1", 7: "FIN_WAIT2",
        8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK",
        11: "TIME_WAIT", 12: "DELETE_TCB",
    }

    for i in range(num_entries):
        entry_ptr = ctypes.cast(
            ctypes.byref(buf, offset + i * entry_size),
            ctypes.POINTER(MIB_TCP6ROW_OWNER_PID),
        )
        row = entry_ptr.contents
        state = state_map.get(row.dwState, f"STATE_{row.dwState}")

        l_ip_bytes = bytes(row.ucLocalAddr)
        l_ip = socket.inet_ntop(socket.AF_INET6, l_ip_bytes)
        l_port = socket.ntohs(row.dwLocalPort & 0xFFFF)

        r_ip_bytes = bytes(row.ucRemoteAddr)
        r_ip = socket.inet_ntop(socket.AF_INET6, r_ip_bytes)
        r_port = socket.ntohs(row.dwRemotePort & 0xFFFF)

        if state == "LISTEN" or r_ip in ("::", "0:0:0:0:0:0:0:0"):
            r_ip = "*"
            r_port = None

        pid = int(row.dwOwningPid)
        connections.append(
            ConnectionInfo(
                pid=pid,
                process_name=None,
                proto="tcp",
                local_address=l_ip,
                local_port=l_port,
                remote_address=r_ip,
                remote_port=r_port,
                state=state,
            )
        )
    return connections


def _windows_listening_native() -> list[PortBinding]:
    """Retrieve all Windows listening sockets natively using ctypes."""
    bindings = []
    try:
        bindings.extend(_get_extended_tcp_table_ipv4())
    except Exception:
        pass
    try:
        bindings.extend(_get_extended_tcp_table_ipv6())
    except Exception:
        pass
    try:
        bindings.extend(_get_extended_udp_table_ipv4())
    except Exception:
        pass
    try:
        bindings.extend(_get_extended_udp_table_ipv6())
    except Exception:
        pass

    for b in bindings:
        if b.pid:
            info = _get_windows_process_info_native(b.pid)
            if info:
                b.process_name = info.name
    return bindings


def _get_windows_ppid_native(pid: int) -> int | None:
    """Retrieve process parent PID natively on Windows using Toolhelp32Snapshot."""
    if platform.system() != "Windows":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        h_snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if h_snap == -1 or not h_snap:
            return None
        try:
            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
                while True:
                    if pe.th32ProcessID == pid:
                        return pe.th32ParentProcessID
                    if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(h_snap)
    except Exception:
        pass
    return None


def _get_windows_process_info_native(pid: int) -> ProcessInfo | None:
    """Retrieve process name/executable path natively on Windows using ctypes."""
    if platform.system() != "Windows":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        h_proc = kernel32.OpenProcess(0x1000, False, pid)
        if not h_proc:
            return None
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(260)
            exe_path = None
            name = None
            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                exe_path = buf.value
                name = os.path.basename(exe_path)

            if not name:
                return None

            class FILETIME(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                ]
            creation_time = FILETIME()
            exit_time = FILETIME()
            kernel_time = FILETIME()
            user_time = FILETIME()
            start_time = None
            if kernel32.GetProcessTimes(
                h_proc,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time)
            ):
                val = (creation_time.dwHighDateTime << 32) + creation_time.dwLowDateTime
                if val > 0:
                    start_time = (val / 10000000.0) - 11644473600.0

            ppid = _get_windows_ppid_native(pid)

            return ProcessInfo(
                pid=pid,
                name=name,
                exe=exe_path,
                ppid=ppid,
                start_time=start_time
            )
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        pass
    return None


_SUBPROCESS_TIMEOUT = 15


class FallbackInspector(BaseInspector):
    def __init__(self):
        self.system = platform.system()
        self._ps_exe = None
        self._process_info_cache: dict[int, ProcessInfo | None] = {}
        self._tasklist_cache: dict[int, str] | None = None
        self._last_cache_time: float = 0.0
        if self.system == "Windows":
            self._ps_exe = shutil.which("powershell") or shutil.which("pwsh")

    def _clear_cache(self, ttl: float = 0.0) -> None:
        """Clear all per-PID caches before queries.

        If ttl > 0, skips cache clearing if the existing snapshot is newer
        than ttl seconds. This prevents disk churn during high-frequency polling
        (e.g., watch mode).
        """
        now = time.time()
        if ttl > 0 and (now - self._last_cache_time) < ttl and self._last_cache_time > 0:
            return
        self._process_info_cache.clear()
        self._tasklist_cache = None  # Force tasklist re-query on Windows
        self._last_cache_time = now

    def _powershell(self) -> str | None:
        return self._ps_exe

    def _run_subprocess(
        self, cmd: list[str], timeout: int = _SUBPROCESS_TIMEOUT
    ) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

    def _run_powershell_json(
        self, script: str, timeout: int = _SUBPROCESS_TIMEOUT
    ) -> Any | None:
        ps = self._powershell()
        if not ps:
            return None
        try:
            cmd = [
                ps,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ]
            proc = self._run_subprocess(cmd, timeout=timeout)
            if proc.returncode != 0:
                return None
            out = (proc.stdout or "").strip()
            if not out:
                return None
            return json.loads(out)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
            return None

    def _parse_tasklist_csv(self, out: str) -> dict[int, str]:
        names: dict[int, str] = {}
        for line in out.splitlines():
            parts = [
                p.strip().strip('"')
                for p in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line)
            ]
            if len(parts) >= 2:
                try:
                    names[int(parts[1])] = parts[0]
                except ValueError:
                    continue
        return names

    def _ensure_tasklist_cache(self) -> dict[int, str]:
        if self._tasklist_cache is not None:
            return self._tasklist_cache
        self._tasklist_cache = {}
        try:
            proc = self._run_subprocess(["tasklist", "/FO", "CSV", "/NH"])
            self._tasklist_cache = self._parse_tasklist_csv(proc.stdout or "")
        except (subprocess.SubprocessError, OSError):
            pass
        return self._tasklist_cache

    def _process_name_for_pid(self, pid: int) -> str | None:
        return self._ensure_tasklist_cache().get(pid)

    def _binding_from_windows_conn(
        self, item: dict[str, Any], proto: str = "tcp"
    ) -> PortBinding | None:
        try:
            port = int(item.get("LocalPort"))
        except (ValueError, TypeError, AttributeError):
            return None
        pid = None
        try:
            pid = int(item.get("OwningProcess"))
        except (ValueError, TypeError, AttributeError):
            pid = None
        laddr = f"{item.get('LocalAddress')}:{port}"
        state = item.get("State")
        pname = self._process_name_for_pid(pid) if pid else None
        return PortBinding(
            port=port,
            family="IPv4",
            laddr=laddr,
            pid=pid,
            process_name=pname,
            state=state,
            proto=proto,
        )

    def list_listening(self, proto: str = "tcp") -> list[PortBinding]:
        self._clear_cache()  # C1: always fresh snapshot
        if self.system == "Windows":
            bindings: list[PortBinding] = []
            try:
                bindings = _windows_listening_native()
            except Exception:
                pass
            if not bindings:
                bindings = self._windows_listening(proto=proto)
            else:
                if proto == "tcp":
                    bindings = [b for b in bindings if b.proto == "tcp"]
                elif proto == "udp":
                    bindings = [b for b in bindings if b.proto == "udp"]
            return bindings
        else:
            bindings = []
            try:
                bindings = _list_listening_linux_native() or []
            except (OSError, ValueError, IndexError):
                bindings = []
            if not bindings:
                bindings = self._unix_listening(proto=proto)
            else:
                if proto == "tcp":
                    bindings = [b for b in bindings if b.proto == "tcp"]
                elif proto == "udp":
                    bindings = [b for b in bindings if b.proto == "udp"]
            return bindings

    def find_bindings_for_pid(self, pid: int) -> list[PortBinding]:
        """Find all listening port bindings owned by *pid*.

        On Linux, without elevated privileges the global inode→PID map
        (_get_linux_inode_to_pid_map) cannot read /proc/<pid>/fd/ for
        processes owned by other users (e.g. root daemons).  In that case
        the binding appears in /proc/net/tcp with pid=None, so filtering
        list_listening() by pid returns nothing.

        Fallback: read /proc/<pid>/net/tcp[6] directly.  These files reflect
        the process's own network-namespace view and are typically readable
        without root in standard Linux configurations (not locked-down
        containers or hardened namespaces).
        """
        # Stage 1: standard path — works when we own the process or are root
        bindings = [b for b in self.list_listening(proto="both") if b.pid == pid]
        if bindings:
            return bindings

        # Stage 2: Linux-only /proc/<pid>/net fallback
        if self.system == "Linux":
            try:
                ns_bindings = _list_listening_proc_pid_net(pid)
                if ns_bindings:
                    # Attach process name from get_process_info so callers
                    # get a populated process_name field.
                    info = self.get_process_info(pid)
                    pname = info.name if info else None
                    for b in ns_bindings:
                        b.process_name = pname
                    return ns_bindings
            except Exception:
                pass

        return bindings

    def find_bindings_on_port(self, port: int, proto: str = "tcp") -> list[PortBinding]:
        self._clear_cache()  # C1
        if self.system == "Windows":
            try:
                bindings = _windows_listening_native()
                if bindings:
                    filtered = [b for b in bindings if b.port == port]
                    if proto == "tcp":
                        return [b for b in filtered if b.proto == "tcp"]
                    elif proto == "udp":
                        return [b for b in filtered if b.proto == "udp"]
                    return filtered
            except Exception:
                pass
            return self._windows_bindings_on_port(port, proto=proto)
        else:
            try:
                bindings = _list_listening_linux_native() or []
                if bindings:
                    filtered = [b for b in bindings if b.port == port]
                    if proto == "tcp":
                        return [b for b in filtered if b.proto == "tcp"]
                    elif proto == "udp":
                        return [b for b in filtered if b.proto == "udp"]
                    return filtered
            except (OSError, ValueError, IndexError):
                pass
            return [b for b in self._unix_listening(proto=proto) if b.port == port]

    def _windows_bindings_on_port(
        self, port: int, proto: str = "tcp"
    ) -> list[PortBinding]:
        bindings: list[PortBinding] = []
        self._ensure_tasklist_cache()

        def _from_ps(data, bp):
            if data is None:
                return
            items = data if isinstance(data, list) else [data]
            for it in items:
                if bp == "udp":
                    it = dict(it)
                    it.setdefault("State", "UDP")
                b = self._binding_from_windows_conn(it, proto=bp)
                if b:
                    bindings.append(b)

        ps_tcp = None
        ps_udp = None
        if proto == "both":
            ps_both = self._run_powershell_json(
                f"@{{ tcp = @(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess,State); "
                f"udp = @(Get-NetUDPEndpoint -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess) }} | ConvertTo-Json -Depth 4"
            )
            if isinstance(ps_both, dict):
                ps_tcp = ps_both.get("tcp")
                ps_udp = ps_both.get("udp")
        else:
            if proto == "tcp":
                ps_tcp = self._run_powershell_json(
                    f"Get-NetTCPConnection -State Listen -LocalPort {port} | "
                    "Select-Object LocalAddress,LocalPort,OwningProcess,State | ConvertTo-Json -Depth 3"
                )
            elif proto == "udp":
                ps_udp = self._run_powershell_json(
                    f"Get-NetUDPEndpoint -LocalPort {port} | "
                    "Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Depth 3"
                )

        if ps_tcp is not None or ps_udp is not None:
            _from_ps(ps_tcp, "tcp")
            _from_ps(ps_udp, "udp")
            return sorted(bindings, key=lambda b: b.port)

        if not shutil.which("netstat"):
            return bindings
        try:
            proc = self._run_subprocess(["netstat", "-ano"])
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = re.split(r"\s+", line)
                if len(parts) >= 4 and parts[0].upper() in ("TCP", "UDP"):
                    line_proto = parts[0].lower()
                    if proto == "tcp" and line_proto != "tcp":
                        continue
                    if proto == "udp" and line_proto != "udp":
                        continue
                    local_addr = parts[1]
                    if ":" not in local_addr:
                        continue
                    port_str = local_addr.rsplit(":", 1)[-1]
                    try:
                        if int(port_str) != port:
                            continue
                    except ValueError:
                        continue
                    state = (
                        parts[3] if line_proto == "tcp" and len(parts) >= 5 else "UDP"
                    )
                    pid = None
                    try:
                        pid = int(parts[-1])
                    except ValueError:
                        pid = None
                    pname = self._process_name_for_pid(pid) if pid else None
                    bindings.append(
                        PortBinding(
                            port=port,
                            family="IPv4",
                            laddr=local_addr,
                            pid=pid,
                            process_name=pname,
                            state=state,
                            proto=line_proto,
                        )
                    )
        except (subprocess.SubprocessError, OSError, ValueError, IndexError):
            pass
        return sorted(bindings, key=lambda b: b.port)

    def _windows_listening(self, proto: str = "tcp") -> list[PortBinding]:
        bindings: list[PortBinding] = []
        self._ensure_tasklist_cache()

        def _from_ps(data, bp):
            if data is None:
                return
            items = data if isinstance(data, list) else [data]
            for it in items:
                if bp == "udp":
                    it = dict(it)
                    it.setdefault("State", "UDP")
                b = self._binding_from_windows_conn(it, proto=bp)
                if b:
                    bindings.append(b)

        ps_tcp = None
        ps_udp = None
        if proto in ("tcp", "both"):
            ps_tcp = self._run_powershell_json(
                "Get-NetTCPConnection -State Listen | "
                "Select-Object LocalAddress,LocalPort,OwningProcess,State | ConvertTo-Json -Depth 3"
            )
        if proto in ("udp", "both"):
            ps_udp = self._run_powershell_json(
                "Get-NetUDPEndpoint | "
                "Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Depth 3"
            )

        if ps_tcp is not None or ps_udp is not None:
            _from_ps(ps_tcp, "tcp")
            _from_ps(ps_udp, "udp")
            return sorted(bindings, key=lambda b: b.port)

        if not shutil.which("netstat"):
            return bindings
        try:
            proc = self._run_subprocess(["netstat", "-ano"])
            lines = proc.stdout.splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = re.split(r"\s+", line)
                if len(parts) >= 4 and parts[0].upper() in ("TCP", "UDP"):
                    line_proto = parts[0].lower()
                    if proto == "tcp" and line_proto != "tcp":
                        continue
                    if proto == "udp" and line_proto != "udp":
                        continue
                    local_addr = parts[1]
                    state = (
                        parts[3] if line_proto == "tcp" and len(parts) >= 5 else "UDP"
                    )
                    pid = None
                    try:
                        pid = int(parts[-1])
                    except ValueError:
                        pid = None
                    if ":" in local_addr:
                        port_str = local_addr.rsplit(":", 1)[-1]
                        try:
                            port = int(port_str)
                        except ValueError:
                            continue
                        pname = self._process_name_for_pid(pid) if pid else None
                        bindings.append(
                            PortBinding(
                                port=port,
                                family="IPv4",
                                laddr=local_addr,
                                pid=pid,
                                process_name=pname,
                                state=state,
                                proto=line_proto,
                            )
                        )
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        return sorted(bindings, key=lambda b: b.port)

    def _unix_listening(self, proto: str = "tcp") -> list[PortBinding]:
        bindings: list[PortBinding] = []
        if shutil.which("lsof"):
            try:
                proc = self._run_subprocess(["lsof", "-i", "-P", "-n"])
                lines = proc.stdout.splitlines()
                for line in lines:
                    parts = re.split(r"\s+", line)
                    if len(parts) < 9:
                        continue
                    # parts[7] is the TYPE column: "TCP", "UDP", "TCP6", "UDP6", etc.
                    node_type = parts[7].upper()
                    if "TCP" in node_type:
                        line_proto = "tcp"
                    elif "UDP" in node_type:
                        line_proto = "udp"
                    else:
                        continue

                    # For TCP we only want LISTEN entries; UDP has no state
                    if line_proto == "tcp" and "LISTEN" not in line.upper():
                        continue

                    # Filter by requested protocol
                    if proto == "tcp" and line_proto != "tcp":
                        continue
                    if proto == "udp" and line_proto != "udp":
                        continue

                    command = parts[0]
                    pid = None
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        pid = None
                    name_field = parts[8]
                    if ":" in name_field:
                        try:
                            raw_port = name_field.rsplit(":", 1)[-1]
                            # Strip trailing "(LISTEN)" if present
                            raw_port = raw_port.split("(")[0].strip()
                            port = int(raw_port)
                        except ValueError:
                            continue
                        family = "IPv6" if "6" in node_type else "IPv4"
                        state = "LISTEN" if line_proto == "tcp" else "UDP"
                        bindings.append(
                            PortBinding(
                                port=port,
                                family=family,
                                laddr=name_field,
                                pid=pid,
                                process_name=command,
                                state=state,
                                proto=line_proto,
                            )
                        )
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass
        else:
            if shutil.which("ss"):
                try:
                    if proto == "tcp":
                        ss_opts = "-ltnp"
                    elif proto == "udp":
                        ss_opts = "-lunp"
                    else:
                        ss_opts = "-ltunp"
                    proc = self._run_subprocess(["ss", ss_opts])
                    lines = proc.stdout.splitlines()
                    for line in lines:
                        parts = re.split(r"\s+", line)
                        if not parts or parts[0] in ("Netid", "State"):
                            continue
                        netid = parts[0].lower()
                        if "tcp" not in netid and "udp" not in netid:
                            continue
                        line_proto = "tcp" if "tcp" in netid else "udp"
                        # For TCP skip non-LISTEN rows
                        if (
                            line_proto == "tcp"
                            and len(parts) > 1
                            and "LISTEN" not in parts[1].upper()
                        ):
                            continue
                        for token in parts:
                            if ":" in token and re.search(r":\d+$", token):
                                try:
                                    port = int(token.rsplit(":", 1)[-1])
                                    m = re.search(r"pid=(\d+)", line)
                                    pid = int(m.group(1)) if m else None
                                    pname = None
                                    if pid:
                                        info = self.get_process_info(pid)
                                        pname = info.name if info else None
                                    family = "IPv6" if "[" in token else "IPv4"
                                    state = "LISTEN" if line_proto == "tcp" else "UDP"
                                    bindings.append(
                                        PortBinding(
                                            port=port,
                                            family=family,
                                            laddr=token,
                                            pid=pid,
                                            process_name=pname,
                                            state=state,
                                            proto=line_proto,
                                        )
                                    )
                                    break
                                except ValueError:
                                    continue
                except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                    pass
        return sorted(bindings, key=lambda b: b.port)

    def find_pids_on_port(self, port: int, proto: str = "tcp") -> list[int]:
        self._clear_cache()  # C1
        if self.system == "Windows":
            try:
                bindings = _windows_listening_native()
                if bindings:
                    if proto == "tcp":
                        return sorted(
                            {
                                b.pid
                                for b in bindings
                                if b.port == port and b.pid and b.proto == "tcp"
                            }
                        )
                    elif proto == "udp":
                        return sorted(
                            {
                                b.pid
                                for b in bindings
                                if b.port == port and b.pid and b.proto == "udp"
                            }
                        )
                    return sorted({b.pid for b in bindings if b.port == port and b.pid})
            except Exception:
                pass
            return self._windows_pids_on_port(port, proto=proto)
        else:
            try:
                bindings = _list_listening_linux_native() or []
                if bindings:
                    if proto == "tcp":
                        pids = sorted(
                            {
                                b.pid
                                for b in bindings
                                if b.port == port and b.pid and b.proto == "tcp"
                            }
                        )
                    elif proto == "udp":
                        pids = sorted(
                            {
                                b.pid
                                for b in bindings
                                if b.port == port and b.pid and b.proto == "udp"
                            }
                        )
                    else:
                        pids = sorted({b.pid for b in bindings if b.port == port and b.pid})
                    # Only short-circuit when we actually resolved PIDs for this port.
                    # If pids is empty, the port may exist but owning PID is invisible to
                    # unprivileged /proc scanning — fall through to ss/lsof.
                    if pids:
                        return pids
            except (OSError, ValueError, IndexError):
                pass
            return self._unix_pids_on_port(port, proto=proto)

    def _windows_pids_on_port(self, port: int, proto: str = "tcp") -> list[int]:
        pids = set()

        def _collect(data):
            if isinstance(data, list):
                for v in data:
                    try:
                        pids.add(int(v))
                    except ValueError:
                        pass
            elif data is not None:
                try:
                    pids.add(int(data))
                except ValueError:
                    pass

        ps_tcp = None
        ps_udp = None
        if proto in ("tcp", "both"):
            ps_tcp = self._run_powershell_json(
                f"Get-NetTCPConnection -State Listen -LocalPort {port} | "
                "Select-Object -ExpandProperty OwningProcess | ConvertTo-Json -Depth 2"
            )
        if proto in ("udp", "both"):
            ps_udp = self._run_powershell_json(
                f"Get-NetUDPEndpoint -LocalPort {port} | "
                "Select-Object -ExpandProperty OwningProcess | ConvertTo-Json -Depth 2"
            )

        if ps_tcp is not None or ps_udp is not None:
            _collect(ps_tcp)
            _collect(ps_udp)
            return sorted(pids)

        if not shutil.which("netstat"):
            return []
        proc = self._run_subprocess(["netstat", "-ano"])
        if proc.returncode != 0:
            return []
        for line in proc.stdout.splitlines():
            parts = re.split(r"\s+", line.strip())
            if len(parts) >= 4 and parts[0].upper() in ("TCP", "UDP"):
                line_proto = parts[0].lower()
                if proto == "tcp" and line_proto != "tcp":
                    continue
                if proto == "udp" and line_proto != "udp":
                    continue
                local_addr = parts[1]
                if ":" in local_addr and local_addr.rsplit(":", 1)[-1] == str(port):
                    try:
                        pid = int(parts[-1])
                        pids.add(pid)
                    except ValueError:
                        continue
        return sorted(pids)

    def _unix_pids_on_port(self, port: int, proto: str = "tcp") -> list[int]:
        pids = set()
        if shutil.which("lsof"):
            # Use lsof's protocol-specific filter: TCP:port, UDP:port, or :port for both
            if proto == "tcp":
                spec = f"TCP:{port}"
            elif proto == "udp":
                spec = f"UDP:{port}"
            else:
                spec = f":{port}"
            proc = self._run_subprocess(["lsof", "-t", "-i", spec])
            for line in proc.stdout.splitlines():
                try:
                    pids.add(int(line.strip()))
                except ValueError:
                    continue
        else:
            if shutil.which("ss"):
                if proto == "tcp":
                    ss_opts = "-ltnp"
                elif proto == "udp":
                    ss_opts = "-lunp"
                else:
                    ss_opts = "-ltunp"
                proc = self._run_subprocess(["ss", ss_opts])
                for line in proc.stdout.splitlines():
                    if f":{port} " in line or line.endswith(f":{port}"):
                        m = re.search(r"pid=(\d+)", line)
                        if m:
                            try:
                                pids.add(int(m.group(1)))
                            except ValueError:
                                continue
        return sorted(pids)

    def get_process_info(self, pid: int) -> ProcessInfo | None:
        if pid in self._process_info_cache:
            return self._process_info_cache[pid]
        info = self._fetch_process_info(pid)
        self._process_info_cache[pid] = info
        return info

    def _fetch_process_info(self, pid: int) -> ProcessInfo | None:
        try:
            if self.system == "Windows":
                try:
                    info = _get_windows_process_info_native(pid)
                    if info:
                        return info
                except Exception:
                    pass
                name = self._process_name_for_pid(pid)
                if name:
                    return ProcessInfo(pid=pid, name=name)

                proc = self._run_subprocess(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    timeout=5,
                )
                out = proc.stdout.strip()
                if out and "No tasks are running" not in out:
                    parts = [
                        p.strip().strip('"')
                        for p in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', out)
                    ]
                    if parts:
                        return ProcessInfo(pid=pid, name=parts[0])
            else:
                try:
                    info = _get_linux_process_info(pid)
                    if info:
                        return info
                except (OSError, ValueError, IndexError, AttributeError):
                    pass
                from datetime import datetime
                proc = self._run_subprocess(
                    ["ps", "-p", str(pid), "-o", "pid=,ppid=,comm=,user=,lstart=,args="]
                )
                out = proc.stdout.strip()
                if not out:
                    return None
                parts = re.split(r"\s+", out)
                if len(parts) >= 10:
                    try:
                        ppid = int(parts[1])
                    except (ValueError, IndexError):
                        ppid = None
                    name = parts[2]
                    user = parts[3]
                    start_time = None
                    try:
                        date_str = " ".join(parts[4:9])
                        date_str = re.sub(r"\s+", " ", date_str)
                        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y")
                        start_time = dt.timestamp()
                    except Exception:
                        pass
                    cmdline = parts[9:] if len(parts) > 9 else None
                    cwd = None
                    if shutil.which("lsof"):
                        try:
                            res = self._run_subprocess(["lsof", "-p", str(pid), "-a", "-d", "cwd", "-F", "n"])
                            for line in res.stdout.splitlines():
                                if line.startswith("n"):
                                    cwd = line[1:].strip()
                                    break
                        except Exception:
                            pass
                    return ProcessInfo(
                        pid=pid,
                        name=name,
                        cmdline=cmdline,
                        user=user,
                        ppid=ppid,
                        cwd=cwd,
                        start_time=start_time,
                    )
        except Exception:
            return None
        return None

    def find_pids_by_name(self, name: str, exact: bool = False) -> list[int]:
        self._clear_cache()  # C1
        self_pid = os.getpid()  # R5: never return kport's own PID
        if self.system == "Windows":
            proc = self._run_subprocess(["tasklist", "/FO", "CSV", "/NH"])
            out = proc.stdout or ""
            pids = []
            name_lower = name.lower()
            for line in out.splitlines():
                parts = [
                    p.strip().strip('"')
                    for p in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line)
                ]
                if len(parts) >= 2:
                    pname = parts[0]
                    pid_s = parts[1]
                    try:
                        pid = int(pid_s)
                    except ValueError:
                        continue
                    if pid == self_pid:
                        continue  # R5: skip self
                    match = (
                        (pname.lower() == name_lower)
                        if exact
                        else (name_lower in pname.lower())
                    )
                    if match:
                        pids.append(pid)
            return sorted(pids)
        else:
            if shutil.which("pgrep"):
                args = ["pgrep", "-i", name] if not exact else ["pgrep", "-x", name]
                proc = self._run_subprocess(args)
                out = proc.stdout or ""
                pids = []
                for line in out.splitlines():
                    try:
                        pid = int(line.strip())
                        if pid != self_pid:
                            pids.append(pid)
                    except ValueError:
                        continue
                return sorted(pids)
            else:
                proc = self._run_subprocess(["ps", "-ef"])
                out = proc.stdout or ""
                pids = []
                for line in out.splitlines():
                    if line.strip().startswith("UID"):
                        continue
                    parts = re.split(r"\s+", line.strip(), maxsplit=7)
                    if len(parts) >= 8:
                        pid_str = parts[1]
                        cmd_field = parts[7]
                        cmd_word = cmd_field.split()[0] if cmd_field.split() else ""
                        pname = os.path.basename(cmd_word).strip("[]()")
                        match = (
                            (pname == name)
                            if exact
                            else (name.lower() in pname.lower())
                        )
                        if match:
                            try:
                                pid = int(pid_str)
                                if pid != self_pid:
                                    pids.append(pid)
                            except ValueError:
                                continue
                return sorted(set(pids))

    def find_ports_by_process_name(
        self, name: str, exact: bool = False, proto: str = "tcp"
    ) -> list[PortBinding]:
        """
        Find all port bindings for processes matching `name`.

        Three-layer cross-platform strategy — maximises data available without
        elevation before ever suggesting 'try sudo':

        Layer 1 (Port table, no elevation needed):
          - Linux:   /proc/net/tcp* inode map + /proc/<pid>/fd socket resolution
          - Windows: ctypes IPHLPAPI GetExtendedTcpTable (gives port→PID directly)
          - macOS:   lsof -i -P -n (works for own processes without sudo)

        Layer 2 (Namespace / container fallback, Linux only):
          - /proc/<pid>/net/tcp — per-process network namespace view.
            Snap / Docker processes live in their own netns; the host /proc/net/tcp
            doesn't see their sockets, but /proc/<pid>/net/tcp does, and it's
            readable without root in most snap configurations.

        Layer 3 (lsof / subprocess fallback):
          - Standard lsof / ss parsing for macOS and Linux without /proc support.
        """
        self._clear_cache()  # C1
        results: list[PortBinding] = []
        name_lower = name.lower()

        # ------------------------------------------------------------------ #
        # Windows path — Layer 1: native ctypes gives port→PID without admin  #
        # ------------------------------------------------------------------ #
        if self.system == "Windows":
            # Step 1: find PIDs by name (tasklist works without admin)
            pids_by_name = set(self.find_pids_by_name(name, exact=exact))
            if not pids_by_name:
                return results

            # Step 2: get all listening bindings via native IPHLPAPI (no elevation)
            try:
                all_bindings = _windows_listening_native()
            except Exception:
                all_bindings = []

            if not all_bindings:
                # Step 2b: subprocess fallback via netstat -ano
                all_bindings = self._windows_listening()

            # Step 3: filter bindings to the PIDs we found
            for b in all_bindings:
                if b.pid in pids_by_name:
                    # Enrich process name if missing
                    if not b.process_name:
                        info = self.get_process_info(b.pid)
                        b.process_name = info.name if info else name
                    results.append(b)

            return sorted(results, key=lambda b: (b.pid or 0, b.port))

        # ------------------------------------------------------------------ #
        # macOS / Linux — try lsof first (fast, works for own processes)      #
        # ------------------------------------------------------------------ #
        if shutil.which("lsof"):
            try:
                proc = self._run_subprocess(["lsof", "-i", "-P", "-n"])
                out = proc.stdout or ""
                for line in out.splitlines():
                    # C2 fix: correct filter logic.
                    parts = re.split(r"\s+", line)
                    if len(parts) < 9:
                        continue
                    command = parts[0]
                    if exact:
                        if command.lower() != name_lower:
                            continue
                    else:
                        if name_lower not in line.lower():
                            continue
                    pid_s = parts[1]
                    try:
                        pid = int(pid_s)
                    except ValueError:
                        pid = None
                    addr = parts[8]
                    if ":" not in addr:
                        continue
                    try:
                        port = int(addr.rsplit(":", 1)[-1])
                    except ValueError:
                        continue
                    results.append(
                        PortBinding(
                            port=port,
                            family="IPv4",
                            laddr=addr,
                            pid=pid,
                            process_name=command,
                            state="LISTEN" if "LISTEN" in line else None,
                        )
                    )
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass

            # If lsof returned results, we're done
            if results:
                return sorted(results, key=lambda b: (b.pid or 0, b.port))

        # ------------------------------------------------------------------ #
        # Linux Layer 1 fallback: /proc/net/tcp inode map                     #
        # Works for processes in the host network namespace.                   #
        # ------------------------------------------------------------------ #
        if platform.system() == "Linux":
            pids_by_name = set(self.find_pids_by_name(name, exact=exact))
            if pids_by_name:
                # Layer 1: host-level /proc/net/tcp (fast, no elevation)
                try:
                    host_bindings = _list_listening_linux_native() or []
                    for b in host_bindings:
                        if b.pid in pids_by_name:
                            if not b.process_name:
                                info = self.get_process_info(b.pid)
                                b.process_name = info.name if info else name
                            results.append(b)
                except (OSError, ValueError, IndexError):
                    pass

                # Layer 2: per-process network namespace (/proc/<pid>/net/tcp)
                # This handles snap-packaged / container processes whose sockets
                # don't appear in the host /proc/net/tcp view.
                seen_pids = {b.pid for b in results if b.pid}
                for pid in pids_by_name:
                    if pid not in seen_pids:
                        ns_bindings = _list_listening_proc_pid_net(pid)
                        for b in ns_bindings:
                            b.pid = pid
                            if not b.process_name:
                                info = self.get_process_info(pid)
                                b.process_name = info.name if info else name
                            results.append(b)

        # ------------------------------------------------------------------ #
        # Final fallback: ss (Linux without /proc write access)               #
        # ------------------------------------------------------------------ #
        if not results and shutil.which("ss"):
            pids_by_name = getattr(self, "_last_pids_by_name", None) or set(
                self.find_pids_by_name(name, exact=exact)
            )
            try:
                proc = self._run_subprocess(["ss", "-ltnp"])
                for line in proc.stdout.splitlines():
                    if "LISTEN" not in line:
                        continue
                    m_pid = re.search(r"pid=(\d+)", line)
                    if not m_pid:
                        continue
                    try:
                        pid = int(m_pid.group(1))
                    except ValueError:
                        continue
                    if pid not in pids_by_name:
                        continue
                    for token in re.split(r"\s+", line):
                        if ":" in token and re.search(r":\d+$", token):
                            try:
                                port = int(token.rsplit(":", 1)[-1])
                                info = self.get_process_info(pid)
                                pname = info.name if info else name
                                results.append(
                                    PortBinding(
                                        port=port,
                                        family="IPv4",
                                        laddr=token,
                                        pid=pid,
                                        process_name=pname,
                                        state="LISTEN",
                                    )
                                )
                                break
                            except ValueError:
                                continue
            except (subprocess.SubprocessError, OSError):
                pass

        return sorted(results, key=lambda b: (b.pid or 0, b.port))

    def send_signal(self, pid: int, sig: int) -> bool:
        if self.system == "Windows":
            import signal as signal_module

            if sig == signal_module.SIGTERM:
                proc = self._run_subprocess(["taskkill", "/PID", str(pid)])
                if proc.returncode == 0:
                    return True
                raise RuntimeError(f"taskkill gentle failed: {proc.stderr.strip()}")
            else:
                proc = self._run_subprocess(["taskkill", "/PID", str(pid), "/F"])
                if proc.returncode == 0:
                    return True
                raise RuntimeError(f"taskkill forced failed: {proc.stderr.strip()}")
        else:
            try:
                os.kill(pid, sig)
                return True
            except ProcessLookupError:
                raise ProcessLookupError()
            except PermissionError:
                raise PermissionError()
            except OSError as e:
                raise RuntimeError(str(e))

    def is_process_alive(self, pid: int) -> bool:
        if self.system == "Windows":
            proc = self._run_subprocess(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=5
            )
            return f" {pid} " in f" {proc.stdout} " or f'"{pid}"' in proc.stdout
        else:
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError) as e:
                return isinstance(e, PermissionError)

    def get_child_pids(self, pid: int) -> list[int]:
        if self.system == "Windows":
            ps = self._powershell()
            if ps:
                try:
                    cmd = [
                        ps,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {pid}' | Select-Object -ExpandProperty ProcessId",
                    ]
                    proc = self._run_subprocess(cmd)
                    if proc.returncode == 0:
                        pids = []
                        for line in proc.stdout.splitlines():
                            line = line.strip()
                            if line.isdigit():
                                pids.append(int(line))
                        all_pids = []
                        for child in pids:
                            all_pids.append(child)
                            all_pids.extend(self.get_child_pids(child))
                        return sorted(set(all_pids))
                except (subprocess.SubprocessError, OSError, ValueError):
                    pass
            try:
                proc = self._run_subprocess(
                    [
                        "wmic",
                        "process",
                        "where",
                        f"ParentProcessId={pid}",
                        "get",
                        "ProcessId",
                    ]
                )
                if proc.returncode == 0:
                    pids = []
                    for line in proc.stdout.splitlines():
                        line = line.strip()
                        if line.isdigit():
                            pids.append(int(line))
                    all_pids = []
                    for child in pids:
                        all_pids.append(child)
                        all_pids.extend(self.get_child_pids(child))
                    return sorted(set(all_pids))
            except (subprocess.SubprocessError, OSError, ValueError):
                pass
            return []
        else:
            try:
                proc = self._run_subprocess(["ps", "-ax", "-o", "ppid=,pid="])
                if proc.returncode == 0:
                    parent_to_children = {}
                    for line in proc.stdout.splitlines():
                        parts = line.strip().split()
                        if len(parts) == 2:
                            try:
                                ppid = int(parts[0])
                                cpid = int(parts[1])
                                parent_to_children.setdefault(ppid, []).append(cpid)
                            except ValueError:
                                continue
                    descendants = []

                    def gather(parent):
                        for child in parent_to_children.get(parent, []):
                            descendants.append(child)
                            gather(child)

                    gather(pid)
                    return sorted(set(descendants))
            except (subprocess.SubprocessError, OSError, ValueError, RecursionError):
                pass
            return []

    def list_connections(self) -> list[ConnectionInfo]:
        self._clear_cache()
        if self.system == "Windows":
            return self._windows_connections()
        elif self.system == "Linux":
            return self._linux_connections()
        else:
            return self._unix_connections()

    def _windows_connections(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        try:
            connections.extend(_get_extended_tcp_connections_ipv4())
        except Exception:
            pass
        try:
            connections.extend(_get_extended_tcp_connections_ipv6())
        except Exception:
            pass

        if not connections:
            connections = self._windows_connections_cmd_fallback()

        # Deduplicate process name lookup by PID
        unique_pids = {c.pid for c in connections if c.pid}
        pid_to_name: dict[int, str | None] = {}
        for pid in unique_pids:
            if pid:
                info = self.get_process_info(pid)
                pid_to_name[pid] = info.name if info else None

        for c in connections:
            if c.pid:
                c.process_name = pid_to_name.get(c.pid)

        return connections

    def _windows_connections_cmd_fallback(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        ps = self._powershell()
        if ps:
            data = self._run_powershell_json(
                "Get-NetTCPConnection | "
                "Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,OwningProcess,State | ConvertTo-Json -Depth 3"
            )
            if data is not None:
                items = data if isinstance(data, list) else [data]
                for item in items:
                    try:
                        l_port = int(item.get("LocalPort"))
                        pid = int(item.get("OwningProcess")) if item.get("OwningProcess") else None
                        l_ip = item.get("LocalAddress")
                        r_ip = item.get("RemoteAddress", "*")
                        r_port = int(item.get("RemotePort")) if item.get("RemotePort") else None
                        state = item.get("State", "UNKNOWN")
                        if r_ip in ("0.0.0.0", "::", "*") or state == "Listen":
                            r_ip = "*"
                            r_port = None
                        connections.append(
                            ConnectionInfo(
                                pid=pid,
                                process_name=None,
                                proto="tcp",
                                local_address=l_ip,
                                local_port=l_port,
                                remote_address=r_ip,
                                remote_port=r_port,
                                state=state.upper(),
                            )
                        )
                    except (ValueError, TypeError, KeyError):
                        continue
                return connections

        if not shutil.which("netstat"):
            return connections

        try:
            proc = self._run_subprocess(["netstat", "-ano"])
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line or "Proto" in line or "Active" in line:
                    continue
                parts = re.split(r"\s+", line)
                if len(parts) >= 4 and parts[0].upper() == "TCP":
                    local_addr = parts[1]
                    remote_addr = parts[2]
                    state = parts[3]
                    pid_str = parts[4] if len(parts) >= 5 else None

                    try:
                        pid = int(pid_str) if pid_str else None
                    except ValueError:
                        pid = None

                    try:
                        l_ip, l_port_str = local_addr.rsplit(":", 1)
                        l_port = int(l_port_str)
                    except ValueError:
                        continue

                    r_ip = "*"
                    r_port = None
                    if remote_addr and ":" in remote_addr:
                        try:
                            r_ip, r_port_str = remote_addr.rsplit(":", 1)
                            r_port = int(r_port_str)
                        except ValueError:
                            pass

                    if state == "LISTENING" or r_ip in ("0.0.0.0", "::", "*"):
                        r_ip = "*"
                        r_port = None

                    connections.append(
                        ConnectionInfo(
                            pid=pid,
                            process_name=None,
                            proto="tcp",
                            local_address=l_ip,
                            local_port=l_port,
                            remote_address=r_ip,
                            remote_port=r_port,
                            state="LISTEN" if state == "LISTENING" else state,
                        )
                    )
        except Exception:
            pass
        return connections

    def _linux_connections(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        inode_to_pid = _get_linux_inode_to_pid_map()

        raw_conns = []
        raw_conns.extend(_parse_proc_net_connections("/proc/net/tcp", "IPv4"))
        raw_conns.extend(_parse_proc_net_connections("/proc/net/tcp6", "IPv6"))

        # Deduplicate process name lookup
        unique_pids = {inode_to_pid.get(inode) for _, _, _, _, _, inode in raw_conns if inode_to_pid.get(inode)}
        pid_to_name: dict[int, str | None] = {}
        for pid in unique_pids:
            if pid:
                info = self.get_process_info(pid)
                pid_to_name[pid] = info.name if info else None

        for l_ip, l_port, r_ip, r_port, state, inode in raw_conns:
            pid = inode_to_pid.get(inode)
            pname = pid_to_name.get(pid) if pid else None
            connections.append(
                ConnectionInfo(
                    pid=pid,
                    process_name=pname,
                    proto="tcp",
                    local_address=l_ip,
                    local_port=l_port,
                    remote_address=r_ip,
                    remote_port=r_port if r_ip != "*" else None,
                    state=state,
                )
            )

        if not connections and shutil.which("ss"):
            connections = self._linux_connections_ss_fallback()

        return connections

    def _linux_connections_ss_fallback(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        try:
            proc = self._run_subprocess(["ss", "-tanp"])
            for line in proc.stdout.splitlines():
                if "State" in line or not line.strip():
                    continue
                parts = re.split(r"\s+", line.strip())
                if len(parts) >= 5:
                    state = parts[0].upper()
                    laddr = parts[3]
                    raddr = parts[4]
                    users = parts[5] if len(parts) >= 6 else ""

                    try:
                        l_ip, l_port_str = laddr.rsplit(":", 1)
                        l_port = int(l_port_str)
                    except ValueError:
                        continue

                    r_ip = "*"
                    r_port = None
                    if raddr and ":" in raddr:
                        try:
                            r_ip, r_port_str = raddr.rsplit(":", 1)
                            r_port = int(r_port_str)
                        except ValueError:
                            pass

                    if state == "LISTEN" or r_ip in ("0.0.0.0", "[::]", "::", "*"):
                        r_ip = "*"
                        r_port = None

                    pid = None
                    pname = None
                    m = re.search(r"pid=(\d+)", users)
                    if m:
                        pid = int(m.group(1))
                        m_name = re.search(r'"([^"]+)"', users)
                        if m_name:
                            pname = m_name.group(1)

                    connections.append(
                        ConnectionInfo(
                            pid=pid,
                            process_name=pname,
                            proto="tcp",
                            local_address=l_ip,
                            local_port=l_port,
                            remote_address=r_ip,
                            remote_port=r_port,
                            state=state,
                        )
                    )
        except Exception:
            pass
        return connections

    def _unix_connections(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        if not shutil.which("lsof"):
            return connections

        try:
            proc = self._run_subprocess(["lsof", "-nP", "-iTCP"])
            for line in proc.stdout.splitlines():
                if "COMMAND" in line or not line.strip():
                    continue
                parts = re.split(r"\s+", line.strip())
                if len(parts) < 9:
                    continue

                pname = parts[0]
                try:
                    pid = int(parts[1])
                except ValueError:
                    pid = None

                name_field = parts[8]
                state = "UNKNOWN"
                m_state = re.search(r"\((\w+)\)", name_field)
                if m_state:
                    state = m_state.group(1).upper()
                    name_field = name_field.split("(")[0].strip()

                l_ip = "*"
                l_port = 0
                r_ip = "*"
                r_port = None

                if "->" in name_field:
                    local_part, remote_part = name_field.split("->", 1)
                    try:
                        l_ip, l_port_str = local_part.rsplit(":", 1)
                        l_port = int(l_port_str)
                    except ValueError:
                        pass

                    try:
                        r_ip, r_port_str = remote_part.rsplit(":", 1)
                        r_port = int(r_port_str)
                    except ValueError:
                        pass
                else:
                    try:
                        l_ip, l_port_str = name_field.rsplit(":", 1)
                        l_port = int(l_port_str)
                    except ValueError:
                        pass

                if state == "LISTEN" or r_ip in ("0.0.0.0", "::", "*"):
                    r_ip = "*"
                    r_port = None

                connections.append(
                    ConnectionInfo(
                        pid=pid,
                        process_name=pname,
                        proto="tcp",
                        local_address=l_ip,
                        local_port=l_port,
                        remote_address=r_ip,
                        remote_port=r_port,
                        state=state,
                    )
                )
        except Exception:
            pass
        return connections
