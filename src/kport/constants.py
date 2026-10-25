"""
Shared safety constants for kport.
Single source-of-truth for protected ports and process names,
used by both CLI and MCP server to keep policies in sync.
"""
from __future__ import annotations

# Ports that must never be killed by default.
# These are critical infrastructure sockets (SSH, DNS, HTTP, HTTPS, databases, k8s).
PROTECTED_PORTS: frozenset[int] = frozenset(
    {
        22,  # SSH
        53,  # DNS
        80,  # HTTP
        443,  # HTTPS
        3306,  # MySQL / MariaDB
        5432,  # PostgreSQL
        6379,  # Redis
        6443,  # Kubernetes API server
        2375,  # Docker daemon (unencrypted)
        2376,  # Docker daemon (TLS)
        27017,  # MongoDB
    }
)

# UDP-specific ports that are critical system infrastructure.
# These sockets are usually owned by the OS/kernel and must not be killed.
# Applied in addition to PROTECTED_PORTS when the binding protocol is UDP.
PROTECTED_UDP_PORTS: frozenset[int] = frozenset(
    {
        53,    # DNS
        67,    # DHCP server
        68,    # DHCP client
        69,    # TFTP
        123,   # NTP (time sync)
        137,   # NetBIOS Name Service
        138,   # NetBIOS Datagram Service
        161,   # SNMP
        162,   # SNMP trap
        500,   # IKE / IPSec VPN
        514,   # Syslog
        520,   # RIP routing
        1900,  # UPnP / SSDP
        4500,  # IKE NAT traversal
        5353,  # mDNS (Bonjour/Avahi)
        5355,  # LLMNR (Windows name resolution)
    }
)

# Critical system process names that must never be targeted.
# Comparison is always case-insensitive.
PROTECTED_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "systemd",
        "init",
        "docker",
        "dockerd",
        "containerd",
        "sshd",
        "cron",
        "rsyslogd",
        "dbus-daemon",
        "explorer.exe",
        "lsass.exe",
        "services.exe",
        "wininit.exe",
        "winlogon.exe",
        "csrss.exe",
        "smss.exe",
    }
)

# Generic runtime process names whose display can be enriched
# by looking up their first meaningful CLI argument (script/jar/module name).
RUNTIME_ENRICHMENT_NAMES: frozenset[str] = frozenset(
    {
        "node",
        "nodejs",
        "node.exe",
        "python",
        "python3",
        "python2",
        "python.exe",
        "python3.exe",
        "java",
        "java.exe",
        "ruby",
        "ruby.exe",
        "php",
        "php-fpm",
        "php.exe",
        "bun",
        "bun.exe",
        "deno",
        "deno.exe",
        "perl",
        "perl.exe",
        "go",
        "go.exe",
        "cargo",
        "dotnet",
        "dotnet.exe",
        "tsx",
        "ts-node",
    }
)
