import os
import sys
from unittest.mock import MagicMock, patch

from kport.inspectors.base import ProcessInfo
from kport.inspectors.psutil_impl import PsutilInspector
from kport.inspectors.system_impl import FallbackInspector


def test_process_info_default_fields():
    """Verify ProcessInfo optional fields default to None."""
    info = ProcessInfo(pid=99999, name="test-proc")
    assert info.pid == 99999
    assert info.name == "test-proc"
    assert info.exe is None
    assert info.cmdline is None
    assert info.user is None
    assert info.ppid is None
    assert info.cwd is None
    assert info.start_time is None


def test_psutil_inspector_enrichment():
    """Verify PsutilInspector retrieves ppid, cwd, and start_time for the current process."""
    import psutil

    inspector = PsutilInspector()
    current_pid = os.getpid()

    if getattr(psutil, "_is_stub", False) or isinstance(getattr(psutil, "Process", None), MagicMock):
        mock_proc = MagicMock()
        mock_proc.pid = current_pid
        mock_proc.name.return_value = "python"
        mock_proc.exe.return_value = sys.executable
        mock_proc.cmdline.return_value = [sys.executable]
        mock_proc.username.return_value = "user"
        mock_proc.ppid.return_value = os.getppid()
        mock_proc.cwd.return_value = os.getcwd()
        mock_proc.create_time.return_value = 1700000000.0
        with patch("psutil.Process", return_value=mock_proc):
            info = inspector.get_process_info(current_pid)
    else:
        info = inspector.get_process_info(current_pid)

    assert info is not None
    assert info.pid == current_pid
    assert info.ppid == os.getppid()
    # Normalize path strings for Windows/UNIX comparison
    assert os.path.normpath(info.cwd) == os.path.normpath(os.getcwd())
    assert isinstance(info.start_time, float)
    assert info.start_time > 0


def test_fallback_inspector_enrichment():
    """Verify FallbackInspector retrieves ppid, cwd, and start_time for the current process."""
    inspector = FallbackInspector()
    current_pid = os.getpid()
    
    info = inspector.get_process_info(current_pid)
    assert info is not None
    assert info.pid == current_pid
    assert info.ppid == os.getppid()
    
    # CWD is supported on Linux, macOS, and psutil-backed Windows
    if sys.platform != "win32":
        assert os.path.normpath(info.cwd) == os.path.normpath(os.getcwd())
        
    assert isinstance(info.start_time, float)
    assert info.start_time > 0


def test_psutil_inspector_permission_denied_graceful():
    """Verify that psutil lookup errors are handled gracefully on per-field basis."""
    import psutil
    
    # Mock Process methods to raise AccessDenied
    mock_proc = MagicMock()
    mock_proc.name.return_value = "restricted-app"
    mock_proc.exe.side_effect = psutil.AccessDenied()
    mock_proc.cmdline.side_effect = psutil.AccessDenied()
    mock_proc.username.side_effect = psutil.AccessDenied()
    mock_proc.ppid.side_effect = psutil.AccessDenied()
    mock_proc.cwd.side_effect = psutil.AccessDenied()
    mock_proc.create_time.side_effect = psutil.AccessDenied()
    
    with patch("psutil.Process", return_value=mock_proc):
        inspector = PsutilInspector()
        info = inspector.get_process_info(12345)
        
        assert info is not None
        assert info.pid == 12345
        assert info.name == "restricted-app"
        assert info.exe is None
        assert info.cmdline is None
        assert info.user is None
        assert info.ppid is None
        assert info.cwd is None
        assert info.start_time is None


def test_fallback_inspector_permission_denied_graceful():
    """Verify that fallback lookup errors are handled gracefully and don't raise exceptions."""
    inspector = FallbackInspector()
    
    # If the process files are missing or read-only/permission error
    with patch("builtins.open", side_effect=PermissionError("Permission Denied")), \
         patch("os.readlink", side_effect=PermissionError("Permission Denied")):
        info = inspector.get_process_info(99999)
        # Even if files are inaccessible, we return None (process unreadable or gone)
        assert info is None
