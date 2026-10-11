#!/usr/bin/env python3
"""Automated release script for kport.

Handles version validation, git tagging, and optionally builds/uploads
PyPI packages. All platform-specific builds (Windows, macOS, Debian, RPM)
are handled automatically by GitHub Actions on tag push.

Usage:
  python release.py                    # Interactive mode
  python release.py --version 3.2.1    # Release specific version
  python release.py --no-pypi          # Skip PyPI build (tag only)
  python release.py --dry-run          # Preview without making changes
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class Colors:
    HEADER = '\033[95m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    END    = '\033[0m'
    BOLD   = '\033[1m'


def print_header(msg: str) -> None:
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{msg}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")


def print_step(msg: str)    -> None: print(f"\n{Colors.CYAN}▶ {msg}{Colors.END}")
def print_success(msg: str) -> None: print(f"{Colors.GREEN}✅ {msg}{Colors.END}")
def print_error(msg: str)   -> None: print(f"{Colors.RED}❌ {msg}{Colors.END}", file=sys.stderr)
def print_warning(msg: str) -> None: print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")


def run_command(cmd: list[str], description: str, check: bool = True) -> subprocess.CompletedProcess:
    print_step(description)
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if check and result.returncode != 0:
        print_error(f"Failed: {description}")
        sys.exit(result.returncode)
    if result.returncode == 0:
        print_success(description)
    return result


def read_version() -> str | None:
    """Read version from pyproject.toml or src/kport/__init__.py."""
    for candidate in [
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "src" / "kport" / "__init__.py",
    ]:
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            m = re.search(
                r"""(?:^version\s*=\s*["']|__version__\s*=\s*["'])([^"']+)""",
                text, re.MULTILINE,
            )
            if m:
                return m.group(1).strip()
    return None


def check_git_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    return len(result.stdout.strip()) == 0


def check_tag_exists(tag: str) -> bool:
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    return len(result.stdout.strip()) > 0


def build_pypi(dry_run: bool = False) -> bool:
    """Build PyPI wheel + sdist locally."""
    if dry_run:
        print_warning("DRY RUN: Would build PyPI packages")
        return True

    print_header("Building PyPI Packages")

    publish_script = REPO_ROOT / "scripts" / "publish_pypi.py"
    if not publish_script.exists():
        print_error("publish_pypi.py not found")
        return False

    result = subprocess.run(
        [sys.executable, str(publish_script)],
        cwd=str(REPO_ROOT),
        input="1\n",
        text=True,
        check=False,
    )

    if result.returncode != 0:
        print_error("PyPI build failed")
        return False

    whl_files = list((REPO_ROOT / "dist").glob("*.whl"))
    if not whl_files:
        print_error("No .whl files found in dist/")
        return False

    print_success("PyPI packages built successfully")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Release script for kport — tags, PyPI build, then GitHub Actions does the rest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version",  help="Version to release (e.g. 3.2.1)")
    parser.add_argument("--no-pypi",  action="store_true", help="Skip local PyPI build")
    parser.add_argument("--dry-run",  action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    print_header("🚀 kport Release")

    # ── Read version ──────────────────────────────────────────────────────────
    current_version = read_version()
    if not current_version:
        print_error("Could not read version from pyproject.toml / __init__.py")
        sys.exit(1)

    version = args.version or current_version
    tag     = f"v{version}"

    print(f"\n{Colors.BOLD}Version:{Colors.END} {version}")
    print(f"{Colors.BOLD}Tag:    {Colors.END} {tag}")

    if args.dry_run:
        print_warning("\n🧪 DRY RUN MODE — no changes will be made")

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    print_header("Pre-flight Checks")

    if not check_git_clean():
        print_error("Working directory has uncommitted changes — commit or stash first")
        sys.exit(1)
    print_success("Working directory is clean")

    if check_tag_exists(tag):
        print_error(f"Tag {tag} already exists")
        print(f"  Delete with: git tag -d {tag} && git push origin :refs/tags/{tag}")
        sys.exit(1)
    print_success(f"Tag {tag} is available")

    # ── Confirm ───────────────────────────────────────────────────────────────
    if not args.dry_run:
        print(f"\n{Colors.YELLOW}Ready to release kport {version}{Colors.END}")
        print("GitHub Actions will build: Windows .exe, macOS .pkg, Debian .deb, RPM .rpm")
        confirm = input("\nProceed? (y/N): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("❌ Release cancelled")
            sys.exit(0)

    # ── Create & push git tag ─────────────────────────────────────────────────
    print_header("Creating Git Tag")

    if not args.dry_run:
        run_command(["git", "tag", "-a", tag, "-m", f"Release {version}"], f"Create tag {tag}")
        run_command(["git", "push", "origin", "main"], "Push commits")
        run_command(["git", "push", "origin", "--tags"], "Push tags")
    else:
        print_warning(f"DRY RUN: Would create and push tag {tag}")

    # ── Optional local PyPI build ─────────────────────────────────────────────
    pypi_success = True
    if not args.no_pypi:
        pypi_success = build_pypi(args.dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    print_header("Done")

    print(f"\n  {Colors.BOLD}Tag pushed:{Colors.END} {tag}")

    if not args.no_pypi:
        icon = "✅" if pypi_success else "❌"
        print(f"  {icon} PyPI packages built locally")

    print(f"\n  {Colors.CYAN}GitHub Actions is now building:{Colors.END}")
    print("    • Windows .exe installer")
    print("    • macOS   .pkg installer")
    print("    • Debian  .deb package")
    print("    • RPM     .rpm package")
    print("    • PyPI    .whl + .tar.gz")
    print("    • GitHub  Release (artifacts auto-attached)")

    print(f"\n  {Colors.BOLD}Next steps:{Colors.END}")
    if not args.no_pypi and pypi_success and not args.dry_run:
        print("    1. Upload to PyPI:    python manage.py publish --pypi")
    print("    2. Watch Actions:     https://github.com/farman20ali/kport/actions")
    print(f"    3. Check release:     https://github.com/farman20ali/kport/releases/tag/{tag}")
    print(f"    4. Test install:      pip install kport=={version}")

    if not args.dry_run:
        print(f"\n{Colors.GREEN}🎉 kport {version} is on its way!{Colors.END}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)