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


def test_enrich_process_name_windows_exe_wrapper():
    """Verify enrich_process_name detects pip console entrypoint .exe wrappers."""
    from kport.inspectors.base import enrich_process_name

    cmdline = ["python.exe", r"C:\Users\HP\.vfox\sdks\python\Scripts\kport.exe", "hold", "5000"]
    enriched = enrich_process_name("python.exe", cmdline)
    assert enriched == "python.exe (kport)"


def test_enrich_process_name_unix_extensionless_wrapper():
    """Verify enrich_process_name detects Unix extensionless script entrypoints."""
    from kport.inspectors.base import enrich_process_name

    cmdline = ["/usr/bin/python3", "/usr/local/bin/kport", "hold", "5000"]
    enriched = enrich_process_name("python3", cmdline)
    assert enriched == "python3 (kport)"


def test_matches_process_name_exact_and_substring():
    """Verify matches_process_name matches process names cleanly."""
    from kport.inspectors.base import matches_process_name

    assert matches_process_name("code.exe", None, "code", exact=True)
    assert matches_process_name("Code.exe", None, "code", exact=False)
    assert matches_process_name("kport.exe", None, "kport", exact=True)
    assert not matches_process_name("code.exe", None, "kport", exact=False)


def test_matches_process_name_runtime_script():
    """Verify matches_process_name matches script module/entrypoint for runtimes."""
    from kport.inspectors.base import matches_process_name

    cmd_kport = ["python.exe", r"C:\Python\Scripts\kport.exe", "hold", "5000"]
    assert matches_process_name("python.exe", cmd_kport, "kport", exact=True)
    assert matches_process_name("python.exe", cmd_kport, "kport", exact=False)

    cmd_module = ["python", "-m", "kport", "watch"]
    assert matches_process_name("python", cmd_module, "kport", exact=True)

    cmd_node = ["node", "/path/to/server.js"]
    assert matches_process_name("node", cmd_node, "server", exact=True)


def test_matches_process_name_does_not_match_arbitrary_cli_paths():
    """
    CRITICAL SECURITY & ACCURACY TEST:
    Verify that unrelated processes (like language servers or IDEs) whose cmdline
    merely contains a directory path with 'kport' DO NOT match.
    """
    from kport.inspectors.base import matches_process_name

    lsp_cmd = [
        r"C:\Extensions\language_server_windows_x64.exe",
        "--workspace",
        r"d:\workspace\dev\kport",
    ]
    assert not matches_process_name("language_server_windows_x64.exe", lsp_cmd, "kport", exact=False)

    ide_cmd = [
        r"C:\Program Files\Antigravity IDE\Antigravity IDE.exe",
        "--folder-uri",
        r"file:///d:/workspace/dev/kport",
    ]
    assert not matches_process_name("Antigravity IDE.exe", ide_cmd, "kport", exact=False)
