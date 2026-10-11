import json
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.cli

from kport.cli import EXIT_OK, EXIT_PORT_DOCKER, EXIT_PORT_FREE, handle_product_command
from kport.docker_engine import DockerPortMapping
from kport.inspectors.base import PortBinding, ProcessInfo
from tests.conftest import FakeInspector, _args


def _binding(port: int, pid: int = 1234, name: str = "node", laddr: str = "127.0.0.1") -> PortBinding:
    return PortBinding(
        port=port,
        family="inet",
        laddr=f"{laddr}:{port}",
        pid=pid,
        process_name=name,
        state="LISTEN",
    )

def test_diagnose_free_port(capsys):
    """diagnose on a free port should return free type and exit code free."""
    inspector = FakeInspector()
    args = _args(command="diagnose", port=8080, json=True)
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    
    assert rc == EXIT_PORT_FREE
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema_version"] == 1
    assert envelope["command"] == "diagnose"
    data = envelope["data"]
    assert data["blocked"] is False
    assert data["observations"]["type"] == "free"
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["action"] == "bind"

def test_diagnose_local_process(capsys):
    """diagnose on a local process port should return observations, no inferences by default, and kill recommendation."""
    pi = ProcessInfo(pid=1234, name="node", exe="/usr/bin/node", cmdline=["node", "app.js"], user="ubuntu")
    inspector = FakeInspector(
        pids_on_port={8080: [1234]},
        process_info={1234: pi},
        bindings_on_port={8080: [_binding(8080)]}
    )
    args = _args(command="diagnose", port=8080, json=True)
    
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
         patch("kport.diagnostics.detect_process_manager", return_value=None):
        rc = handle_product_command(args, inspector)

    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    assert data["blocked"] is True
    assert data["observations"]["type"] == "local"
    assert len(data["observations"]["processes"]) == 1
    proc = data["observations"]["processes"][0]
    assert proc["pid"] == 1234
    assert proc["name"] == "node (app.js)"
    assert proc["exe"] == "/usr/bin/node"
    assert data["inferences"] == []
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["action"] == "kill_processes"
    assert data["recommendations"][0]["command"] == "kport kill 8080"

def test_diagnose_systemd_managed(capsys):
    """diagnose on a managed process should infer the manager and recommend stopping via manager."""
    pi = ProcessInfo(pid=1234, name="nginx")
    inspector = FakeInspector(
        pids_on_port={80: [1234]},
        process_info={1234: pi},
        bindings_on_port={80: [_binding(80)]}
    )
    # safety check needs bypass since port 80 is protected
    args = _args(command="diagnose", port=80, json=True, bypass_safety=True)
    
    pm_info = {
        "manager": "systemd",
        "name": "nginx.service",
        "managed_by": "systemd:nginx.service",
        "warning": "warning text"
    }

    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
         patch("kport.diagnostics.detect_process_manager", return_value=pm_info):
        rc = handle_product_command(args, inspector)

    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    
    # Verify inference exists
    assert len(data["inferences"]) == 1
    inf = data["inferences"][0]
    assert inf["type"] == "process_manager"
    assert inf["manager"] == "systemd"
    assert inf["name"] == "nginx.service"
    
    # Verify risks contain auto_restart warning
    auto_restart_risks = [r for r in data["risks"] if r["type"] == "auto_restart"]
    assert len(auto_restart_risks) == 1
    
    # Verify recommendation action is stop_service
    assert len(data["recommendations"]) == 1
    rec = data["recommendations"][0]
    assert rec["action"] == "stop_service"
    assert rec["command"] == "systemctl stop nginx.service"

def test_diagnose_docker_port(capsys):
    """diagnose on a docker-mapped port should identify container and recommend stopping it."""
    inspector = FakeInspector()
    args = _args(command="diagnose", port=8080, json=True)
    
    # Mock docker mapping hit
    mock_mapping = DockerPortMapping(
        container_id="abc123containerid",
        container_name="test-db",
        image="postgres:latest",
        status="running",
        host_ip="0.0.0.0",
        host_port=8080,
        container_port=5432,
        proto="tcp"
    )
    
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[mock_mapping]):
        rc = handle_product_command(args, inspector)

    assert rc == EXIT_PORT_DOCKER
    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    
    assert data["observations"]["type"] == "docker"
    assert len(data["observations"]["docker_containers"]) == 1
    cont = data["observations"]["docker_containers"][0]
    assert cont["container_name"] == "test-db"
    
    assert len(data["inferences"]) == 1
    assert data["inferences"][0]["type"] == "docker_isolation"
    
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["action"] == "stop_docker_container"
    assert data["recommendations"][0]["command"] == "kport kill 8080 --docker-action stop"


def test_diagnose_protected_port(capsys):
    """diagnose on a protected port without bypass should recommend aborting."""
    pi = ProcessInfo(pid=1234, name="sshd")
    inspector = FakeInspector(
        pids_on_port={22: [1234]},
        process_info={1234: pi},
        bindings_on_port={22: [_binding(22, pid=1234, name="sshd")]}
    )
    args = _args(command="diagnose", port=22, json=True, bypass_safety=False) # 22 is SSH (protected)
    
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)

    assert rc == EXIT_OK # port is occupied, exit code is EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    
    protected_port_risks = [r for r in data["risks"] if r["type"] == "protected_port"]
    assert len(protected_port_risks) == 1
    
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["action"] == "abort"
    assert "Safety Shield Active" in data["recommendations"][0]["reason"]
    assert data["recommendations"][0]["safe"] is False

def test_diagnose_public_bind_risk(capsys):
    """diagnose on wildcard bound address should log public exposure risk."""
    pi = ProcessInfo(pid=1234, name="node")
    inspector = FakeInspector(
        pids_on_port={8080: [1234]},
        process_info={1234: pi},
        bindings_on_port={8080: [_binding(8080, laddr="0.0.0.0")]}
    )
    args = _args(command="diagnose", port=8080, json=True)
    
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
         patch("kport.diagnostics.detect_process_manager", return_value=None):
        rc = handle_product_command(args, inspector)

    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    
    exposure_risks = [r for r in data["risks"] if r["type"] == "public_exposure"]
    assert len(exposure_risks) == 1
    assert "bound to wildcard address" in exposure_risks[0]["message"]

def test_diagnose_observation_not_inference(capsys):
    """Verify observations block only contains direct facts and no inferences."""
    pi = ProcessInfo(pid=1234, name="node")
    inspector = FakeInspector(
        pids_on_port={8080: [1234]},
        process_info={1234: pi},
        bindings_on_port={8080: [_binding(8080)]}
    )
    args = _args(command="diagnose", port=8080, json=True)
    
    pm_info = {
        "manager": "pm2",
        "name": "app",
        "managed_by": "pm2:app",
        "warning": "warning"
    }

    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
         patch("kport.diagnostics.detect_process_manager", return_value=pm_info):
        handle_product_command(args, inspector)

    envelope = json.loads(capsys.readouterr().out)
    data = envelope["data"]
    
    # Observation must not contain process manager info
    assert "pm2" not in json.dumps(data["observations"]).lower()
    
    # Inference must contain process manager info
    assert "pm2" in json.dumps(data["inferences"]).lower()
