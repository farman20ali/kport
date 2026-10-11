#!/usr/bin/env python3
"""Snap package publisher for KPort.

Publishes built .snap packages to the Snap Store.

Usage (automated / CI):
    python snap_publish.py --check              # Check prerequisites
    python snap_publish.py --publish            # Publish snap
    python snap_publish.py --publish --edge     # Publish to edge channel
    python snap_publish.py --dry-run            # Show what would happen

Interactive (recommended for first-time setup):
    python snap_publish.py                      # Interactive menu

Requirements:
    - Built .snap file from snap_build.py
    - Snapcraft login with valid credentials
    - snapcraft CLI installed
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except OSError:
                pass

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_SNAP = REPO_ROOT / "dist" / "snap"


def _c(text: str, code: str) -> str:
    """Colorize output on supporting terminals"""
    if sys.stdout.isatty() and sys.platform != "win32":
        return f"\033[{code}m{text}\033[0m"
    return text


def ok(msg: str) -> None:
    """Print success message"""
    print(_c(f"✅ {msg}", "92"))


def err(msg: str) -> None:
    """Print error message"""
    print(_c(f"❌ {msg}", "91"), file=sys.stderr)


def warn(msg: str) -> None:
    """Print warning message"""
    print(_c(f"⚠️  {msg}", "93"))


def info(msg: str) -> None:
    """Print info message"""
    print(_c(f"ℹ️  {msg}", "94"))


def read_version() -> str:
    """Read version from pyproject.toml."""
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


def run_cmd(cmd: str, description: str, check: bool = True) -> int:
    """Run a command and return exit code"""
    print(f"\n{'='*60}")
    print(f"🔄 {description}")
    print(f"{'='*60}")
    print(f"$ {cmd}\n")
    
    result = subprocess.run(cmd, shell=True, check=False)
    
    if check and result.returncode != 0:
        err(f"Failed: {description}")
        return result.returncode
    
    if result.returncode == 0:
        ok(f"Success: {description}")
    
    return result.returncode


def check_snapcraft_installed() -> bool:
    """Check if snapcraft is installed"""
    result = subprocess.run(
        "snapcraft --version",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def check_snapcraft_authenticated() -> bool:
    """Check if snapcraft is authenticated with Snap Store"""
    # Try to get whoami output
    result = subprocess.run(
        "snapcraft whoami",
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return bool(result.stdout.strip())
    return False


def find_snap_file() -> Path | None:
    """Find the built .snap file"""
    if not DIST_SNAP.exists():
        return None
    
    snap_files = list(DIST_SNAP.glob("*.snap"))
    
    if not snap_files:
        return None
    
    # Return the most recent snap file
    return max(snap_files, key=lambda p: p.stat().st_mtime)


def get_snap_version(snap_file: Path) -> str:
    """Extract version from snap filename"""
    # Format: kport_<version>_<arch>.snap
    match = re.search(r"kport_([^_]+)_", snap_file.name)
    return match.group(1) if match else "unknown"


def check_requirements() -> bool:
    """Check all prerequisites for publishing"""
    print("\n" + "="*60)
    print("📋 Checking prerequisites")
    print("="*60)
    
    all_ok = True
    
    # Check snapcraft
    if check_snapcraft_installed():
        ok("snapcraft is installed")
    else:
        err("snapcraft is NOT installed")
        print("  Install: sudo apt install snapcraft")
        all_ok = False
    
    # Check Snap Store authentication
    if check_snapcraft_authenticated():
        ok("snapcraft is authenticated with Snap Store")
    else:
        warn("snapcraft is NOT authenticated")
        print("  Run: snapcraft login")
    
    # Check for built snap file
    snap_file = find_snap_file()
    if snap_file:
        version = get_snap_version(snap_file)
        ok(f"Built snap file found: {snap_file.name} (v{version})")
    else:
        err(f"No built snap file found in {DIST_SNAP}")
        print("  Run: python scripts/build_snap.py --build")
        all_ok = False
    
    return all_ok


def download_snap_from_release(version: str, dest_dir: Path) -> Path:
    """Download .snap package from GitHub release."""
    import json
    import urllib.request
    tag = f"v{version}"
    api_url = f"https://api.github.com/repos/farman20ali/kport/releases/tags/{tag}"
    info(f"Querying GitHub Release {tag} for .snap package…")
    filename = f"kport_{version}_amd64.snap"
    download_url = f"https://github.com/farman20ali/kport/releases/download/{tag}/{filename}"
    total_size = 0

    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "kport-snap-publish/1.0", "Accept": "application/vnd.github+json"})
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        assets = data.get("assets", [])
        snaps = [a for a in assets if ".snap" in a["name"]]
        if snaps:
            filename = snaps[0]["name"]
            download_url = snaps[0]["browser_download_url"]
            total_size = snaps[0].get("size", 0)
    except Exception as exc:  # noqa: BLE001
        warn(f"GitHub API note: {exc}. Using direct release URL fallback.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / filename
    info(f"Downloading {filename} from {download_url}…")
    req = urllib.request.Request(download_url, headers={"User-Agent": "kport-snap-publish/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest_file, "wb") as fh:
        total = int(resp.headers.get("Content-Length", total_size or 0))
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            fh.write(chunk)
            downloaded += len(chunk)
            pct = int(downloaded * 100 / total) if total else 0
            print(f"\r  Downloading … {pct:3d}% ({downloaded // 1024:,} KB)", end="", flush=True)
    print()
    ok(f"Downloaded snap: {dest_file.name}")
    return dest_file


def publish_snap(
    channel: str = "stable",
    dry_run: bool = False,
    snap_file: Path | None = None,
    from_release: bool = False,
    version: str | None = None,
) -> int:
    """Publish snap to the Snap Store"""
    if from_release:
        v = version or read_version()
        try:
            snap_file = download_snap_from_release(v, DIST_SNAP / "from-release")
        except Exception as exc:  # noqa: BLE001
            err(f"Failed to download .snap from release: {exc}")
            return 1
    elif snap_file is None:
        snap_file = find_snap_file()
    
    if not snap_file or not snap_file.exists():
        err(f"No snap file found in {DIST_SNAP}")
        warn("Run: python scripts/build_snap.py --build, or specify --from-release")
        return 1
    
    ver = get_snap_version(snap_file)
    info(f"Publishing {snap_file.name} (v{ver}) to '{channel}' channel")
    
    # Check authentication
    if not check_snapcraft_authenticated():
        err("Not authenticated with Snap Store")
        print("\nRun: snapcraft login")
        print("Then: python scripts/publish_snap.py --publish")
        return 1
    
    cmd = f"snapcraft upload {snap_file} --release {channel}"
    
    if dry_run:
        print(f"\n[DRY-RUN] Would execute:\n$ {cmd}")
        return 0
    
    rc = run_cmd(cmd, f"Publishing snap v{ver} to {channel} channel", check=True)

    if rc == 0:
        # Push listing metadata (summary, description, icon) to the store
        meta_cmd = f"snapcraft upload-metadata {snap_file} --force"
        run_cmd(meta_cmd, "Syncing store listing (summary, description, icon)", check=False)

    return rc


def login_snapcraft(dry_run: bool = False) -> int:
    """Authenticate with Snap Store"""
    info("Authenticating with Snap Store")
    print("\nYou will be prompted for your Snap Store credentials.")
    print("Get your credentials at: https://snapcraft.io/account")
    
    cmd = "snapcraft login"
    
    if dry_run:
        print(f"\n[DRY-RUN] Would execute:\n$ {cmd}")
        return 0
    
    return run_cmd(cmd, "Authenticating with Snap Store", check=True)


def interactive_menu() -> int:
    """Show interactive menu"""
    print("\n" + "="*60)
    print("📦 KPort Snap Package Publisher")
    print("="*60)
    
    snap_file = find_snap_file()
    if snap_file:
        version = get_snap_version(snap_file)
        print(f"\nFound: {snap_file.name} (v{version})")
    else:
        warn("No built snap file found")
    
    print("\nWhat would you like to do?")
    print("  1. Check prerequisites")
    print("  2. Login to Snap Store")
    print("  3. Publish to stable channel")
    print("  4. Publish to edge channel (testing)")
    print("  5. Publish to candidate channel (release candidate)")
    print("  0. Exit")
    
    choice = input("\nEnter choice (0-5): ").strip()
    
    if choice == "0":
        return 0
    elif choice == "1":
        check_requirements()
        return 0
    elif choice == "2":
        return login_snapcraft()
    elif choice == "3":
        return publish_snap(channel="stable")
    elif choice == "4":
        return publish_snap(channel="edge")
    elif choice == "5":
        return publish_snap(channel="candidate")
    else:
        err("Invalid choice")
        return 1


def main() -> int:
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Publish KPort snap package to Snap Store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check all prerequisites"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Authenticate with Snap Store"
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish snap to Snap Store"
    )
    parser.add_argument(
        "--channel",
        choices=["stable", "candidate", "edge"],
        default="stable",
        help="Release channel (default: stable)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without doing it"
    )
    
    parser.add_argument(
        "--from-release",
        action="store_true",
        help="Download .snap package from GitHub Release instead of local dist/snap",
    )
    parser.add_argument(
        "--file",
        help="Explicit path to .snap package file",
    )
    parser.add_argument(
        "--version",
        help="Override version string when fetching from release",
    )
    
    args = parser.parse_args()
    
    # Interactive mode if no args
    if not any([args.check, args.login, args.publish]):
        return interactive_menu()
    
    # Execute requested operations
    if args.check:
        check_requirements()
        return 0
    
    if args.login:
        return login_snapcraft(dry_run=args.dry_run)
    
    if args.publish:
        snap_path = Path(args.file) if args.file else None
        return publish_snap(
            channel=args.channel,
            dry_run=args.dry_run,
            snap_file=snap_path,
            from_release=args.from_release,
            version=args.version,
        )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
