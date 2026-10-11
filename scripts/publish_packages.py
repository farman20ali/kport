#!/usr/bin/env python3
"""Unified package publisher for kport.

Publishes built artifacts to PyPI, Snap Store, or Chocolatey Community.

Usage:
    python publish_packages.py --pypi          # Publish to PyPI
    python publish_packages.py --snap          # Publish to Snap Store
    python publish_packages.py --chocolatey    # Publish to Chocolatey Community (interactive)
    python publish_packages.py --all           # Publish to all configured channels
    python publish_packages.py --dry-run       # Preview without executing
    python publish_packages.py --check         # Verify prerequisites only

Interactive Chocolatey Menu:
    When --chocolatey is used in an interactive terminal, you can choose
    how to handle the package before pushing it to the community server:

      [1] Push the existing package as-is
      [2] Rebuild using live GitHub release checksum
      [3] Rebuild using manual checksum entry
      [4] Rebuild using custom file path
      [5] Rebuild using local installer file
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ── UTF-8 console on Windows ─────────────────────────────────────────────────
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8")
            except OSError:
                pass

REPO_ROOT  = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent

GITHUB_REPO = "farman20ali/kport"

# ── helpers ───────────────────────────────────────────────────────────────────

def _c(text: str, code: str) -> str:
    if sys.stdout.isatty() and sys.platform != "win32":
        return f"\033[{code}m{text}\033[0m"
    return text


def ok(msg: str)     -> None: print(_c(f"  ✅ {msg}", "92"))
def err(msg: str)    -> None: print(_c(f"  ❌ {msg}", "91"), file=sys.stderr)
def warn(msg: str)   -> None: print(_c(f"  ⚠️  {msg}", "93"))
def info(msg: str)   -> None: print(_c(f"  ℹ️  {msg}", "96"))
def section(msg: str)-> None:
    print(_c(f"\n{'─'*60}", "94;1"))
    print(_c(f"  {msg}", "94;1"))
    print(_c(f"{'─'*60}", "94;1"))


def read_version() -> str:
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
    return "0.0.0"


def _run_script(script: str, extra_args: list[str]) -> bool:
    """Invoke a sibling script as a subprocess."""
    script_path = SCRIPT_DIR / script
    if not script_path.exists():
        err(f"Script not found: {script_path}")
        return False
    cmd = [sys.executable, str(script_path)] + extra_args
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return result.returncode == 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _sha256_url(url: str) -> str | None:
    """Download url and return its SHA-256, printing progress."""
    try:
        digest = hashlib.sha256()
        req = urllib.request.Request(url, headers={"User-Agent": "kport-publish/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                digest.update(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  Downloading … {pct:3d}%", end="", flush=True)
        print()
        return digest.hexdigest().upper()
    except Exception as exc:  # noqa: BLE001
        print()
        err(f"Download failed: {exc}")
        return None


def _find_nupkg() -> Path | None:
    dist_choco = REPO_ROOT / "dist" / "choco"
    pkgs = sorted(dist_choco.glob("kport.*.nupkg"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pkgs[0] if pkgs else None


def _find_local_installer(version: str) -> Path | None:
    dist_win = REPO_ROOT / "dist" / "win"
    matches = sorted(dist_win.glob(f"kport-{version}-setup.exe"))
    if not matches:
        matches = sorted(dist_win.glob("kport-*-setup.exe"))
    return matches[-1] if matches else None


# ── Chocolatey ────────────────────────────────────────────────────────────────

def _choco_rebuild(version: str, extra_args: list[str], dry_run: bool) -> bool:
    """Rebuild the Chocolatey .nupkg with the given extra args."""
    args = ["--build"] + extra_args
    if dry_run:
        args.append("--dry-run")
    return _run_script("build_choco.py", args)


def _choco_push(nupkg: Path, api_key: str | None, dry_run: bool) -> bool:
    """Push a .nupkg to the Chocolatey Community Repository."""
    choco = shutil.which("choco")
    if not choco:
        err("choco CLI not found. Install from https://chocolatey.org/install")
        return False

    if not api_key:
        err("CHOCO_API_KEY environment variable is not set.")
        info("Get your API key at: https://community.chocolatey.org/account")
        return False

    cmd = [
        "choco", "push", str(nupkg),
        "--source", "https://push.chocolatey.org/",
        "--api-key", api_key,
    ]

    if dry_run:
        print("\n  [DRY-RUN] Would execute:")
        print(f"  $ choco push {nupkg.name} --source https://push.chocolatey.org/ --api-key ***")
        return True

    print(f"  $ choco push {nupkg.name} --source https://push.chocolatey.org/ --api-key ***")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return result.returncode == 0


def publish_chocolatey(version: str, dry_run: bool, check: bool) -> bool:
    section("Publishing to Chocolatey")

    api_key = os.environ.get("CHOCO_API_KEY", "").strip() or None

    if check:
        choco_ok = bool(shutil.which("choco"))
        if choco_ok:
            ok("choco CLI found")
        else:
            err("choco CLI NOT found — install from https://chocolatey.org/install")
        if api_key:
            ok("CHOCO_API_KEY is set")
        else:
            warn("CHOCO_API_KEY not set — required to publish")
        nupkg = _find_nupkg()
        if nupkg:
            ok(f"Built package found: {nupkg.name}")
        else:
            warn("No .nupkg found in dist/choco/ — run: python build_packages.py --choco")
        return choco_ok

    github_url = (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"v{version}/kport-{version}-setup.exe"
    )

    # ── Interactive menu (TTY only) ───────────────────────────────────────────
    if sys.stdin.isatty():
        existing = _find_nupkg()
        print()
        print("─" * 50)
        print("  --- Chocolatey Package Publish Options ---")
        print("  Choose an action:")
        print("    [1] Push the existing package as-is (default)")
        print("    [2] Rebuild package using live GitHub release checksum")
        print("    [3] Rebuild package using manual checksum entry")
        print("    [4] Rebuild package using custom file")
        print("    [5] Rebuild package using local installer file")
        print("─" * 50)
        if existing:
            info(f"Current package: {existing.name}")
        else:
            warn("No existing .nupkg found in dist/choco/")
        print()

        try:
            raw = input("  Enter choice [1-5] (default 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            warn("No input — defaulting to [1] push as-is")
            raw = "1"

        choice = raw if raw in ("1", "2", "3", "4", "5") else "1"
    else:
        choice = "1"   # CI: push as-is

    # ── Option dispatch ───────────────────────────────────────────────────────

    if choice == "1":
        # Push existing package as-is
        nupkg = _find_nupkg()
        if not nupkg:
            err("No .nupkg found in dist/choco/")
            warn("Build one first: python build_packages.py --choco")
            return False
        info(f"Pushing: {nupkg.name}")
        return _choco_push(nupkg, api_key, dry_run)

    elif choice == "2":
        # Rebuild from live GitHub release URL checksum
        info(f"Fetching checksum from: {github_url}")
        checksum = _sha256_url(github_url)
        if not checksum:
            err("Could not download from GitHub. Ensure the release asset exists.")
            return False
        ok(f"SHA-256: {checksum}")
        if not _choco_rebuild(version, ["--checksum", checksum], dry_run):
            return False
        nupkg = _find_nupkg()
        if not nupkg:
            err("Rebuild failed — no .nupkg produced.")
            return False
        return _choco_push(nupkg, api_key, dry_run)

    elif choice == "3":
        # Rebuild from manual checksum
        try:
            checksum = input("  Enter SHA-256 (64 hex chars): ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            checksum = ""
        if len(checksum) != 64 or not all(c in "0123456789ABCDEF" for c in checksum):
            err("Invalid checksum — must be exactly 64 hex characters.")
            return False
        ok(f"Using checksum: {checksum}")
        if not _choco_rebuild(version, ["--checksum", checksum], dry_run):
            return False
        nupkg = _find_nupkg()
        if not nupkg:
            err("Rebuild failed — no .nupkg produced.")
            return False
        return _choco_push(nupkg, api_key, dry_run)

    elif choice == "4":
        # Rebuild from custom file path
        try:
            path_str = input("  Enter path to installer/file: ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            path_str = ""
        custom = Path(path_str)
        if not custom.exists():
            err(f"File not found: {custom}")
            return False
        info(f"Hashing: {custom.name}")
        checksum = _sha256_file(custom)
        ok(f"SHA-256: {checksum}")
        if not _choco_rebuild(version, ["--checksum", checksum], dry_run):
            return False
        nupkg = _find_nupkg()
        if not nupkg:
            err("Rebuild failed — no .nupkg produced.")
            return False
        return _choco_push(nupkg, api_key, dry_run)

    else:  # choice == "5"
        # Rebuild from local installer in dist/win/
        installer = _find_local_installer(version)
        if not installer:
            err(f"No local installer found in dist/win/ for v{version}")
            warn("Build the Windows installer first: python build_packages.py --win")
            return False
        info(f"Using installer: {installer.name}")
        checksum = _sha256_file(installer)
        ok(f"SHA-256: {checksum}")
        if not _choco_rebuild(version, ["--installer", str(installer)], dry_run):
            return False
        nupkg = _find_nupkg()
        if not nupkg:
            err("Rebuild failed — no .nupkg produced.")
            return False
        return _choco_push(nupkg, api_key, dry_run)


# ── PyPI ──────────────────────────────────────────────────────────────────────

def publish_pypi(version: str, dry_run: bool, check: bool) -> bool:
    section("Publishing to PyPI")
    args = ["--check"] if check else (["--dry-run"] if dry_run else [])
    return _run_script("publish_pypi.py", args)


# ── Snap ──────────────────────────────────────────────────────────────────────

def publish_snap(version: str, dry_run: bool, check: bool) -> bool:
    section("Publishing to Snap Store")
    args = ["--check"] if check else (["--dry-run"] if dry_run else [])
    return _run_script("publish_snap.py", args)


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: dict[str, bool | None]) -> None:
    print()
    print(_c("═" * 60, "95;1"))
    print(_c("  Publish Summary", "95;1"))
    print(_c("═" * 60, "95;1"))
    for name, result in results.items():
        if result is None:
            print(f"  {'─':2} {name:16} (skipped)")
        elif result:
            ok(f"{name:16} SUCCESS")
        else:
            err(f"{name:16} FAILED")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    version = read_version()

    parser = argparse.ArgumentParser(
        prog="publish_packages.py",
        description="Publish kport packages to distribution channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    tgroup = parser.add_argument_group("targets")
    tgroup.add_argument(
        "--all", action="store_true",
        help="Publish to all configured channels (PyPI + Chocolatey + Snap)",
    )
    tgroup.add_argument(
        "--pypi", action="store_true",
        help="Publish to PyPI (requires TWINE_USERNAME / TWINE_PASSWORD or .pypirc)",
    )
    tgroup.add_argument(
        "--chocolatey", "--choco", dest="chocolatey", action="store_true",
        help="Publish to Chocolatey Community (requires CHOCO_API_KEY)",
    )
    tgroup.add_argument(
        "--snap", action="store_true",
        help="Publish to Snap Store (requires SNAPCRAFT_STORE_CREDENTIALS)",
    )

    mgroup = parser.add_argument_group("modes")
    mgroup.add_argument(
        "--check", action="store_true",
        help="Verify credentials and prerequisites without publishing",
    )
    mgroup.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be published without actually pushing",
    )
    mgroup.add_argument(
        "--version", default=version,
        help=f"Override version string (default: {version})",
    )

    args = parser.parse_args()
    version = args.version

    if args.all:
        args.pypi        = True
        args.chocolatey  = True
        args.snap        = True

    any_target = any([args.pypi, args.chocolatey, args.snap])
    if not any_target:
        parser.print_help()
        print()
        warn("No target selected. Use --all or one of: --pypi  --chocolatey  --snap")
        sys.exit(0)

    print()
    print(_c("═" * 60, "95;1"))
    print(_c(f"  🚀 kport Package Publisher  v{version}", "95;1"))
    print(_c("═" * 60, "95;1"))
    if args.dry_run:
        warn("DRY-RUN mode — nothing will actually be published")
    if args.check:
        info("CHECK mode — verifying prerequisites only")

    results: dict[str, bool | None] = {}

    if args.pypi:
        results["PyPI"] = publish_pypi(version, args.dry_run, args.check)

    if args.chocolatey:
        results["Chocolatey"] = publish_chocolatey(version, args.dry_run, args.check)

    if args.snap:
        results["Snap Store"] = publish_snap(version, args.dry_run, args.check)

    print_summary(results)

    failed = [n for n, r in results.items() if r is False]
    if failed:
        err(f"Failed: {', '.join(failed)}")
        sys.exit(1)

    if not args.check and not args.dry_run:
        ok("All requested packages published successfully.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        err(f"Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
