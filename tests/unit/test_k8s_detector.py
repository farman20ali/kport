"""
Unit tests for k8s_detector module.
"""
from __future__ import annotations

from kport.k8s_detector import K8sProxyInfo, detect_k8s_proxy


class TestK8sDetector:
    def test_empty_or_none(self):
        assert detect_k8s_proxy(None, None) is None
        assert detect_k8s_proxy([], "") is None

    def test_kubectl_port_forward_service(self):
        cmdline = ["kubectl", "port-forward", "svc/billing-api", "8080:80", "-n", "production"]
        info = detect_k8s_proxy(cmdline, "kubectl")
        assert info is not None
        assert isinstance(info, K8sProxyInfo)
        assert info.tool == "kubectl"
        assert info.proxy_type == "port-forward"
        assert info.resource == "svc/billing-api"
        assert info.namespace == "production"
        assert info.local_port == 8080
        assert info.remote_port == 80
        assert "port-forward -> svc/billing-api:80 (ns: production)" in info.summary

    def test_kubectl_port_forward_pod_long_namespace(self):
        cmdline = [
            "kubectl.exe",
            "port-forward",
            "pod/my-redis-0",
            "6379:6379",
            "--namespace=cache-tier",
        ]
        info = detect_k8s_proxy(cmdline, "kubectl.exe")
        assert info is not None
        assert info.tool == "kubectl"
        assert info.resource == "pod/my-redis-0"
        assert info.namespace == "cache-tier"
        assert info.local_port == 6379
        assert info.remote_port == 6379

    def test_kubectl_single_port(self):
        cmdline = ["kubectl", "port-forward", "deployment/web", "3000"]
        info = detect_k8s_proxy(cmdline, "kubectl")
        assert info is not None
        assert info.resource == "deployment/web"
        assert info.local_port == 3000
        assert info.remote_port == 3000
        assert info.namespace is None

    def test_oc_port_forward(self):
        cmdline = ["oc", "port-forward", "svc/auth", "4000:4000", "-n", "security"]
        info = detect_k8s_proxy(cmdline, "oc")
        assert info is not None
        assert info.tool == "oc"
        assert info.resource == "svc/auth"
        assert info.namespace == "security"

    def test_minikube_tunnel(self):
        cmdline = ["minikube", "tunnel"]
        info = detect_k8s_proxy(cmdline, "minikube")
        assert info is not None
        assert info.tool == "minikube"
        assert info.proxy_type == "tunnel"
        assert "tunnel" in info.summary

    def test_minikube_service(self):
        cmdline = ["minikube", "service", "my-frontend"]
        info = detect_k8s_proxy(cmdline, "minikube")
        assert info is not None
        assert info.tool == "minikube"
        assert info.resource == "my-frontend"
        assert "service -> my-frontend" in info.summary

    def test_telepresence_intercept(self):
        cmdline = ["telepresence", "intercept", "order-service", "--port", "9090"]
        info = detect_k8s_proxy(cmdline, "telepresence")
        assert info is not None
        assert info.tool == "telepresence"
        assert info.proxy_type == "intercept"
        assert info.resource == "order-service"
        assert "intercept -> order-service" in info.summary

    def test_kind_node(self):
        cmdline = ["kind-control-plane"]
        info = detect_k8s_proxy(cmdline, "kind")
        assert info is not None
        assert info.tool == "kind"
        assert info.proxy_type == "cluster"

    def test_unrelated_process_returns_none(self):
        cmdline = ["python", "-m", "http.server", "8000"]
        assert detect_k8s_proxy(cmdline, "python") is None
