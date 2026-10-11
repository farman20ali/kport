"""
Inspectors package for kport.

Inspector Selection Order
-------------------------
``get_inspector()`` resolves the best available backend at runtime using a
two-level priority:

1. **PsutilInspector** (``psutil_impl.py``) — preferred when ``psutil`` is
   installed *and* can actually call ``psutil.net_connections(kind="inet")``
   without raising ``PermissionError`` or ``psutil.AccessDenied``.

   psutil wraps native OS APIs per-platform:
   - Linux   → reads ``/proc/net/tcp*`` / ``/proc/<pid>/fd`` (same as FallbackInspector)
   - macOS   → uses ``libproc``/``proc_pidinfo`` syscalls (avoids ``lsof`` shell-out)
   - Windows → calls ``GetExtendedTcpTable``/``GetExtendedUdpTable`` via ctypes
               (same as FallbackInspector on Windows)

   Use-case: gives richer per-process metadata (exe path, username, full
   cmdline) and is the preferred path on macOS where the fallback requires
   shelling out to ``lsof``.

2. **FallbackInspector** (``system_impl.py``) — used when psutil is absent or
   inaccessible (e.g. inside a snap with restricted AppArmor policy).

   Reads OS data directly without psutil:
   - Linux   → parses ``/proc/net/tcp``, ``/proc/net/tcp6``, ``/proc/net/udp``
               and ``/proc/<pid>/cmdline`` natively (no shell-out, no lsof)
   - Windows → ctypes calls to ``iphlpapi.dll`` (``GetExtendedTcpTable`` etc.)
   - macOS   → shells out to ``lsof -nP -iTCP -iUDP -sTCP:LISTEN`` as a last
               resort (macOS lacks a stable ``/proc`` tree).

   macOS roadmap note: if psutil is available (it is a hard dependency in
   pyproject.toml since v3.2.5), the lsof shell-out is bypassed automatically.
   A native ``libproc``/ctypes macOS path would remove the psutil dependency
   entirely — tracked in Phase 3.2 of the improvement plan.

Unit-test contract
------------------
To verify both paths are exercised, mock ``_psutil_accessible`` to return
``False`` and assert that ``get_inspector()`` returns a ``FallbackInspector``.
"""

from .base import BaseInspector, ConnectionInfo, PortBinding, ProcessInfo

__all__ = [
    "BaseInspector",
    "ConnectionInfo",
    "PortBinding",
    "ProcessInfo",
    "get_inspector",
]

import importlib.util as _ilu

try:
    USING_PSUTIL = _ilu.find_spec("psutil") is not None
except (ImportError, ValueError):
    USING_PSUTIL = False


def _psutil_accessible() -> bool:
    """
    Return True only if psutil can actually enumerate network connections on
    this host.  On Linux, psutil.net_connections() requires access to
    /proc/net/tcp* and per-process /proc/<pid>/fd/ entries.  Inside a
    strictly-confined snap whose network-observe / system-observe plugs are
    not yet connected, this raises PermissionError (raw) or
    psutil.AccessDenied — even when the caller is root, because AppArmor
    applies regardless of UID in strict-confinement mode.
    """
    if not USING_PSUTIL:
        return False
    try:
        import psutil as _p

        _p.net_connections(kind="inet")
        return True
    except PermissionError:
        return False
    except Exception as exc:  # noqa: BLE001 - catch arbitrary exceptions during connection query
        # psutil.AccessDenied is NOT a subclass of PermissionError; catch it too.
        try:
            import psutil as _p2

            if isinstance(exc, _p2.AccessDenied):
                return False
        except Exception:  # noqa: BLE001, S110
            pass
        # Any other unexpected psutil failure → be safe and fall back.
        return False


def get_inspector() -> BaseInspector:
    """Resolve and return the appropriate inspector instance for the host system."""
    if _psutil_accessible():
        from .psutil_impl import PsutilInspector

        return PsutilInspector()
    from .system_impl import FallbackInspector

    return FallbackInspector()
