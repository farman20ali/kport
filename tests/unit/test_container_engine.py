"""
Unit tests for container_engine module.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from kport.container_engine import (
    ContainerPortMapping,
    _query_engine_mappings,
    container_action_on_container,
    container_engine_available,
    container_mappings_for_host_port,
    container_mappings_for_host_ports,
    docker_available,
    list_container_mappings,
    podman_available,
)


class TestContainerEngine:
    def test_container_port_mapping_fields(self):
        m = ContainerPortMapping(
            container_id="abcdef123456",
            container_name="web-frontend",
            image="nginx:alpine",
            status="Up 2 hours",
            host_ip="0.0.0.0",
            host_port=8080,
            container_port=80,
            proto="tcp",
            engine="docker",
            compose_project="ecommerce",
            compose_service="web",
            networks=["frontend_net"],
        )
        assert m.host_port == 8080
        assert m.compose_project == "ecommerce"
        assert m.compose_service == "web"
        assert m.engine == "docker"
        assert "frontend_net" in m.networks

    def test_query_engine_mappings_parsing(self):
        # Format: {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Labels}}\t{{.Networks}}
        sample_ps = (
            "c101\tweb-app\tmy-app:1.0\tUp 5 mins\t0.0.0.0:3000->3000/tcp\tcom.docker.compose.project=shop,com.docker.compose.service=web\tbridge\n"
            "c102\tdb\tpostgres:15\tUp 10 mins\t127.0.0.1:5432->5432/tcp\tcom.docker.compose.project=shop,com.docker.compose.service=db\tbridge\n"
        )
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = sample_ps
        proc.stderr = ""

        with patch("kport.container_engine._run_container_cli", return_value=proc):
            mappings = _query_engine_mappings("docker")

        assert len(mappings) == 2
        assert mappings[0].host_port == 3000
        assert mappings[0].compose_project == "shop"
        assert mappings[0].compose_service == "web"
        assert mappings[0].container_name == "web-app (shop/web)"
        assert mappings[0].networks == ["bridge"]

        assert mappings[1].host_port == 5432
        assert mappings[1].compose_project == "shop"
        assert mappings[1].compose_service == "db"

    def test_podman_labels_parsing(self):
        sample_ps = (
            "p201\tapi-service\tapi:v2\tUp 1 hour\t0.0.0.0:8000->8000/tcp\tio.podman.compose.project=backend,io.podman.compose.service=api\tpodman\n"
        )
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = sample_ps
        proc.stderr = ""

        with patch("kport.container_engine._run_container_cli", return_value=proc):
            mappings = _query_engine_mappings("podman")

        assert len(mappings) == 1
        assert mappings[0].engine == "podman"
        assert mappings[0].compose_project == "backend"
        assert mappings[0].compose_service == "api"
        assert mappings[0].container_name == "api-service (backend/api)"

    def test_list_container_mappings_filters(self):
        m1 = ContainerPortMapping(
            container_id="c1",
            container_name="web",
            image="img",
            status="Up",
            host_ip="0.0.0.0",
            host_port=3000,
            container_port=3000,
            proto="tcp",
            engine="docker",
            compose_project="frontend",
            compose_service="web",
        )
        m2 = ContainerPortMapping(
            container_id="c2",
            container_name="api",
            image="img",
            status="Up",
            host_ip="0.0.0.0",
            host_port=4000,
            container_port=4000,
            proto="tcp",
            engine="docker",
            compose_project="backend",
            compose_service="api",
        )

        with patch("kport.container_engine._query_engine_mappings", return_value=[m1, m2]), \
             patch("kport.container_engine.docker_available", return_value=True), \
             patch("kport.container_engine.podman_available", return_value=False):
            
            # No filter
            all_m = list_container_mappings()
            assert len(all_m) == 2

            # Project filter
            fe = list_container_mappings(compose_project="frontend")
            assert len(fe) == 1
            assert fe[0].host_port == 3000

            # Service filter
            be = list_container_mappings(compose_service="api")
            assert len(be) == 1
            assert be[0].host_port == 4000

    def test_container_mappings_for_host_ports(self):
        m1 = ContainerPortMapping(
            container_id="c1",
            container_name="web",
            image="img",
            status="Up",
            host_ip="0.0.0.0",
            host_port=3000,
            container_port=3000,
            proto="tcp",
        )
        with patch("kport.container_engine.list_container_mappings", return_value=[m1]):
            res = container_mappings_for_host_ports([3000, 8080])
            assert 3000 in res
            assert 8080 in res
            assert len(res[3000]) == 1
            assert len(res[8080]) == 0

            single = container_mappings_for_host_port(3000)
            assert len(single) == 1

    def test_container_action_on_container(self):
        # Dry run
        ok, msg = container_action_on_container("c123456789012", "stop", dry_run=True, engine="docker")
        assert ok is True
        assert "Dry-run: would docker stop" in msg

        # Real action success
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "c123456789012\n"
        proc.stderr = ""
        with patch("kport.container_engine._run_container_cli", return_value=proc):
            ok, msg = container_action_on_container("c123456789012", "restart", dry_run=False, engine="podman")
            assert ok is True
            assert "podman restart succeeded" in msg

    def test_availability_helpers(self):
        with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "podman" else None):
            assert podman_available() is True
            assert docker_available() is False
            assert container_engine_available() is True
