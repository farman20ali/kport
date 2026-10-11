#!/usr/bin/env python3
"""manage.py - Universal developer entrypoint for kport.

Provides a unified command-line and interactive interface for local setup,
package building, publishing, running tests, and synchronizing version metadata.

Usage (CLI):
    python manage.py setup
    python manage.py build [--all] [--win] [--mac] [--deb] [--rpm] [--snap] [--choco] [--vscode] [--pypi] [--check] [--dry-run]
    python manage.py publish [--pypi] [--snap] [--vscode] [--all] [--channel {stable,candidate,edge}] [--release-tag VERSION] [--dry-run]
    python manage.py test
    python manage.py sync-version VERSION

Usage (Interactive Menu):
    python manage.py
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

# Configure UTF-8 encoding for standard streams on Windows
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except OSError:
                pass

REPO_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _c(text: str, code: str) -> str:
    """Colorize output on supporting terminals."""
    if sys.stdout.isatty() and sys.platform != "win32":
        return f"\033[{code}m{text}\033[0m"
    return text


def ok(msg: str) -> None:
    print(_c(f"✅ {msg}", "92"))


def err(msg: str) -> None:
    print(_c(f"❌ {msg}", "91"), file=sys.stderr)


def warn(msg: str) -> None:
    print(_c(f"⚠️  {msg}", "93"))


def info(msg: str) -> None:
    print(_c(f"ℹ️  {msg}", "94"))


def section(title: str) -> None:
    sep = "=" * 60
    print(_c(f"\n{sep}", "96"))
    print(_c(f"{title}", "96"))
    print(_c(f"{sep}", "96"))


def run_script(script_name: str, args: list[str], dry_run: bool = False) -> int:
    """Run a helper script from the scripts/ directory."""
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        err(f"Script not found: {script_path}")
        return 1

    cmd = [sys.executable, str(script_path)] + args
    if dry_run:
        print(_c(f"[DRY-RUN] Would execute: {' '.join(cmd)}", "90"))
        return 0

    print(f"$ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return result.returncode


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup() -> int:
    """Set up local development environment."""
    section("Setting up Local Development Environment")
    
    # 1. Install dependencies in editable mode
    info("Installing package and development dependencies in editable mode...")
    pip_cmd = [sys.executable, "-m", "pip", "install", "--break-system-packages", "-e", ".[dev,mcp,packaging]"]
    print(f"$ {' '.join(pip_cmd)}")
    res = subprocess.run(pip_cmd, cwd=str(REPO_ROOT), check=False)
    if res.returncode != 0:
        err("Failed to install dependencies via pip.")
        return res.returncode
    
    ok("Dependencies installed successfully.")

    # 2. Check build tools and prerequisites
    info("\nChecking system prerequisites for packaging...")
    run_script("build_packages.py", ["--all", "--check"])
    run_script("build_vscode.py", ["--check"])
    
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Build platform packages."""
    section("Building Packages")

    build_packages_args = []
    if args.check:
        build_packages_args.append("--check")
    if args.dry_run:
        build_packages_args.append("--dry-run")
    if args.version:
        build_packages_args.extend(["--version", args.version])

    # Map flags to build_packages.py arguments
    package_targets = []
    has_target = any([args.all, args.win, args.mac, args.deb, args.rpm, args.snap, args.choco, args.vscode, args.pypi, args.install_vscode])
    
    # If no target specified but check/dry-run is active, default to --all
    should_default_all = not has_target and (args.check or args.dry_run)

    if args.all or should_default_all:
        package_targets.append("--all")
    else:
        if args.win:
            package_targets.append("--win")
        if args.mac:
            package_targets.append("--mac")
        if args.deb:
            package_targets.append("--deb")
        if args.rpm:
            package_targets.append("--rpm")
        if args.snap:
            package_targets.append("--snap")
        if args.choco:
            package_targets.append("--choco")
        if args.pypi:
            package_targets.append("--pypi")

    ret = 0
    if package_targets:
        ret = run_script("build_packages.py", package_targets + build_packages_args)

    # Handled separately if vscode target requested
    if args.vscode or args.all or should_default_all:
        vscode_args = ["--build"]
        if args.check:
            vscode_args = ["--check"]
        if args.dry_run:
            vscode_args.append("--dry-run")
        vscode_ret = run_script("build_vscode.py", vscode_args)
        if vscode_ret != 0:
            ret = vscode_ret

    if args.install_vscode:
        install_vscode_args = ["--install"]
        if args.dry_run:
            install_vscode_args.append("--dry-run")
        vscode_install_ret = run_script("build_vscode.py", install_vscode_args)
        if vscode_install_ret != 0:
            ret = vscode_install_ret

    return ret


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish built packages."""
    section("Publishing Packages")

    ret = 0

    # Handle release tagging first if requested
    if args.release_tag:
        release_args = ["--version", args.release_tag]
        if args.dry_run:
            release_args.append("--dry-run")
        # Ensure we don't automatically push release tags in local tests without warning
        release_ret = run_script("git_release.py", release_args)
        if release_ret != 0:
            return release_ret

    if args.all or args.pypi:
        pypi_args = []
        if getattr(args, "from_release", False):
            pypi_args.append("--from-release")
        if args.dry_run:
            pypi_args.append("--dry-run")
        pypi_ret = run_script("publish_pypi.py", pypi_args)
        if pypi_ret != 0:
            ret = pypi_ret

    if args.all or args.snap:
        snap_args = ["--publish"]
        if args.channel:
            snap_args.extend(["--channel", args.channel])
        if getattr(args, "from_release", False):
            snap_args.append("--from-release")
        if args.dry_run:
            snap_args.append("--dry-run")
        snap_ret = run_script("publish_snap.py", snap_args)
        if snap_ret != 0:
            ret = snap_ret

    if args.all or args.vscode:
        vscode_args = ["--publish"]
        if args.dry_run:
            vscode_args.append("--dry-run")
        vscode_ret = run_script("build_vscode.py", vscode_args)
        if vscode_ret != 0:
            ret = vscode_ret

    if args.all or args.choco:
        choco_args = ["--chocolatey"]
        if getattr(args, "from_release", False):
            choco_args.append("--from-release")
        if args.dry_run:
            choco_args.append("--dry-run")
        choco_ret = run_script("publish_packages.py", choco_args)
        if choco_ret != 0:
            ret = choco_ret

    if args.all or getattr(args, "winget", False):
        winget_args = ["--winget"]
        if getattr(args, "winget_version", None):
            winget_args.extend(["--winget-version", args.winget_version])
        if getattr(args, "from_release", False):
            winget_args.append("--from-release")
        if getattr(args, "yes", False):
            winget_args.append("--yes")
        if args.dry_run:
            winget_args.append("--dry-run")
        winget_ret = run_script("publish_packages.py", winget_args)
        if winget_ret != 0:
            ret = winget_ret

    return ret


def cmd_test() -> int:
    """Run automated tests."""
    section("Running Automated Tests")

    # Try importing and running pytest
    if importlib.util.find_spec("pytest") is not None:
        info("Found pytest installed. Running unit tests...")
        cmd = ["pytest"]
        print(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
        return result.returncode
    else:
        warn("pytest is not installed. Falling back to the custom lightweight test runner...")
        return run_script("run_tests.py", [])


def cmd_sync_version(version: str) -> int:
    """Sync version metadata."""
    section(f"Synchronizing Version Metadata to: {version}")
    return run_script("sync_version.py", [version])


def cmd_docs(check_only: bool = False) -> int:
    """Regenerate README.md Usage section from live --help output.

    Runs scripts/gen_readme_usage.py, which captures every subcommand's
    --help text and inserts it between <!-- BEGIN/END AUTO-GENERATED USAGE -->
    markers in README.md so the documentation never drifts from the actual CLI.

    Pass check_only=True (or --check on the CLI) to fail with exit code 1 if
    README is stale without modifying it (useful as a CI gate).
    """
    section("Regenerating README Usage Section")
    script_args = ["--check"] if check_only else []
    return run_script("gen_readme_usage.py", script_args)


# ── Interactive Mode ──────────────────────────────────────────────────────────

def interactive_menu() -> int:
    while True:
        section("KPort developer manager")
        print("\n🛠️  General Tasks:")
        print("  1. Setup local environment (dependencies & tools check)")
        print("  2. Run unit tests")
        print("  3. Synchronize version metadata")
        
        print("\n📦 Build Options:")
        print("  4. Build all platform packages")
        print("  5. Build Python package (PyPI)")
        print("  6. Build VS Code extension")
        print("  7. Build Snap package")
        
        print("\n📤 Publish Options:")
        print("  8. Publish all packages")
        print("  9. Publish to PyPI (interactive)")
        print("  10. Publish VS Code extension")
        print("  11. Publish Snap package (stable)")
        print("  12. Publish Snap package (edge)")
        print("  13. Publish Chocolatey package")
        print("  14. Publish WinGet package (Windows Package Manager)")

        print("\n📝 Documentation:")
        print("  15. Regenerate README usage from --help (docs)")
        print("  16. Check if README usage is stale (CI gate)")

        print("\n0. Exit")

        try:
            choice = input("\nEnter choice (0-16): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Goodbye!")
            return 0

        if choice == "0":
            print("👋 Goodbye!")
            return 0
        elif choice == "1":
            cmd_setup()
        elif choice == "2":
            cmd_test()
        elif choice == "3":
            version = input("Enter version to sync (e.g. 3.2.3): ").strip()
            if version:
                cmd_sync_version(version)
            else:
                err("No version specified.")
        elif choice == "4":
            args = argparse.Namespace(all=True, win=False, mac=False, deb=False, rpm=False, snap=False, choco=False, pypi=False, vscode=False, install_vscode=False, check=False, dry_run=False, version=None)
            cmd_build(args)
        elif choice == "5":
            args = argparse.Namespace(all=False, win=False, mac=False, deb=False, rpm=False, snap=False, choco=False, pypi=True, vscode=False, install_vscode=False, check=False, dry_run=False, version=None)
            cmd_build(args)
        elif choice == "6":
            args = argparse.Namespace(all=False, win=False, mac=False, deb=False, rpm=False, snap=False, choco=False, pypi=False, vscode=True, install_vscode=False, check=False, dry_run=False, version=None)
            cmd_build(args)
        elif choice == "7":
            args = argparse.Namespace(all=False, win=False, mac=False, deb=False, rpm=False, snap=True, choco=False, pypi=False, vscode=False, install_vscode=False, check=False, dry_run=False, version=None)
            cmd_build(args)
        elif choice == "8":
            args = argparse.Namespace(all=True, pypi=False, snap=False, choco=False, winget=False, vscode=False, channel=None, release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "9":
            args = argparse.Namespace(all=False, pypi=True, snap=False, choco=False, winget=False, vscode=False, channel=None, release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "10":
            args = argparse.Namespace(all=False, pypi=False, snap=False, choco=False, winget=False, vscode=True, channel=None, release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "11":
            args = argparse.Namespace(all=False, pypi=False, snap=True, choco=False, winget=False, vscode=False, channel="stable", release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "12":
            args = argparse.Namespace(all=False, pypi=False, snap=True, choco=False, winget=False, vscode=False, channel="edge", release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "13":
            args = argparse.Namespace(all=False, pypi=False, snap=False, choco=True, winget=False, vscode=False, channel=None, release_tag=None, dry_run=False, from_release=False, winget_version=None, yes=False)
            cmd_publish(args)
        elif choice == "14":
            wg_ver = input("Enter WinGet version override (leave blank for current): ").strip() or None
            args = argparse.Namespace(all=False, pypi=False, snap=False, choco=False, winget=True, vscode=False, channel=None, release_tag=None, dry_run=False, from_release=False, winget_version=wg_ver, yes=False)
            cmd_publish(args)
        elif choice == "15":
            cmd_docs(check_only=False)
        elif choice == "16":
            cmd_docs(check_only=True)
        else:
            err("Invalid choice. Please choose again.")


# ── CLI Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Universal developer entrypoint for kport",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: setup
    subparsers.add_parser("setup", help="Set up local dev environment")

    # Command: build
    build_parser = subparsers.add_parser("build", help="Build platform installer packages")
    build_parser.add_argument("--all", action="store_true", help="Build everything appropriate for host platform")
    build_parser.add_argument("--win", action="store_true", help="Build Windows installer")
    build_parser.add_argument("--mac", action="store_true", help="Build macOS pkg installer")
    build_parser.add_argument("--deb", action="store_true", help="Build Debian package")
    build_parser.add_argument("--rpm", action="store_true", help="Build RPM package")
    build_parser.add_argument("--snap", action="store_true", help="Build Snap package")
    build_parser.add_argument("--choco", action="store_true", help="Build Chocolatey package")
    build_parser.add_argument("--vscode", action="store_true", help="Build VS Code extension VSIX")
    build_parser.add_argument("--pypi", action="store_true", help="Build PyPI wheel + source package")
    build_parser.add_argument("--install-vscode", action="store_true", help="Install built VS Code extension locally")
    build_parser.add_argument("--check", action="store_true", help="Verify build prerequisites only")
    build_parser.add_argument("--dry-run", action="store_true", help="Preview build commands without running")
    build_parser.add_argument("--version", help="Override output version string")

    # Command: publish
    publish_parser = subparsers.add_parser("publish", help="Publish packages to marketplaces/repositories")
    publish_parser.add_argument("--all", action="store_true", help="Publish all available targets")
    publish_parser.add_argument("--pypi", action="store_true", help="Publish to PyPI")
    publish_parser.add_argument("--snap", action="store_true", help="Publish Snap package")
    publish_parser.add_argument("--vscode", action="store_true", help="Publish VS Code extension")
    publish_parser.add_argument("--choco", action="store_true", help="Publish to Chocolatey Community")
    publish_parser.add_argument("--winget", action="store_true", help="Submit or update package in Windows Package Manager (WinGet)")
    publish_parser.add_argument("--winget-version", help="Explicit version for WinGet package (e.g. 5.1.0 or 2.5.0)")
    publish_parser.add_argument("--from-release", action="store_true", help="Download release artifacts from GitHub Releases before publishing")
    publish_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    publish_parser.add_argument("--channel", choices=["stable", "candidate", "edge"], default="stable", help="Snap store target channel")
    publish_parser.add_argument("--release-tag", help="Create a local Git release tag with specified version first")
    publish_parser.add_argument("--dry-run", action="store_true", help="Preview publish command only")

    # Command: test
    subparsers.add_parser("test", help="Run automated test suite")

    # Command: docs
    docs_parser = subparsers.add_parser("docs", help="Regenerate README Usage section from live --help output")
    docs_parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if README is stale instead of updating it (use as CI gate)",
    )

    # Command: sync-version
    sync_parser = subparsers.add_parser("sync-version", help="Synchronize versions across metadata files")
    sync_parser.add_argument("version", help="Version to sync (e.g., 3.2.3)")

    args = parser.parse_args()

    if not args.command:
        # No subcommand, start interactive menu
        return interactive_menu()

    if args.command == "setup":
        return cmd_setup()
    elif args.command == "build":
        # Make sure at least one build target or flag is specified
        if not any([args.all, args.win, args.mac, args.deb, args.rpm, args.snap, args.choco, args.vscode, args.pypi, args.install_vscode, args.check]):
            build_parser.print_help()
            return 1
        return cmd_build(args)
    elif args.command == "publish":
        # Make sure at least one publish target or flag is specified
        if not any([args.all, args.pypi, args.snap, args.choco, args.winget, args.vscode, args.release_tag]):
            publish_parser.print_help()
            return 1
        return cmd_publish(args)
    elif args.command == "test":
        return cmd_test()
    elif args.command == "docs":
        return cmd_docs(check_only=args.check)
    elif args.command == "sync-version":
        return cmd_sync_version(args.version)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled")
        sys.exit(0)
