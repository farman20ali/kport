"""
Psutil-backed inspector implementation for kport.
Leverages psutil to get robust, cross-platform networking and process information.
"""

from __future__ import annotations

import os

import psutil  # Safe to import because this implementation is dynamically loaded only when psutil is active.

# Ensure psutil.Error base exception exists even under mock/stub environments
if not hasattr(psutil, "Error"):
    psutil.Error = Exception  # type: ignore[attr-defined]

from .base import BaseInspector, ConnectionInfo, PortBinding, ProcessInfo


class PsutilInspector(BaseInspector):
    def list_listening(self, proto: str = "both") -> list[PortBinding]:
        bindings: dict[tuple, PortBinding] = {}
        kinds = (
            ["tcp"]
            if proto == "tcp"
            else (["udp"] if proto == "udp" else ["tcp", "udp"])
        )
        for kind in kinds:
            try:
                conns = psutil.net_connections(kind=kind)
            except psutil.Error:
                continue
            for conn in conns:
                if not conn.laddr:
                    continue
                if kind == "tcp" and conn.status != psutil.CONN_LISTEN:
                    continue
                laddr = (
                    f"{conn.laddr.ip}:{conn.laddr.port}"
                    if hasattr(conn.laddr, "ip")
                    else f"{conn.laddr[0]}:{conn.laddr[1]}"
                )
                port = conn.laddr.port if hasattr(conn.laddr, "port") else conn.laddr[1]
                family = "IPv6" if conn.family.name == "AF_INET6" else "IPv4"
                state = "UDP" if kind == "udp" and (not conn.status or conn.status == psutil.CONN_NONE) else conn.status
                pid = conn.pid
                proc_name = None
                if pid:
                    try:
                        p = psutil.Process(pid)
                        proc_name = p.name()
                    except psutil.Error:
                        proc_name = None
                key = (port, family, pid, state)
                if key not in bindings:
                    bindings[key] = PortBinding(
                        port=port,
                        family=family,
                        laddr=laddr,
                        pid=pid,
                        process_name=proc_name,
                        state=state,
                        proto="tcp" if kind == "tcp" else "udp",
                    )
        return sorted(bindings.values(), key=lambda b: b.port)

    def find_pids_on_port(self, port: int, proto: str = "both") -> list[int]:
        pids = set()
        kinds = (
            ["tcp"]
            if proto == "tcp"
            else (["udp"] if proto == "udp" else ["tcp", "udp"])
        )
        for kind in kinds:
            try:
                conns = psutil.net_connections(kind=kind)
            except psutil.Error:
                continue
            for conn in conns:
                if not conn.laddr:
                    continue
                if kind == "tcp" and conn.status != psutil.CONN_LISTEN:
                    continue
                try:
                    conn_port = (
                        conn.laddr.port
                        if hasattr(conn.laddr, "port")
                        else conn.laddr[1]
                    )
                except (AttributeError, IndexError, TypeError):
                    continue
                if conn_port == port and conn.pid:
                    pids.add(conn.pid)
        return sorted(pids)

    def find_bindings_on_port(self, port: int, proto: str = "both") -> list[PortBinding]:
        bindings: list[PortBinding] = []
        kinds = (
            ["tcp"]
            if proto == "tcp"
            else (["udp"] if proto == "udp" else ["tcp", "udp"])
        )
        for kind in kinds:
            try:
                conns = psutil.net_connections(kind=kind)
            except psutil.Error:
                continue
            for conn in conns:
                if not conn.laddr:
                    continue
                if kind == "tcp" and conn.status != psutil.CONN_LISTEN:
                    continue
                try:
                    conn_port = (
                        conn.laddr.port
                        if hasattr(conn.laddr, "port")
                        else conn.laddr[1]
                    )
                except (AttributeError, IndexError, TypeError):
                    continue
                if conn_port != port:
                    continue
                laddr = (
                    f"{conn.laddr.ip}:{conn.laddr.port}"
                    if hasattr(conn.laddr, "ip")
                    else f"{conn.laddr[0]}:{conn.laddr[1]}"
                )
                family = "IPv6" if conn.family.name == "AF_INET6" else "IPv4"
                pid = conn.pid
                proc_name = None
                if pid:
                    try:
                        proc_name = psutil.Process(pid).name()
                    except psutil.Error:
                        proc_name = None
                bindings.append(
                    PortBinding(
                        port=conn_port,
                        family=family,
                        laddr=laddr,
                        pid=pid,
                        process_name=proc_name,
                        state="UDP" if kind == "udp" and (not conn.status or conn.status == psutil.CONN_NONE) else conn.status,
                        proto="tcp" if kind == "tcp" else "udp",
                    )
                )
        return sorted(bindings, key=lambda b: b.port)

    def list_connections(self) -> list[ConnectionInfo]:
        connections: list[ConnectionInfo] = []
        try:
            conns = psutil.net_connections(kind="tcp")
        except psutil.Error:
            return []

        # Deduplicate process name lookup by PID to avoid N connections * N queries
        unique_pids = {conn.pid for conn in conns if conn.pid}
        pid_to_name: dict[int, str | None] = {}
        for pid in unique_pids:
            try:
                p = psutil.Process(pid)
                pid_to_name[pid] = p.name()
            except psutil.Error:
                pid_to_name[pid] = None

        for conn in conns:
            if not conn.laddr:
                continue
            l_ip = conn.laddr.ip if hasattr(conn.laddr, "ip") else conn.laddr[0]
            l_port = conn.laddr.port if hasattr(conn.laddr, "port") else conn.laddr[1]

            r_ip = "*"
            r_port = None
            if conn.raddr:
                r_ip = conn.raddr.ip if hasattr(conn.raddr, "ip") else conn.raddr[0]
                r_port = conn.raddr.port if hasattr(conn.raddr, "port") else conn.raddr[1]

            pid = conn.pid
            pname = pid_to_name.get(pid) if pid else None

            connections.append(
                ConnectionInfo(
                    pid=pid,
                    process_name=pname,
                    proto="tcp",
                    local_address=l_ip,
                    local_port=l_port,
                    remote_address=r_ip,
                    remote_port=r_port,
                    state=conn.status,
                )
            )

        return sorted(connections, key=lambda c: (c.pid or 0, c.local_port))

    def get_process_info(self, pid: int) -> ProcessInfo | None:
        try:
            p = psutil.Process(pid)
            # R7 fix: store exe() result once — it triggers a syscall each time.
            exe = None
            try:
                exe = p.exe() or None
            except psutil.Error:
                pass
            cmdline = None
            try:
                cmdline = p.cmdline() or None
            except psutil.Error:
                pass
            user = None
            try:
                user = p.username() if hasattr(p, "username") else None
            except psutil.Error:
                pass
            ppid = None
            try:
                ppid = p.ppid()
            except psutil.Error:
                pass
            cwd = None
            try:
                cwd = p.cwd() or None
            except psutil.Error:
                pass
            start_time = None
            try:
                start_time = p.create_time() or None
            except psutil.Error:
                pass
            return ProcessInfo(
                pid=pid,
                name=p.name(),
                exe=exe,
                cmdline=cmdline,
                user=user,
                ppid=ppid,
                cwd=cwd,
                start_time=start_time,
            )
        except psutil.Error:
            return None

    def find_pids_by_name(self, name: str, exact: bool = False) -> list[int]:
        out = []
        name_lower = name.lower()
        self_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pid = p.info["pid"]
                if pid == self_pid:
                    continue
                pname = p.info["name"] or ""
                compare = pname.lower()
                match = (
                    (compare == name_lower)
                    if exact
                    else (name_lower in compare)
                )
                if match:
                    out.append(pid)
            except psutil.Error:
                continue
        return sorted(set(out))


    def find_ports_by_process_name(
        self, name: str, exact: bool = False, proto: str = "both"
    ) -> list[PortBinding]:
        results: list[PortBinding] = []
        name_lower = name.lower()
        kinds = (
            ["tcp"]
            if proto == "tcp"
            else (["udp"] if proto == "udp" else ["tcp", "udp"])
        )
        for kind in kinds:
            try:
                conns = psutil.net_connections(kind=kind)
            except psutil.Error:
                continue
            for conn in conns:
                if not conn.laddr:
                    continue
                if kind == "tcp" and conn.status != psutil.CONN_LISTEN:
                    continue
                pid = conn.pid
                if not pid:
                    continue
                try:
                    p = psutil.Process(pid)
                    pname = (p.name() or "").lower()
                    cmdline = " ".join(p.cmdline() or []).lower()
                    matched = (
                        (pname == name_lower)
                        if exact
                        else (name_lower in pname or name_lower in cmdline)
                    )
                    if matched:
                        laddr = (
                            f"{conn.laddr.ip}:{conn.laddr.port}"
                            if hasattr(conn.laddr, "ip")
                            else f"{conn.laddr[0]}:{conn.laddr[1]}"
                        )
                        family = "IPv6" if conn.family.name == "AF_INET6" else "IPv4"
                        results.append(
                            PortBinding(
                                port=conn.laddr.port
                                if hasattr(conn.laddr, "port")
                                else conn.laddr[1],
                                family=family,
                                laddr=laddr,
                                pid=pid,
                                process_name=p.name(),
                                state=conn.status,
                                proto="tcp" if kind == "tcp" else "udp",
                            )
                        )
                except psutil.Error:
                    continue
        return sorted(results, key=lambda b: (b.pid or 0, b.port))

    def send_signal(self, pid: int, sig: int) -> bool:
        try:
            os.kill(pid, sig)
            return True
        except ProcessLookupError:
            raise ProcessLookupError()
        except PermissionError:
            raise PermissionError()
        except OSError as e:
            # On Windows, ERROR_INVALID_PARAMETER (winerror 87 / errno 22) indicates the PID no longer exists
            if getattr(e, "winerror", None) == 87 or getattr(e, "errno", None) in (22, 3):
                raise ProcessLookupError() from e
            raise RuntimeError(str(e))

    def is_process_alive(self, pid: int) -> bool:
        try:
            p = psutil.Process(pid)
            # Zombie processes are technically dead and ignore signals; treat them as dead
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.Error:
            try:
                # Fallback to checking via os.kill(pid, 0)
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                return False

    def get_child_pids(self, pid: int) -> list[int]:
        try:
            p = psutil.Process(pid)
            return [c.pid for c in p.children(recursive=True)]
        except psutil.Error:
            return []
