"""
Kubernetes & Cloud Runtime proxy detection for kport.
Detects kubectl port-forward, minikube, kind, and telepresence tunnels/proxies.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class K8sProxyInfo:
    tool: str  # "kubectl", "oc", "minikube", "telepresence", "kind"
    proxy_type: str  # "port-forward", "tunnel", "intercept", "cluster"
    resource: str | None = None  # e.g. "svc/billing-api", "pod/backend-xyz"
    namespace: str | None = None  # e.g. "production", "default"
    remote_port: int | None = None  # e.g. 80
    local_port: int | None = None  # e.g. 8080
    summary: str = ""


def detect_k8s_proxy(
    cmdline: list[str] | None, process_name: str | None = None
) -> K8sProxyInfo | None:
    """
    Analyze process cmdline and name to detect Kubernetes port forwarding or cloud proxy.
    Returns K8sProxyInfo if detected, None otherwise.
    """
    if not cmdline and not process_name:
        return None

    cmd_parts = [arg.strip() for arg in (cmdline or []) if arg.strip()]
    proc_name = (
        process_name or (os.path.basename(cmd_parts[0]) if cmd_parts else "")
    ).lower()
    if proc_name.endswith(".exe"):
        proc_name = proc_name[:-4]

    cmd_str = " ".join(cmd_parts).lower()

    # 1. kubectl / oc port-forward
    if proc_name in ("kubectl", "oc") or "port-forward" in cmd_str:
        # Check if port-forward subcommand is present
        pf_idx = -1
        for idx, arg in enumerate(cmd_parts):
            if arg.lower() == "port-forward":
                pf_idx = idx
                break

        if pf_idx != -1 or "port-forward" in cmd_str:
            tool = "oc" if proc_name == "oc" else "kubectl"
            resource = None
            namespace = None
            local_port = None
            remote_port = None

            # Look for namespace flag: -n <ns> or --namespace <ns> or --namespace=<ns>
            i = 0
            while i < len(cmd_parts):
                arg = cmd_parts[i]
                if arg in ("-n", "--namespace") and i + 1 < len(cmd_parts):
                    namespace = cmd_parts[i + 1]
                    i += 2
                    continue
                if arg.startswith("--namespace="):
                    namespace = arg.split("=", 1)[1]
                    i += 1
                    continue
                i += 1

            # Walk args after 'port-forward'
            args_after_pf = cmd_parts[pf_idx + 1 :] if pf_idx != -1 else cmd_parts[1:]
            for arg in args_after_pf:
                if arg.startswith("-"):
                    continue
                # Resource target: e.g. svc/name, service/name, pod/name, deployment/name
                if any(
                    arg.startswith(prefix)
                    for prefix in (
                        "svc/",
                        "service/",
                        "pod/",
                        "deploy/",
                        "deployment/",
                        "statefulset/",
                        "sts/",
                    )
                ):
                    resource = arg
                elif ":" in arg:
                    # Port specification: [local_port:]remote_port
                    m = re.match(r"^(\d+):(\d+)$", arg)
                    if m:
                        local_port = int(m.group(1))
                        remote_port = int(m.group(2))
                elif arg.isdigit() and local_port is None:
                    # Single port e.g. 8080 (same for local and remote)
                    local_port = int(arg)
                    remote_port = int(arg)
                elif resource is None and not arg.startswith("-"):
                    resource = arg

            ns_suffix = f" (ns: {namespace})" if namespace else ""
            res_str = resource or "remote target"
            port_str = f":{remote_port}" if remote_port else ""
            summary = f"port-forward -> {res_str}{port_str}{ns_suffix}"

            return K8sProxyInfo(
                tool=tool,
                proxy_type="port-forward",
                resource=resource,
                namespace=namespace,
                remote_port=remote_port,
                local_port=local_port,
                summary=summary,
            )

    # 2. minikube tunnel / service
    if "minikube" in proc_name or "minikube" in cmd_str:
        if "tunnel" in cmd_str:
            return K8sProxyInfo(
                tool="minikube",
                proxy_type="tunnel",
                summary="tunnel (LoadBalancer proxy)",
            )
        if "service" in cmd_str:
            service_name = None
            for idx, arg in enumerate(cmd_parts):
                if arg.lower() == "service" and idx + 1 < len(cmd_parts):
                    service_name = cmd_parts[idx + 1]
                    break
            summary = f"service -> {service_name or 'service'}"
            return K8sProxyInfo(
                tool="minikube",
                proxy_type="tunnel",
                resource=service_name,
                summary=summary,
            )

    # 3. telepresence
    if "telepresence" in proc_name or "telepresence" in cmd_str:
        intercept = None
        for idx, arg in enumerate(cmd_parts):
            if arg.lower() == "intercept" and idx + 1 < len(cmd_parts):
                intercept = cmd_parts[idx + 1]
                break
        summary = (
            f"intercept -> {intercept}" if intercept else "cloud proxy"
        )
        return K8sProxyInfo(
            tool="telepresence",
            proxy_type="intercept",
            resource=intercept,
            summary=summary,
        )

    # 4. kind
    if "kind-control-plane" in cmd_str or "kind" in proc_name:
        return K8sProxyInfo(
            tool="kind",
            proxy_type="cluster",
            summary="node proxy (kind)",
        )

    return None
