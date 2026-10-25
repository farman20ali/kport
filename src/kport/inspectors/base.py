"""
Base inspector interface and class definitions for kport.
Implements the unified, escalated port-killing and validation workflow.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

# Fallback for Windows where signal.SIGKILL is not defined in standard library
SIGKILL = getattr(signal, "SIGKILL", 9)

from kport.constants import RUNTIME_ENRICHMENT_NAMES

# ---------------------------------------------------------------------------
# Process name enrichment
# ---------------------------------------------------------------------------


def enrich_process_name(name: str, cmdline: list[str] | None) -> str:
    """
    Enrich generic runtime process names with their script / module / jar.

    For example:
      node  + ["/usr/bin/node", "server.js"]     → "node (server.js)"
      python3 + ["/usr/bin/python3", "-m", "http.server"] → "python3 (http.server)"
      java  + ["/usr/bin/java", "-jar", "app.jar"] → "java (app.jar)"

    Returns the original name unchanged if enrichment is not applicable.
    """
    if not cmdline or len(cmdline) < 2:
        return name

    base = os.path.basename(name).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    name_base = name.lower()
    if name_base.endswith(".exe"):
        name_base = name_base[:-4]
    if (
        base not in RUNTIME_ENRICHMENT_NAMES
        and name_base not in RUNTIME_ENRICHMENT_NAMES
    ):
        return name

    # Walk arguments; skip the executable itself (index 0)
    args = cmdline[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        # Python -m <module>
        if arg == "-m" and i + 1 < len(args):
            module = args[i + 1]
            # Take only the first component (e.g. "http.server" from "http.server")
            return f"{name} ({module})"
        # Java -jar <file>
        if arg in ("-jar", "--jar") and i + 1 < len(args):
            return f"{name} ({os.path.basename(args[i + 1])})"
        # Any argument that looks like a script/jar file and isn't a flag
        if not arg.startswith("-"):
            _, ext = os.path.splitext(arg)
            if ext.lower() in (
                ".js",
                ".mjs",
                ".cjs",
                ".ts",
                ".py",
                ".rb",
                ".php",
                ".jar",
                ".war",
                ".ear",
                ".pl",
                ".pm",
            ):
                return f"{name} ({os.path.basename(arg)})"
            # node / bun / deno — first positional that is a bare file name
            if base in {"node", "nodejs", "bun", "deno", "tsx", "ts-node"}:
                return f"{name} ({os.path.basename(arg)})"
        i += 1

    return name


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: str | None = None
    cmdline: list[str] | None = None
    user: str | None = None
    ppid: int | None = None
    cwd: str | None = None
    start_time: float | None = None

    def __post_init__(self) -> None:
        """Auto-enrich generic runtime names with their script/jar argument."""
        self.name = enrich_process_name(self.name, self.cmdline)


@dataclass
class PortBinding:
    port: int
    family: str
    laddr: str
    pid: int | None = None
    process_name: str | None = None
    state: str | None = None
    proto: str = "tcp"


@dataclass
class ConnectionInfo:
    pid: int | None
    process_name: str | None
    proto: str
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int | None
    state: str


# ---------------------------------------------------------------------------
# Privilege escalation helpers
# ---------------------------------------------------------------------------


def _escalate_kill_unix(
    pid: int,
    sig: int,
    assume_yes: bool,
    debug: bool = False,
    confirm_fn: Callable[[str], bool] | None = None,
) -> bool:
    """
    Attempt a privilege-escalated kill on Unix via sudo.
    Returns True if the process disappeared after the sudo kill, False otherwise.
    """
    sig_name = "KILL" if sig == SIGKILL else "TERM"
    prompt = (
        f"\nPID {pid} requires elevated privileges (sudo) to terminate. "
        f"Attempt 'sudo kill -{sig_name} {pid}'?"
    )
    if not assume_yes:
        if confirm_fn is None:
            return False
        if not confirm_fn(prompt):
            return False

    sudo = shutil.which("sudo")
    if not sudo:
        # Provide actionable hint when sudo is not in PATH (common with pip/pipx installs)
        _YELLOW = "\033[93m"
        _RESET = "\033[0m"
        print(
            f"{_YELLOW}\n⚠  'sudo' not found in PATH. If kport was installed via pip or pipx,\n"
            f"   run:  kport setup-sudo   to create a system-wide launcher.\n"
            f"   Or re-run as:  sudo -E env PATH=\"$PATH\" kport ...{_RESET}",
            file=sys.stderr,
        )
        return False

    try:
        if debug:
            print(f"[debug] sudo kill -{sig_name} {pid}", file=sys.stderr)

        # When running in an interactive terminal, inherit stdin/stdout so sudo
        # can display its password prompt.  In non-interactive contexts (pipes,
        # CI, JSON mode) we capture output to avoid garbled output.
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if interactive:
            result = subprocess.run(
                [sudo, "kill", f"-{sig_name}", str(pid)],
                timeout=30,  # longer: user needs time to type password
                check=False,
            )
        else:
            result = subprocess.run(
                [sudo, "kill", f"-{sig_name}", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if debug and result.stderr:
                print(f"[debug] sudo stderr: {result.stderr.strip()}", file=sys.stderr)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _escalate_kill_windows(
    pid: int,
    assume_yes: bool,
    debug: bool = False,
    confirm_fn: Callable[[str], bool] | None = None,
) -> bool:
    """
    Attempt a privilege-escalated kill on Windows via UAC-elevated taskkill.
    Returns True if taskkill reports success.
    """
    prompt = (
        f"\nPID {pid} requires elevated privileges (Administrator) to terminate. "
        "Attempt UAC-elevated taskkill?"
    )
    if not assume_yes:
        if confirm_fn is None:
            return False
        if not confirm_fn(prompt):
            return False

    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return False

    script = (
        f"Start-Process taskkill "
        f"-ArgumentList '/PID {pid} /F' "
        f"-Verb RunAs -WindowStyle Hidden -Wait"
    )
    try:
        if debug:
            print(f"[debug] PowerShell UAC taskkill PID {pid}", file=sys.stderr)
        result = subprocess.run(
            [
                ps,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# ---------------------------------------------------------------------------
# Base inspector
# ---------------------------------------------------------------------------


class BaseInspector:
    def _clear_cache(self, ttl: float = 0.0) -> None:
        """Clear cached state. Subclasses may implement process metadata caching."""

    def list_listening(self, proto: str = "both") -> list[PortBinding]:
        """List all active listening ports."""
        raise NotImplementedError()

    def list_connections(self) -> list[ConnectionInfo]:
        """List all active network connections."""
        raise NotImplementedError()

    def find_pids_on_port(self, port: int, proto: str = "both") -> list[int]:
        """Find PIDs currently bound to a port."""
        raise NotImplementedError()

    def find_bindings_on_port(self, port: int, proto: str = "both") -> list[PortBinding]:
        """Find listening bindings for a specific port."""
        return [b for b in self.list_listening(proto=proto) if b.port == port]

    def find_bindings_for_pid(self, pid: int) -> list[PortBinding]:
        """Find all listening port bindings owned by *pid*.

        The default implementation filters ``list_listening(proto="both")`` by
        pid.  On Linux this will miss sockets belonging to root-owned or
        sandboxed processes when running unprivileged (the inode→PID map built
        from /proc/<pid>/fd/ is only readable by the owning user or root).
        Subclasses should override this to apply a per-process /proc/<pid>/net/
        fallback in that case.
        """
        return [b for b in self.list_listening(proto="both") if b.pid == pid]


    def get_process_info(self, pid: int) -> ProcessInfo | None:
        """Get details about a specific process PID."""
        raise NotImplementedError()

    def find_pids_by_name(self, name: str, exact: bool = False) -> list[int]:
        """Find PIDs matching a process name."""
        raise NotImplementedError()

    def find_ports_by_process_name(
        self, name: str, exact: bool = False, proto: str = "both"
    ) -> list[PortBinding]:
        """Find port bindings matching a process name."""
        raise NotImplementedError()

    def get_child_pids(self, pid: int) -> list[int]:
        """Return direct child PIDs of *pid* (best-effort, empty on failure).

        Subclasses should override this with a platform-native lookup
        (e.g. psutil.Process.children() or parsing /proc/<pid>/status).
        The base implementation always returns an empty list so subclasses that
        don't implement it still work correctly via kill_pid().
        """
        return []

    def kill_process_tree(
        self,
        pid: int,
        graceful_timeout: float = 3.0,
        force: bool = False,
        dry_run: bool = False,
        assume_yes: bool = False,
        debug: bool = False,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> tuple[bool, str]:
        """Terminate *pid* and all of its descendant processes.

        Kill order: depth-first (children before parent) to avoid orphaned
        zombies when the parent is killed before its children.

        Returns (all_ok, summary_message).
        """
        # Gather the full subtree before killing anything: once the root dies
        # the child list may change (children get re-parented to init/PID-1).
        children = self.get_child_pids(pid)

        killed = []
        failed = []

        for child_pid in children:
            ok, msg = self.kill_pid(
                child_pid,
                graceful_timeout=graceful_timeout,
                force=force,
                dry_run=dry_run,
                assume_yes=assume_yes,
                debug=debug,
                confirm_fn=confirm_fn,
            )
            (killed if ok else failed).append((child_pid, msg))

        # Now kill the root
        ok, msg = self.kill_pid(
            pid,
            graceful_timeout=graceful_timeout,
            force=force,
            dry_run=dry_run,
            assume_yes=assume_yes,
            debug=debug,
            confirm_fn=confirm_fn,
        )
        (killed if ok else failed).append((pid, msg))

        if not failed:
            n = len(killed)
            return True, f"Killed process tree: {n} process(es) terminated"
        failed_pids = [str(p) for p, _ in failed]
        return False, f"Failed to kill PIDs: {', '.join(failed_pids)}"

    def send_signal(self, pid: int, sig: int) -> bool:
        """Send a standard signal (TERM, KILL) to target PID."""
        raise NotImplementedError()

    def is_process_alive(self, pid: int) -> bool:
        """Check if process is active."""
        raise NotImplementedError()

    # ------------------------------------------------------------------
    # Escalated kill
    # ------------------------------------------------------------------

    def _try_escalate(
        self,
        pid: int,
        sig: int,
        assume_yes: bool,
        debug: bool = False,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> bool:
        """
        Attempt privilege-escalated termination.
        Returns True if escalation succeeded (process is gone), False otherwise.
        """
        if platform.system() == "Windows":
            ok = _escalate_kill_windows(pid, assume_yes, debug=debug, confirm_fn=confirm_fn)
        else:
            ok = _escalate_kill_unix(pid, sig, assume_yes, debug=debug, confirm_fn=confirm_fn)
        if ok:
            # Allow OS scheduler time to reap the process
            time.sleep(0.3)
            return not self.is_process_alive(pid)
        return False

    # ------------------------------------------------------------------
    # kill_pid
    # ------------------------------------------------------------------

    def kill_pid(
        self,
        pid: int,
        graceful_timeout: float = 3.0,
        force: bool = False,
        dry_run: bool = False,
        assume_yes: bool = False,
        debug: bool = False,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> tuple[bool, str]:
        """
        Attempt to terminate a process by PID.

        Kill escalation path:
          1. SIGTERM (graceful)
          2. Poll up to graceful_timeout
          3. If still alive and force/assume_yes → SIGKILL
          4. On PermissionError at any stage → attempt sudo/UAC escalation
        """
        if dry_run:
            return True, "Dry-run: would terminate process"

        # Stage 1: Graceful SIGTERM
        try:
            self.send_signal(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, "Process disappeared before signal"
        except PermissionError:
            # Attempt privilege escalation before giving up
            if debug:
                print(
                    f"[debug] PermissionError on SIGTERM for PID {pid}, attempting escalation",
                    file=sys.stderr,
                )
            if self._try_escalate(pid, signal.SIGTERM, assume_yes, debug=debug, confirm_fn=confirm_fn):
                return True, "Terminated via privilege escalation"
            return (
                False,
                "Permission denied — could not escalate. Try running with sudo/admin.",
            )
        except Exception as e:  # noqa: BLE001 - catch arbitrary exceptions during signal dispatch
            return False, f"SIGTERM error: {e}"

        # Stage 2: Wait & Poll
        start = time.time()
        while time.time() - start < graceful_timeout:
            time.sleep(0.1)
            if not self.is_process_alive(pid):
                return True, "Terminated gracefully"

        # Still alive after graceful timeout — decide whether to force kill
        if not force:
            try:
                # Escalated (sudo/UAC) kills are the most destructive action this tool can take.
                # We deliberately refuse to auto-escalate in non-interactive contexts unless the
                # operator has explicitly passed --yes (assume_yes=True). This prevents CI/agent
                # pipelines from silently gaining root-level kill capability without explicit opt-in.
                # Do not relax this check without a corresponding audit-log feature (see roadmap).
                if confirm_fn is not None:
                    info = self.get_process_info(pid)
                    pname = f" ({info.name})" if info else ""
                    if confirm_fn(
                        f"\nPID {pid}{pname} is still running after graceful timeout. Force kill?"
                    ):
                        force = True
                elif assume_yes:
                    force = True
            except (EOFError, OSError, ValueError):
                if assume_yes:
                    force = True

        if not force:
            return False, "Still running after graceful timeout"

        # Stage 3: Forceful SIGKILL
        try:
            self.send_signal(pid, SIGKILL)
            time.sleep(0.3)
            if not self.is_process_alive(pid):
                return True, "Killed (force)"
            return False, "Process ignored SIGKILL"
        except ProcessLookupError:
            return True, "Process disappeared"
        except PermissionError:
            if debug:
                print(
                    f"[debug] PermissionError on SIGKILL for PID {pid}, attempting escalation",
                    file=sys.stderr,
                )
            if self._try_escalate(pid, SIGKILL, assume_yes, debug=debug, confirm_fn=confirm_fn):
                return True, "Force-killed via privilege escalation"
            return False, "Permission denied on force kill — could not escalate."
        except Exception as e:  # noqa: BLE001 - catch arbitrary exceptions during signal dispatch
            return False, f"SIGKILL error: {e}"

    # ------------------------------------------------------------------
    # kill_port
    # ------------------------------------------------------------------

    def kill_port(
        self,
        port: int,
        graceful_timeout: float = 3.0,
        force: bool = False,
        dry_run: bool = False,
        debug: bool = False,
        assume_yes: bool = False,
        kill_tree: bool = False,
        proto: str = "both",
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> tuple[bool, str]:
        """
        Kill all processes using a specific port.

        Escalation path:
          1. Find PIDs on port (fetched once).
          2. kill_process_tree() or kill_pid() per PID.
          3. On Linux: fuser fallback if PIDs survive and force=True.
          4. Final socket verification.
        """
        if dry_run:
            return True, f"Dry-run: would terminate port {port}"

        # C4 fix: fetch PIDs once — consistent snapshot for the entire kill sequence
        pids = self.find_pids_on_port(port, proto=proto)
        if not pids:
            return True, "No process found on port"

        killed_count = 0
        errors: list[str] = []
        remaining_pids: list[int] = []

        for pid in pids:
            if kill_tree:
                ok, msg = self.kill_process_tree(
                    pid,
                    graceful_timeout=graceful_timeout,
                    force=force,
                    dry_run=dry_run,
                    assume_yes=assume_yes,
                    debug=debug,
                    confirm_fn=confirm_fn,
                )
            else:
                ok, msg = self.kill_pid(
                    pid,
                    graceful_timeout=graceful_timeout,
                    force=force,
                    dry_run=dry_run,
                    assume_yes=assume_yes,
                    debug=debug,
                    confirm_fn=confirm_fn,
                )
            if ok:
                killed_count += 1
            else:
                remaining_pids.append(pid)
                errors.append(f"PID {pid}: {msg}")

        # Linux fuser fallback — only when forced and fuser is available
        if (
            remaining_pids
            and platform.system() != "Windows"
            and force
            and shutil.which("fuser")
        ):
            fuser_protos = [proto] if proto in ("tcp", "udp") else ["tcp", "udp"]
            for _fuser_proto in fuser_protos:
                if not remaining_pids:
                    break
                if debug:
                    print(
                        f"[debug] PIDs {remaining_pids} survived standard signals. Triggering fuser fallback ({_fuser_proto})...",
                        file=sys.stderr,
                    )
                try:
                    subprocess.run(
                        ["fuser", "-k", f"{port}/{_fuser_proto}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    time.sleep(0.5)
                    remaining_pids = [p for p in remaining_pids if self.is_process_alive(p)]
                except (subprocess.SubprocessError, OSError) as e:
                    errors.append(f"fuser fallback failed ({_fuser_proto}): {e}")

        if not remaining_pids:
            return True, f"Killed {len(pids)} process(es)"

        # Final socket verification — the process may have died but the socket
        # might linger in TIME_WAIT; check what's actually still bound.
        actual_remaining = self.find_pids_on_port(port, proto=proto)
        if actual_remaining:
            return False, (
                f"Failed to free port. Remaining PIDs: {actual_remaining}. "
                f"Errors: {'; '.join(errors)}"
            )

        return True, "Port successfully freed"
