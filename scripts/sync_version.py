#!/usr/bin/env python3
"""Synchronize kport's release version across package metadata and app code."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[A-Za-z0-9._-]+)?$")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected one version match in {path}")
    path.write_text(updated, encoding="utf-8")


def sync_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ValueError("Version must look like 3.2.1 or 3.2.1rc1")

    replace_once(
        ROOT / "pyproject.toml",
        r'^(version\s*=\s*)["\'][^"\']+["\']',
        rf'\1"{version}"',
    )
    replace_once(
        ROOT / "src" / "kport" / "__init__.py",
        r'^(__version__\s*=\s*)["\'][^"\']+["\']',
        rf'\1"{version}"',
    )

    vscode_pkg = ROOT / "vscode-extension" / "package.json"
    if vscode_pkg.exists():
        replace_once(
            vscode_pkg,
            r'^(\s*"version"\s*:\s*)["\'][^"\']+["\']',
            rf'\1"{version}"',
        )

    win_version = ROOT / "packaging" / "windows" / "version.txt"
    if win_version.exists():
        # e.g., "4.0.0" -> "4, 0, 0, 0" and "4.0.0.0"
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
        if m:
            major, minor, patch = m.groups()
            tuple_str = f"({major}, {minor}, {patch}, 0)"
            dotted_str = f"{major}.{minor}.{patch}.0"
            replace_once(
                win_version,
                r'(filevers=)\([^)]+\)',
                f'\\g<1>{tuple_str}',
            )
            replace_once(
                win_version,
                r'(prodvers=)\([^)]+\)',
                f'\\g<1>{tuple_str}',
            )
            replace_once(
                win_version,
                r"(\s*StringStruct\(u'FileVersion',\s*u')[^']+'\),",
                f"\\g<1>{dotted_str}'),",
            )
            replace_once(
                win_version,
                r"(\s*StringStruct\(u'ProductVersion',\s*u')[^']+'\),",
                f"\\g<1>{dotted_str}'),",
            )

    # NSIS installer fallback version constant
    win_nsi = ROOT / "packaging" / "windows" / "installer.nsi"
    if win_nsi.exists():
        replace_once(
            win_nsi,
            r'(!define VERSION\s+")[^"]+(")',
            rf'\g<1>{version}\g<2>',
        )

    # macOS productbuild distribution manifest
    macos_dist = ROOT / "packaging" / "macos" / "distribution.xml"
    if macos_dist.exists():
        replace_once(
            macos_dist,
            r'(<pkg-ref[^>]+\bversion=")[^"]+(")',
            rf'\g<1>{version}\g<2>',
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync kport version metadata")
    parser.add_argument("version", help="Version to write, for example 3.2.1")
    args = parser.parse_args()

    sync_version(args.version)
    print(f"Synced kport version to {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
