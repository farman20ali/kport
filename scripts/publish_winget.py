#!/usr/bin/env python3
"""Windows Package Manager (WinGet) publisher for KPort.

Handles initial submission and updates to microsoft/winget-pkgs via wingetcreate.

Usage (automated / CI):
    python scripts/publish_winget.py --check                          # Verify prerequisites & tokens
    python scripts/publish_winget.py --publish                        # Submit or update package
    python scripts/publish_winget.py --publish --version 5.1.0        # Publish specific version
    python scripts/publish_winget.py --publish --first-time           # Initial package onboarding PR
    python scripts/publish_winget.py --manifest-only                  # Generate manifests without submitting
    python scripts/publish_winget.py --validate                       # Validate generated manifests
    python scripts/publish_winget.py --dry-run                        # Preview execution without submitting

Interactive (recommended for manual submission):
    python scripts/publish_winget.py                                  # Interactive menu

Requirements:
    - wingetcreate CLI (install: winget install Microsoft.WingetCreate)
    - WINGET_GITHUB_TOKEN environment variable (or GH_TOKEN / GITHUB_TOKEN)
      GitHub Personal Access Token with 'public_repo' scope to fork microsoft/winget-pkgs and open PR.
"""

from __future__ import annotations

import argparse
import hashlib
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

REPO_ROOT   = Path(__file__).resolve().parents[1]
DIST_DIR    = REPO_ROOT / "dist"
DIST_WINGET = DIST_DIR / "winget"
WINGET_DIR  = REPO_ROOT / "packaging" / "winget"

PACKAGE_ID  = "farman20ali.kport"
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


def release_installer_url(version: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/kport-{version}-setup.exe"


def get_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.strip()
    for var in ("WINGET_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    # Fallback to gh auth token if gh CLI is authenticated
    if shutil.which("gh"):
        try:
            gh_token = subprocess.check_output(
                ["gh", "auth", "token"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if gh_token:
                return gh_token
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass
    return None


def print_token_guide() -> None:
    print()
    print(_c("═" * 68, "93;1"))
    print(_c("  🔑 WinGet GitHub Token (WINGET_GITHUB_TOKEN) Setup Guide", "93;1"))
    print(_c("═" * 68, "93;1"))
    print("""
  wingetcreate needs a GitHub Personal Access Token (classic PAT) to:
    1. Fork 'microsoft/winget-pkgs' to your account
    2. Commit the manifest files
    3. Open a Pull Request on microsoft/winget-pkgs

  Step-by-step setup:
    1. Open GitHub Tokens page:
       👉 https://github.com/settings/tokens/new

    2. Note: "winget submission token"
       Expiration: 90 days (or your preference)
       Scopes: Check [x] 'public_repo'  (Access public repositories)

    3. Click 'Generate token' and copy the token (starts with ghp_...)

    4. Set the environment variable:
       • PowerShell:
           $env:WINGET_GITHUB_TOKEN = "ghp_your_token_here"

       • Command Prompt (cmd):
           set WINGET_GITHUB_TOKEN=ghp_your_token_here

       • Linux / macOS / Bash:
           export WINGET_GITHUB_TOKEN="ghp_your_token_here"
""")
    print(_c("═" * 68, "93;1"))
    print()


def print_first_time_guide(version: str) -> None:
    url = release_installer_url(version)
    print()
    print(_c("═" * 68, "96;1"))
    print(_c("  🚀 WinGet First-Time vs Next-Time Publishing Guide", "96;1"))
    print(_c("═" * 68, "96;1"))
    print(f"""
  FIRST-TIME PUBLISHING (v{version}):
  ──────────────────────────────────
  Since '{PACKAGE_ID}' is NOT yet in the official 'microsoft/winget-pkgs' repo,
  'wingetcreate update' will fail because the package doesn't exist there yet.

  For first-time onboarding, use one of:
    Option A (Recommended - Auto-generate and submit valid manifests):
      python scripts/publish_winget.py --publish --first-time

      Or manually submit the generated manifest folder:
      wingetcreate submit dist/winget/manifests/f/farman20ali/kport/{version} --token $env:WINGET_GITHUB_TOKEN

    Option B (Interactive wizard from live installer URL):
      wingetcreate new "{url}" --token $env:WINGET_GITHUB_TOKEN

  NEXT-TIME PUBLISHING (Subsequent releases e.g., v5.2.0):
  ────────────────────────────────────────────────────────
  Once your first PR is approved and merged into microsoft/winget-pkgs,
  subsequent releases are 100% automated:
      python manage.py publish --winget
      # or: python scripts/publish_packages.py --winget
      # or: wingetcreate update {PACKAGE_ID} --version <new-v> --urls "<new-url>" --submit --token $env:WINGET_GITHUB_TOKEN
""")
    print(_c("═" * 68, "96;1"))
    print()


def check_prerequisites() -> dict[str, bool]:
    has_wingetcreate = shutil.which("wingetcreate") is not None
    has_winget = shutil.which("winget") is not None
    has_token = get_token() is not None
    return {
        "wingetcreate": has_wingetcreate,
        "winget": has_winget,
        "token": has_token,
    }


def print_check_results(version: str) -> bool:
    section("Checking WinGet Prerequisites")
    checks = check_prerequisites()
    all_ok = True

    if checks["wingetcreate"]:
        ok("wingetcreate CLI found")
    else:
        err("wingetcreate CLI NOT found")
        print("    Install via winget: winget install Microsoft.WingetCreate")
        print("    Or download from:   https://github.com/microsoft/winget-create/releases")
        all_ok = False

    if checks["winget"]:
        ok("winget CLI found (for manifest schema validation)")
    else:
        warn("winget CLI not found (manifest validation will be skipped)")

    if checks["token"]:
        ok("WINGET_GITHUB_TOKEN (or GH_TOKEN / GITHUB_TOKEN) is set")
    else:
        warn("WINGET_GITHUB_TOKEN is NOT set — PR submission will require interactive login")
        print_token_guide()
        all_ok = False

    installer = find_local_installer(version)
    if installer:
        ok(f"Local installer found: {installer.name}")
    else:
        info(f"No local installer found in dist/win/ — will use live GitHub Release asset (v{version})")

    return all_ok


def find_local_installer(version: str) -> Path | None:
    dist_win = REPO_ROOT / "dist" / "win"
    matches = sorted(dist_win.glob(f"kport-{version}-setup.exe"))
    if not matches:
        matches = sorted(dist_win.glob("kport-*-setup.exe"))
    return matches[-1] if matches else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_from_url(url: str) -> str | None:
    """Download installer from release URL and compute SHA-256."""
    try:
        info(f"Fetching and hashing release asset: {url}")
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
                    print(f"\r  Downloading … {pct:3d}% ({downloaded // 1024:,}/{total // 1024:,} KB)", end="", flush=True)
        print()
        return digest.hexdigest().upper()
    except Exception as exc:  # noqa: BLE001
        print()
        err(f"Download from GitHub Release failed: {exc}")
        return None


def resolve_sha256(version: str, installer_path: str | None = None, dry_run: bool = False) -> str | None:
    if installer_path:
        path = Path(installer_path)
        if path.exists():
            h = sha256_file(path)
            ok(f"Computed SHA256 from custom installer ({path.name}): {h}")
            return h
        err(f"Specified installer not found: {installer_path}")
        return None

    local_inst = find_local_installer(version)
    if local_inst and local_inst.exists():
        h = sha256_file(local_inst)
        ok(f"Computed SHA256 from local installer ({local_inst.name}): {h}")
        return h

    # Try live release asset
    url = release_installer_url(version)
    live_sha = sha256_from_url(url)
    if live_sha:
        ok(f"Computed SHA256 from live release asset: {live_sha}")
        return live_sha

    if dry_run:
        warn("DRY RUN — using placeholder SHA-256")
        return "0" * 64

    return None


# ── Manifest Generation & Validation ──────────────────────────────────────────

def render_manifests(version: str, sha256_val: str) -> tuple[Path, Path]:
    """Render singleton and multi-file WinGet manifests into dist/winget/.

    Returns (singleton_file_path, multi_manifest_directory_path).
    """
    DIST_WINGET.mkdir(parents=True, exist_ok=True)

    # 1. Singleton manifest: dist/winget/farman20ali.kport.yaml
    singleton_template = WINGET_DIR / "kport.yaml.template"
    if not singleton_template.exists():
        raise FileNotFoundError(f"Missing template: {singleton_template}")
    singleton_content = (
        singleton_template.read_text(encoding="utf-8")
        .replace("{version}", version)
        .replace("{sha256}", sha256_val)
    )
    singleton_file = DIST_WINGET / f"{PACKAGE_ID}.yaml"
    singleton_file.write_text(singleton_content, encoding="utf-8")

    # 2. Multi-file manifests: dist/winget/manifests/f/farman20ali/kport/<version>/
    multi_dir = DIST_WINGET / "manifests" / "f" / "farman20ali" / "kport" / version
    multi_dir.mkdir(parents=True, exist_ok=True)

    # Also write to repo source under packaging/winget/
    repo_multi_dir = WINGET_DIR / "manifests" / "f" / "farman20ali" / "kport" / version
    repo_multi_dir.mkdir(parents=True, exist_ok=True)
    repo_singleton_file = WINGET_DIR / f"{PACKAGE_ID}.yaml"
    repo_singleton_file.write_text(singleton_content, encoding="utf-8")

    templates = [
        ("farman20ali.kport.yaml.template", f"{PACKAGE_ID}.yaml"),
        ("farman20ali.kport.installer.yaml.template", f"{PACKAGE_ID}.installer.yaml"),
        ("farman20ali.kport.locale.en-US.yaml.template", f"{PACKAGE_ID}.locale.en-US.yaml"),
    ]

    for tmpl_name, out_name in templates:
        tmpl_path = WINGET_DIR / tmpl_name
        if tmpl_path.exists():
            content = (
                tmpl_path.read_text(encoding="utf-8")
                .replace("{version}", version)
                .replace("{sha256}", sha256_val)
            )
            (multi_dir / out_name).write_text(content, encoding="utf-8")
            (repo_multi_dir / out_name).write_text(content, encoding="utf-8")

    return singleton_file, multi_dir


def validate_manifests(path_to_validate: Path) -> bool:
    """Validate manifests using 'winget validate' if available."""
    if not shutil.which("winget"):
        info("winget CLI not found — skipping local schema validation")
        return True

    info(f"Validating manifest schema via 'winget validate' on: {path_to_validate}")
    cmd = ["winget", "validate", str(path_to_validate)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0:
        ok("WinGet manifest schema validation succeeded!")
        return True

    err(f"WinGet manifest validation failed (exit {res.returncode}):")
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    return False


# ── Submission Execution ──────────────────────────────────────────────────────

def check_package_exists_in_winget(package_id: str) -> bool:
    """Check if the package already exists in microsoft/winget-pkgs."""
    if not shutil.which("wingetcreate"):
        return False
    res = subprocess.run(
        ["wingetcreate", "show", package_id],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def submit_first_time(
    version: str,
    multi_dir: Path,
    token: str | None,
    dry_run: bool = False,
) -> bool:
    """Submit initial onboarding PR to microsoft/winget-pkgs."""
    section(f"Submitting WinGet Initial Package PR ({PACKAGE_ID} v{version})")

    if not shutil.which("wingetcreate"):
        err("wingetcreate CLI not found. Install: winget install Microsoft.WingetCreate")
        return False

    cmd = ["wingetcreate", "submit", str(multi_dir)]
    if token:
        cmd.extend(["--token", token])

    if dry_run:
        print("  [DRY-RUN] Would execute:")
        print(f"  $ {' '.join(cmd[:3])} --token ***")
        return True

    print(f"  $ {' '.join(cmd[:3])} --token ***")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if res.returncode == 0:
        ok(f"Successfully submitted WinGet manifest PR for {PACKAGE_ID} v{version}!")
        return True

    err(f"wingetcreate submit failed with exit code {res.returncode}")
    return False


def submit_update(
    version: str,
    token: str | None,
    installer_url: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Submit update PR for an existing package in microsoft/winget-pkgs."""
    section(f"Updating WinGet Package ({PACKAGE_ID} v{version})")

    if not shutil.which("wingetcreate"):
        err("wingetcreate CLI not found. Install: winget install Microsoft.WingetCreate")
        return False

    url = installer_url or release_installer_url(version)
    cmd = [
        "wingetcreate", "update", PACKAGE_ID,
        "--version", version,
        "--urls", url,
        "--submit",
    ]
    if token:
        cmd.extend(["--token", token])

    if dry_run:
        print("  [DRY-RUN] Would execute:")
        print(f"  $ {' '.join(cmd[:7])} --token ***")
        return True

    print(f"  $ {' '.join(cmd[:7])} --token ***")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if res.returncode == 0:
        ok(f"Successfully submitted WinGet update PR for {PACKAGE_ID} v{version}!")
        return True

    err(f"wingetcreate update failed with exit code {res.returncode}")
    return False


def publish_winget(
    version: str,
    token: str | None = None,
    dry_run: bool = False,
    check: bool = False,
    yes: bool = False,
    first_time: bool = False,
    manifest_only: bool = False,
    installer: str | None = None,
) -> bool:
    """Master WinGet publishing function."""
    auth_token = get_token(token)

    if check:
        return print_check_results(version)

    section(f"WinGet Publication Process (v{version})")
    url = release_installer_url(version)
    print(f"  Package Identifier : {PACKAGE_ID}")
    print(f"  Version            : {version}")
    print(f"  Setup URL          : {url}")
    print(f"  GitHub Token       : {'Configured ✅' if auth_token else 'NOT SET (interactive fallback) ⚠️'}")

    if not auth_token:
        print_token_guide()

    # Resolve SHA-256
    sha256_val = resolve_sha256(version, installer_path=installer, dry_run=dry_run)
    if not sha256_val:
        err("Could not resolve installer SHA-256 checksum.")
        info(f"Make sure {url} is published or local installer exists in dist/win/.")
        return False

    # Render manifests
    singleton_file, multi_dir = render_manifests(version, sha256_val)
    ok(f"Generated singleton manifest  : {singleton_file}")
    ok(f"Generated multi-manifest tree : {multi_dir}")

    # Validate
    validate_manifests(multi_dir)

    if manifest_only:
        ok("Manifests generated and validated. Exiting as --manifest-only was requested.")
        return True

    # Detect if package exists in winget-pkgs or if first-time
    package_exists = check_package_exists_in_winget(PACKAGE_ID) if not first_time else False
    is_first_time = first_time or (not package_exists)

    if is_first_time:
        info("First-time package submission detected (package not yet in microsoft/winget-pkgs).")
    else:
        info("Package already exists in microsoft/winget-pkgs — performing version update.")

    # Confirmation prompt
    if not yes:
        action_desc = "first-time onboarding PR" if is_first_time else "version update PR"
        try:
            confirm = input(f"\n  Proceed with submitting WinGet {action_desc} for v{version}? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return False

        if confirm not in ("y", "yes"):
            warn("Submission cancelled by user.")
            return False

    # Dispatch
    if is_first_time:
        success = submit_first_time(version, multi_dir, auth_token, dry_run=dry_run)
        if not success:
            print_first_time_guide(version)
        return success
    else:
        success = submit_update(version, auth_token, installer_url=url, dry_run=dry_run)
        if not success:
            warn("Update command failed. If this is actually the first time submitting, try --first-time:")
            info(f"  python scripts/publish_winget.py --publish --first-time --version {version}")
        return success


# ── Interactive Menu ──────────────────────────────────────────────────────────

def interactive_menu() -> int:
    version = read_version()
    while True:
        section(f"WinGet Package Manager Publisher (v{version})")
        print("\nChoose an option:")
        print("  1. Auto-publish to WinGet (checks first-time vs update, verifies manifests)")
        print("  2. First-time onboarding submission (generate manifests & submit PR)")
        print("  3. Update existing package (wingetcreate update)")
        print("  4. Generate local manifests only (dist/winget/)")
        print("  5. Validate generated manifests (winget validate)")
        print("  6. Show WINGET_GITHUB_TOKEN guide & troubleshooting")
        print("  7. Check prerequisites & tools")
        print("  0. Exit")

        try:
            choice = input("\nEnter choice [0-7] (default 1): ").strip() or "1"
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Goodbye!")
            return 0

        if choice == "0":
            return 0
        elif choice == "1":
            publish_winget(version)
        elif choice == "2":
            publish_winget(version, first_time=True)
        elif choice == "3":
            publish_winget(version, first_time=False)
        elif choice == "4":
            publish_winget(version, manifest_only=True)
        elif choice == "5":
            sha = resolve_sha256(version)
            if sha:
                _, mdir = render_manifests(version, sha)
                validate_manifests(mdir)
        elif choice == "6":
            print_token_guide()
            print_first_time_guide(version)
        elif choice == "7":
            print_check_results(version)
        else:
            err("Invalid choice.")


# ── CLI Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    default_ver = read_version()
    parser = argparse.ArgumentParser(
        prog="publish_winget.py",
        description="Publish and update kport in Windows Package Manager (WinGet)",
    )
    parser.add_argument("--publish", action="store_true", help="Submit or update package in WinGet")
    parser.add_argument("--check", action="store_true", help="Verify prerequisites and token status")
    parser.add_argument("--dry-run", action="store_true", help="Preview execution without submitting")
    parser.add_argument("--version", default=default_ver, help=f"Package version (default: {default_ver})")
    parser.add_argument("--token", help="GitHub Personal Access Token with public_repo scope")
    parser.add_argument("--first-time", action="store_true", help="Force first-time package submission flow")
    parser.add_argument("--manifest-only", action="store_true", help="Generate local manifests only without submitting")
    parser.add_argument("--validate", action="store_true", help="Run 'winget validate' on generated manifests")
    parser.add_argument("--installer", help="Path to custom setup.exe to compute SHA-256")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    if not any([args.publish, args.check, args.dry_run, args.manifest_only, args.validate]):
        return interactive_menu()

    if args.validate:
        sha = resolve_sha256(args.version, installer_path=args.installer, dry_run=args.dry_run)
        if not sha:
            return 1
        _, mdir = render_manifests(args.version, sha)
        ok_val = validate_manifests(mdir)
        return 0 if ok_val else 1

    success = publish_winget(
        version=args.version,
        token=args.token,
        dry_run=args.dry_run,
        check=args.check,
        yes=args.yes,
        first_time=args.first_time,
        manifest_only=args.manifest_only,
        installer=args.installer,
    )
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n👋 Cancelled")
        sys.exit(0)
