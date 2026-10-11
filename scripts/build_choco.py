#!/usr/bin/env python3
"""Chocolatey package builder for kport.

Builds a Chocolatey .nupkg that downloads the Windows NSIS installer from
the official GitHub release (no binary embedded in the package).

Usage (automated / CI):
    python choco_build.py --build
    python choco_build.py --build --installer dist/win/kport-3.2.4-setup.exe
    python choco_build.py --build --checksum ABC123...
    python choco_build.py --check

Output:
    dist/choco/kport.{version}.nupkg
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except OSError:
                pass

    extra_paths = [
        r"C:\Program Files (x86)\NSIS",
        r"C:\Program Files\NSIS",
        r"C:\ProgramData\chocolatey\bin",
    ]
    path_env = os.environ.get("PATH", "")
    paths = [p.strip() for p in path_env.split(os.pathsep) if p.strip()]
    added = False
    for p in extra_paths:
        if os.path.exists(p) and p not in paths:
            paths.append(p)
            added = True
    if added:
        os.environ["PATH"] = os.pathsep.join(paths)

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_CHOCO = REPO_ROOT / "dist" / "choco"
CHOCO_DIR = REPO_ROOT / "packaging" / "chocolatey"
NUSPEC_TEMPLATE = CHOCO_DIR / "kport.nuspec.template"
INSTALL_TEMPLATE = CHOCO_DIR / "tools" / "chocolateyinstall.ps1.template"
UNINSTALL_SCRIPT = CHOCO_DIR / "tools" / "chocolateyuninstall.ps1"
GITHUB_REPO = "farman20ali/kport"


def _c(text: str, code: str) -> str:
    if sys.stdout.isatty() and sys.platform != "win32":
        return f"\033[{code}m{text}\033[0m"
    return text


def ok(msg: str) -> None:
    print(_c(f"✅ {msg}", "92"))


def err(msg: str) -> None:
    print(_c(f"❌ {msg}", "91"), file=sys.stderr)


def warn(msg: str) -> None:
    print(_c(f"⚠️  {msg}", "93"))


def header(msg: str) -> None:
    sep = "=" * 60
    print(_c(f"\n{sep}\n{msg}\n{sep}", "95"))


def read_version() -> str:
    for candidate in [
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "src" / "kport" / "__init__.py",
    ]:
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            m = re.search(
                r"""(?:^version\s*=\s*["']|__version__\s*=\s*["'])([^"']+)""",
                text,
                re.MULTILINE,
            )
            if m:
                return m.group(1).strip()
    return "0.0.0"


def release_url(version: str) -> str:
    return (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"v{version}/kport-{version}-setup.exe"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def find_installer(version: str, explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    matches = sorted((REPO_ROOT / "dist" / "win").glob(f"kport-{version}-setup.exe"))
    if matches:
        return matches[-1]

    matches = sorted((REPO_ROOT / "dist" / "win").glob("kport-*-setup.exe"))
    return matches[-1] if matches else None


def resolve_checksum(
    version: str,
    installer: Path | None,
    explicit_checksum: str | None,
    dry_run: bool,
) -> str | None:
    if explicit_checksum:
        return explicit_checksum.strip().upper()

    if installer and installer.exists():
        checksum = sha256_file(installer)
        ok(f"Computed SHA256 from {installer.name}: {checksum}")
        return checksum

    if dry_run:
        warn("DRY RUN — using placeholder checksum")
        return "0" * 64

    err(
        "Installer checksum required. Provide one of:\n"
        f"  --installer dist/win/kport-{version}-setup.exe\n"
        "  --checksum <sha256>\n"
        "\nThe installer is only used to compute the checksum."
        f"\nChocolatey downloads the binary from:\n  {release_url(version)}"
    )
    return None


def check_prerequisites() -> dict[str, bool]:
    return {"choco": command_exists("choco")}


def print_check_results(checks: dict[str, bool]) -> bool:
    header("Prerequisites Check")
    all_ok = True
    for tool, present in checks.items():
        if present:
            ok(f"{tool} — found")
        else:
            err(f"{tool} — NOT found")
            all_ok = False

    if not checks["choco"]:
        print("\nInstall Chocolatey CLI:")
        print("  https://chocolatey.org/install")
        print("  # or on CI: choco is pre-installed on windows-latest runners")
    return all_ok


def render_template(template_path: Path, version: str, checksum: str) -> str:
    return (
        template_path.read_text(encoding="utf-8")
        .replace("{VERSION}", version)
        .replace("{CHECKSUM}", checksum)
    )


def build_choco(
    version: str,
    checksum: str,
    dry_run: bool = False,
) -> Path | None:
    header(f"Building kport {version} Chocolatey package")

    if not NUSPEC_TEMPLATE.exists() or not INSTALL_TEMPLATE.exists():
        err("Chocolatey templates missing in packaging/chocolatey/")
        return None

    info_url = release_url(version)
    print(f"Download URL: {info_url}")
    print("Package contains install script only (no embedded .exe)")

    with tempfile.TemporaryDirectory(prefix="kport-choco-") as td:
        pkg_root = Path(td) / f"kport.{version}"
        tools_dir = pkg_root / "tools"
        tools_dir.mkdir(parents=True)

        (pkg_root / "kport.nuspec").write_text(
            render_template(NUSPEC_TEMPLATE, version, checksum),
            encoding="utf-8",
        )
        (tools_dir / "chocolateyinstall.ps1").write_text(
            render_template(INSTALL_TEMPLATE, version, checksum),
            encoding="utf-8",
        )

        # Include uninstall script if present
        if UNINSTALL_SCRIPT.exists():
            import shutil as _shutil
            _shutil.copy2(UNINSTALL_SCRIPT, tools_dir / "chocolateyuninstall.ps1")
            ok("Included chocolateyuninstall.ps1")
        else:
            warn("chocolateyuninstall.ps1 not found — uninstall support will be missing")

        cmd = ["choco", "pack", str(pkg_root / "kport.nuspec"), "--output-directory", str(DIST_CHOCO)]
        print("$", " ".join(cmd))

        if dry_run:
            warn("DRY RUN — skipping choco pack")
            return None

        DIST_CHOCO.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
        if result.returncode != 0:
            err("choco pack failed")
            return None

        nupkgs = sorted(DIST_CHOCO.glob(f"kport.{version}.nupkg"))
        if not nupkgs:
            nupkgs = sorted(DIST_CHOCO.glob("kport.*.nupkg"))
        if not nupkgs:
            err("No .nupkg produced")
            return None

        ok(f"Chocolatey package built: {nupkgs[-1]}")
        print("\nTest locally with:")
        print(f"  choco install {nupkgs[-1]} -s {DIST_CHOCO} -y")
        print("\nEnsure the GitHub release asset exists before publishing:")
        print(f"  {info_url}")
        return nupkgs[-1]


def main() -> None:
    version = read_version()

    parser = argparse.ArgumentParser(
        description="Build kport Chocolatey package",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--check", action="store_true", help="Check prerequisites and exit")
    parser.add_argument("--build", action="store_true", help="Build .nupkg")
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing")
    parser.add_argument(
        "--installer",
        default=None,
        help="Local kport-*-setup.exe used only to compute SHA256 checksum",
    )
    parser.add_argument(
        "--checksum",
        default=None,
        help="SHA256 checksum of the GitHub release installer (uppercase hex)",
    )
    parser.add_argument("--version", default=version, help=f"Version string (default: {version})")
    args = parser.parse_args()

    version = args.version

    if args.check:
        ok_all = print_check_results(check_prerequisites())
        sys.exit(0 if ok_all else 1)

    if args.build:
        if sys.platform != "win32" and not args.dry_run:
            warn("Chocolatey packaging is intended for Windows hosts.")
            warn("On Linux/macOS use --dry-run or run on a Windows CI runner.")

        checks = check_prerequisites()
        if not checks["choco"] and not args.dry_run:
            err("choco not found")
            sys.exit(1)

        installer = find_installer(version, args.installer)
        checksum = resolve_checksum(version, installer, args.checksum, args.dry_run)
        if not checksum:
            sys.exit(1)

        path = build_choco(version, checksum, args.dry_run)
        sys.exit(0 if path or args.dry_run else 1)

    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled")
        sys.exit(0)
