#!/usr/bin/env python3
"""Unified package publisher for kport.

Publishes built artifacts to PyPI, Snap Store, Chocolatey Community, or WinGet.

Usage:
    python publish_packages.py --pypi                     # Publish to PyPI
    python publish_packages.py --snap                     # Publish to Snap Store
    python publish_packages.py --chocolatey               # Publish to Chocolatey Community
    python publish_packages.py --winget                   # Submit / update in WinGet
    python publish_packages.py --winget --winget-version 5.1.0  # Specific WinGet version
    python publish_packages.py --from-release             # Download artifacts from GitHub Release
    python publish_packages.py --all                      # Publish to all configured channels
    python publish_packages.py --dry-run                  # Preview without executing
    python publish_packages.py --check                    # Verify prerequisites and credentials

Interactive Menus:
    When run interactively without non-interactive flags, each channel allows
    choosing how to handle packages:
      • Chocolatey : Rebuild via live release checksum, local file, or push as-is
      • PyPI       : Push local dist/ or download live .whl/.tar.gz from release
      • Snap Store : Push local dist/snap/ or download live .snap from release
      • WinGet     : Submit first-time onboarding manifest PR or update existing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
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
DIST_DIR   = REPO_ROOT / "dist"

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
    except Exception as exc:  # noqa: BLE001  # noqa: BLE001
        print()
        err(f"Download failed: {exc}")
        return None


def _download_from_release(
    version: str,
    name_pattern: str,
    dest_dir: Path,
    label: str = "asset",
) -> Path:
    """Download a GitHub Release asset whose filename matches *name_pattern*.

    *name_pattern* is a substring the asset filename must contain
    (e.g. ".snap", ".whl", ".tar.gz", ".nupkg", "-setup.exe").
    The file is saved into *dest_dir* and its Path is returned.
    """
    tag = f"v{version}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    info(f"Querying GitHub Release {tag} for {label} ({name_pattern})…")

    filename = None
    download_url = None
    total_size = 0

    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "kport-publish-script/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("WINGET_GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        assets = data.get("assets", [])
        matches = [a for a in assets if name_pattern in a["name"]]
        if not matches:
            available = ", ".join(a["name"] for a in assets) or "(none)"
            err(f"No release asset matching '{name_pattern}' found in release {tag}.")
            info(f"Available assets in {tag}: {available}")
            raise FileNotFoundError(f"Asset matching '{name_pattern}' not found in release {tag}")

        if len(matches) > 1:
            exact = [a for a in matches if version in a["name"]]
            matches = exact if exact else matches

        asset = matches[0]
        filename = asset["name"]
        download_url = asset["browser_download_url"]
        total_size = asset.get("size", 0)
    except Exception as exc:  # noqa: BLE001
        warn(f"GitHub API metadata request note: {exc}. Using direct release URL fallback…")
        if name_pattern == ".snap":
            filename = f"kport_{version}_amd64.snap"
        elif name_pattern == ".whl":
            filename = f"kport-{version}-py3-none-any.whl"
        elif name_pattern == ".tar.gz":
            filename = f"kport-{version}.tar.gz"
        elif name_pattern == ".nupkg":
            filename = f"kport.{version}.nupkg"
        elif "-setup.exe" in name_pattern:
            filename = f"kport-{version}-setup.exe"
        else:
            filename = f"kport-{version}{name_pattern}"
        download_url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{filename}"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / filename

    info(f"Downloading {filename} from {download_url}…")
    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "kport-publish-script/1.0"},
        )
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
                total_kb = f"/{total // 1024:,}" if total else ""
                print(f"\r  Downloading … {pct:3d}% ({downloaded // 1024:,}{total_kb} KB)", end="", flush=True)
        print()
        ok(f"Downloaded {label}: {dest_file.name} ({dest_file.stat().st_size // 1024:,} KB)")
        return dest_file
    except Exception as exc:
        print()
        err(f"Download failed for {label}: {exc}")
        raise


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


def publish_chocolatey(version: str, dry_run: bool, check: bool, from_release: bool = False) -> bool:
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

    if from_release:
        info(f"Rebuilding Chocolatey package using live GitHub release checksum ({github_url})…")
        checksum = _sha256_url(github_url)
        if not checksum:
            err("Could not download/hash installer from GitHub. Ensure release asset exists.")
            return False
        ok(f"SHA-256: {checksum}")
        if not _choco_rebuild(version, ["--checksum", checksum], dry_run):
            return False
        nupkg = _find_nupkg()
        if not nupkg:
            err("Rebuild failed — no .nupkg produced.")
            return False
        return _choco_push(nupkg, api_key, dry_run)

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

    if choice == "1":
        nupkg = _find_nupkg()
        if not nupkg:
            err("No .nupkg found in dist/choco/")
            warn("Build one first: python build_packages.py --choco")
            return False
        info(f"Pushing: {nupkg.name}")
        return _choco_push(nupkg, api_key, dry_run)

    elif choice == "2":
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

def _twine_upload(files: list[Path], test_pypi: bool = False, dry_run: bool = False) -> bool:
    """Run twine upload on given file list."""
    twine = shutil.which("twine")
    if not twine:
        # Check python -m twine
        res = subprocess.run([sys.executable, "-m", "twine", "--version"], capture_output=True, check=False)
        if res.returncode != 0:
            err("twine not found. Install with: pip install twine")
            return False
        base_cmd = [sys.executable, "-m", "twine", "upload"]
    else:
        base_cmd = ["twine", "upload"]

    cmd = list(base_cmd)
    if test_pypi:
        cmd.extend(["--repository", "testpypi"])
    cmd.extend([str(f) for f in files])

    target_name = "TestPyPI" if test_pypi else "Production PyPI"
    if dry_run:
        print("\n  [DRY-RUN] Would execute:")
        print(f"  $ {' '.join(cmd)}")
        return True

    info(f"Uploading {len(files)} file(s) to {target_name}…")
    print(f"  $ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return res.returncode == 0


def publish_pypi(
    version: str,
    dry_run: bool,
    check: bool,
    from_release: bool = False,
    test_pypi: bool = False,
) -> bool:
    section(f"Publishing to {'TestPyPI' if test_pypi else 'PyPI'}")

    # Check
    has_twine = shutil.which("twine") is not None or subprocess.run([sys.executable, "-m", "twine", "--version"], capture_output=True, check=False).returncode == 0
    if check:
        if has_twine:
            ok("twine is installed")
        else:
            err("twine is NOT installed — install: pip install twine")
        dist_files = list(DIST_DIR.glob("*.whl")) + list(DIST_DIR.glob("*.tar.gz"))
        if dist_files:
            ok(f"Local packages found in dist/: {[f.name for f in dist_files]}")
        else:
            info("No local packages in dist/ (can be downloaded from GitHub Release via --from-release)")
        return has_twine

    if not has_twine:
        err("twine is required to publish to PyPI. Install: pip install twine")
        return False

    upload_files: list[Path] = []

    if from_release:
        info(f"Downloading .whl and .tar.gz from GitHub Release v{version}…")
        pypi_dir = DIST_DIR / "pypi-from-release"
        try:
            whl = _download_from_release(version, ".whl", pypi_dir, label="Python wheel (.whl)")
            sdist = _download_from_release(version, ".tar.gz", pypi_dir, label="Source distribution (.tar.gz)")
            upload_files = [whl, sdist]
        except Exception as exc:  # noqa: BLE001
            err(f"Failed to fetch PyPI packages from GitHub Release: {exc}")
            return False
    elif sys.stdin.isatty():
        print()
        print("─" * 50)
        print("  --- PyPI Package Publish Options ---")
        print("  Choose an action:")
        print("    [1] Push existing dist/*.whl and dist/*.tar.gz (default)")
        print("    [2] Download artifacts (.whl + .tar.gz) from live GitHub Release and publish")
        print("    [3] Publish custom wheel / sdist file")
        print("─" * 50)
        dist_files = list(DIST_DIR.glob("*.whl")) + list(DIST_DIR.glob("*.tar.gz"))
        if dist_files:
            info(f"Found local artifacts: {[f.name for f in dist_files]}")
        else:
            warn("No local wheel or sdist found in dist/")

        try:
            raw = input("  Enter choice [1-3] (default 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = "1"

        choice = raw if raw in ("1", "2", "3") else "1"

        if choice == "1":
            upload_files = [f for f in dist_files if f.is_file()]
            if not upload_files:
                err("No .whl or .tar.gz found in dist/. Build with: python -m build")
                return False
        elif choice == "2":
            pypi_dir = DIST_DIR / "pypi-from-release"
            try:
                whl = _download_from_release(version, ".whl", pypi_dir, label="Python wheel (.whl)")
                sdist = _download_from_release(version, ".tar.gz", pypi_dir, label="Source distribution (.tar.gz)")
                upload_files = [whl, sdist]
            except Exception as exc:  # noqa: BLE001
                err(f"Failed to download release artifacts: {exc}")
                return False
        else:  # choice == "3"
            try:
                path_str = input("  Enter path to package file (.whl or .tar.gz): ").strip().strip('"')
            except (EOFError, KeyboardInterrupt):
                path_str = ""
            p = Path(path_str)
            if not p.exists():
                err(f"File not found: {p}")
                return False
            upload_files = [p]
    else:
        # CI or non-interactive without --from-release: look in dist/
        upload_files = list(DIST_DIR.glob("*.whl")) + list(DIST_DIR.glob("*.tar.gz"))
        if not upload_files:
            err("No packages found in dist/. Build with 'python -m build' or use '--from-release'.")
            return False

    return _twine_upload(upload_files, test_pypi=test_pypi, dry_run=dry_run)


# ── Snap ──────────────────────────────────────────────────────────────────────

def _snapcraft_upload(snap_file: Path, channel: str = "stable", dry_run: bool = False) -> bool:
    """Run snapcraft upload on given snap file."""
    snapcraft = shutil.which("snapcraft")
    if not snapcraft:
        err("snapcraft CLI not found. Install with: sudo snap install snapcraft --classic")
        return False

    cmd = ["snapcraft", "upload", str(snap_file), "--release", channel]
    if dry_run:
        print("\n  [DRY-RUN] Would execute:")
        print(f"  $ {' '.join(cmd)}")
        return True

    info(f"Uploading {snap_file.name} to Snap Store ({channel} channel)…")
    print(f"  $ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return res.returncode == 0


def publish_snap(
    version: str,
    dry_run: bool,
    check: bool,
    from_release: bool = False,
    channel: str = "stable",
) -> bool:
    section("Publishing to Snap Store")

    has_snapcraft = shutil.which("snapcraft") is not None
    if check:
        if has_snapcraft:
            ok("snapcraft CLI found")
        else:
            err("snapcraft CLI NOT found — install: sudo snap install snapcraft --classic")
        snap_files = list((DIST_DIR / "snap").glob("*.snap"))
        if snap_files:
            ok(f"Local snap package found: {[f.name for f in snap_files]}")
        else:
            info("No local .snap in dist/snap/ (can be downloaded from GitHub Release via --from-release)")
        return has_snapcraft

    if not has_snapcraft:
        err("snapcraft CLI not found. Install: sudo snap install snapcraft --classic")
        return False

    snap_file: Path | None = None

    if from_release:
        info(f"Downloading .snap package from GitHub Release v{version}…")
        snap_dir = DIST_DIR / "snap-from-release"
        try:
            snap_file = _download_from_release(version, ".snap", snap_dir, label="Snap package (.snap)")
        except Exception as exc:  # noqa: BLE001
            err(f"Failed to fetch .snap from GitHub Release: {exc}")
            return False
    elif sys.stdin.isatty():
        print()
        print("─" * 50)
        print("  --- Snap Store Publish Options ---")
        print("  Choose an action:")
        print("    [1] Push existing dist/snap/*.snap (default)")
        print("    [2] Download .snap from live GitHub Release and publish")
        print("    [3] Publish custom .snap file")
        print("─" * 50)
        local_snaps = sorted((DIST_DIR / "snap").glob("*.snap"))
        if local_snaps:
            info(f"Found local snap: {local_snaps[-1].name}")
        else:
            warn("No local .snap package found in dist/snap/")

        try:
            raw = input("  Enter choice [1-3] (default 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = "1"

        choice = raw if raw in ("1", "2", "3") else "1"

        if choice == "1":
            if not local_snaps:
                err("No .snap file found in dist/snap/. Build with: python scripts/build_snap.py")
                return False
            snap_file = local_snaps[-1]
        elif choice == "2":
            snap_dir = DIST_DIR / "snap-from-release"
            try:
                snap_file = _download_from_release(version, ".snap", snap_dir, label="Snap package (.snap)")
            except Exception as exc:  # noqa: BLE001
                err(f"Failed to download release .snap: {exc}")
                return False
        else:  # choice == "3"
            try:
                path_str = input("  Enter path to .snap file: ").strip().strip('"')
            except (EOFError, KeyboardInterrupt):
                path_str = ""
            p = Path(path_str)
            if not p.exists():
                err(f"File not found: {p}")
                return False
            snap_file = p
    else:
        # CI or non-interactive:
        local_snaps = sorted((DIST_DIR / "snap").glob("*.snap"))
        if not local_snaps:
            err("No .snap package found in dist/snap/. Use '--from-release' to fetch from GitHub.")
            return False
        snap_file = local_snaps[-1]

    return _snapcraft_upload(snap_file, channel=channel, dry_run=dry_run)


# ── WinGet ────────────────────────────────────────────────────────────────────

def publish_winget(
    version: str,
    dry_run: bool,
    check: bool,
    token: str | None = None,
    yes: bool = False,
    first_time: bool = False,
    manifest_only: bool = False,
    installer: str | None = None,
) -> bool:
    """Delegate to publish_winget.py."""
    args = ["--version", version]
    if check:
        args.append("--check")
    if dry_run:
        args.append("--dry-run")
    if yes:
        args.append("--yes")
    if first_time:
        args.append("--first-time")
    if manifest_only:
        args.append("--manifest-only")
    if token:
        args.extend(["--token", token])
    if installer:
        args.extend(["--installer", installer])
    if not (check or manifest_only):
        args.append("--publish")

    return _run_script("publish_winget.py", args)


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: dict[str, bool | None]) -> None:
    print()
    print(_c("═" * 60, "95;1"))
    print(_c("  Publish Summary", "95;1"))
    print(_c("═" * 60, "95;1"))
    for name, result in results.items():
        if result is None:
            print(f"  {'─':2} {name:20} (skipped)")
        elif result:
            ok(f"{name:20} SUCCESS")
        else:
            err(f"{name:20} FAILED")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    default_version = read_version()

    parser = argparse.ArgumentParser(
        prog="publish_packages.py",
        description="Publish kport packages to distribution channels (PyPI, Chocolatey, Snap, WinGet)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    tgroup = parser.add_argument_group("targets")
    tgroup.add_argument(
        "--all", action="store_true",
        help="Publish to all configured channels (PyPI + Chocolatey + Snap + WinGet)",
    )
    tgroup.add_argument(
        "--pypi", action="store_true",
        help="Publish to PyPI (requires twine and credentials)",
    )
    tgroup.add_argument(
        "--chocolatey", "--choco", dest="chocolatey", action="store_true",
        help="Publish to Chocolatey Community (requires CHOCO_API_KEY)",
    )
    tgroup.add_argument(
        "--snap", action="store_true",
        help="Publish to Snap Store (requires snapcraft login)",
    )
    tgroup.add_argument(
        "--winget", action="store_true",
        help="Submit or update package in Windows Package Manager (WinGet)",
    )

    ogroup = parser.add_argument_group("options")
    ogroup.add_argument(
        "--from-release", action="store_true",
        help="Download package artifacts from live GitHub Release instead of local dist/",
    )
    ogroup.add_argument(
        "--winget-version",
        help="Explicit version for WinGet package (default: project version)",
    )
    ogroup.add_argument(
        "--test-pypi", action="store_true",
        help="Upload to TestPyPI instead of production PyPI",
    )
    ogroup.add_argument(
        "--channel", default="stable", choices=["stable", "candidate", "edge"],
        help="Snap store release channel (default: stable)",
    )
    ogroup.add_argument(
        "--token",
        help="GitHub Personal Access Token for WinGet PR submission",
    )
    ogroup.add_argument(
        "--first-time", action="store_true",
        help="Force initial WinGet package onboarding PR",
    )
    ogroup.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip interactive confirmation prompts",
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
        "--version", default=default_version,
        help=f"Override version string (default: {default_version})",
    )

    args = parser.parse_args()
    version = args.version

    if args.all:
        args.pypi        = True
        args.chocolatey  = True
        args.snap        = True
        args.winget      = True

    any_target = any([args.pypi, args.chocolatey, args.snap, args.winget])
    if not any_target:
        parser.print_help()
        print()
        warn("No target selected. Use --all or one of: --pypi  --chocolatey  --snap  --winget")
        sys.exit(0)

    print()
    print(_c("═" * 60, "95;1"))
    print(_c(f"  🚀 kport Package Publisher  v{version}", "95;1"))
    print(_c("═" * 60, "95;1"))
    if args.dry_run:
        warn("DRY-RUN mode — nothing will actually be published")
    if args.check:
        info("CHECK mode — verifying prerequisites only")
    if args.from_release:
        info("SOURCE mode — downloading artifacts from live GitHub Releases")

    results: dict[str, bool | None] = {}

    if args.pypi:
        results["PyPI"] = publish_pypi(
            version,
            dry_run=args.dry_run,
            check=args.check,
            from_release=args.from_release,
            test_pypi=args.test_pypi,
        )

    if args.chocolatey:
        results["Chocolatey"] = publish_chocolatey(
            version,
            dry_run=args.dry_run,
            check=args.check,
            from_release=args.from_release,
        )

    if args.snap:
        results["Snap Store"] = publish_snap(
            version,
            dry_run=args.dry_run,
            check=args.check,
            from_release=args.from_release,
            channel=args.channel,
        )

    if args.winget:
        wg_version = args.winget_version or version
        results["WinGet"] = publish_winget(
            wg_version,
            dry_run=args.dry_run,
            check=args.check,
            token=args.token,
            yes=args.yes,
            first_time=args.first_time,
        )

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
    except Exception as exc:  # noqa: BLE001  # noqa: BLE001
        err(f"Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
