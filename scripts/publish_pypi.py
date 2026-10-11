#!/usr/bin/env python3
"""
Script to help publish kport to PyPI
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPO = "farman20ali/kport"


def _configure_stdio() -> None:
    """Use UTF-8 on Windows so emoji/symbols in CLI output do not crash."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, io.UnsupportedOperation):
            pass


def run_command(cmd, description):
    """Run a shell command and print status"""
    print(f"\n{'='*60}")
    print(f"🔄 {description}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, shell=True, cwd=str(REPO_ROOT), check=False)
    if result.returncode != 0:
        print(f"❌ Failed: {description}")
        sys.exit(1)
    print(f"✅ Success: {description}")
    return result


def check_requirements():
    """Check if required packages are installed"""
    print("\n📦 Checking required packages...")
    
    required = ['build', 'twine']
    missing = []
    
    for package in required:
        result = subprocess.run(
            f"{sys.executable} -m pip show {package}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            missing.append(package)
    
    if missing:
        print(f"⚠️  Missing packages: {', '.join(missing)}")
        install = input("Install missing packages? (y/N): ")
        if install.lower() in ['y', 'yes']:
            run_command(
                f"{sys.executable} -m pip install {' '.join(missing)}",
                f"Installing {', '.join(missing)}"
            )
        else:
            print("❌ Cannot proceed without required packages")
            sys.exit(1)
    else:
        print("✅ All required packages installed")


def clean_build():
    """Clean previous build artifacts"""
    print("\n🧹 Cleaning previous builds...")
    dirs = ['dist', 'build', 'kport.egg-info']
    for d in dirs:
        path = REPO_ROOT / d
        if path.exists():
            if sys.platform == 'win32':
                subprocess.run(f'rmdir /s /q "{path}"', shell=True, check=False)
            else:
                subprocess.run(f'rm -rf "{path}"', shell=True, check=False)
    print("✅ Cleaned build directories")


def build_package():
    """Build the package"""
    run_command(f"{sys.executable} -m build", "Building package")


def upload_to_test_pypi():
    """Upload to Test PyPI first"""
    print("\n📤 Uploading to Test PyPI (test.pypi.org)...")
    print("Note: You need a Test PyPI account at https://test.pypi.org/")
    proceed = input("Proceed with Test PyPI upload? (y/N): ")
    
    if proceed.lower() in ['y', 'yes']:
        run_command(
            f"{sys.executable} -m twine upload --repository testpypi dist/*",
            "Uploading to Test PyPI"
        )
        print("\n✅ Upload successful!")
        print("Test installation with:")
        print("  pip install --index-url https://test.pypi.org/simple/ kport")


def upload_to_pypi():
    """Upload to production PyPI"""
    print("\n📤 Uploading to PyPI (pypi.org)...")
    print("⚠️  WARNING: This will publish to production PyPI!")
    print("Note: You need a PyPI account at https://pypi.org/")
    proceed = input("Proceed with PyPI upload? (y/N): ")
    
    if proceed.lower() in ['y', 'yes']:
        run_command(
            f"{sys.executable} -m twine upload dist/*",
            "Uploading to PyPI"
        )
        print("\n🎉 Successfully published to PyPI!")
        print("Users can now install with:")
        print("  pip install kport")


def create_github_release():
    """Instructions for creating GitHub release"""
    print("\n📦 Creating GitHub Release")
    print("="*60)
    print("To create a GitHub release:")
    print("1. Push your code to GitHub")
    print("2. Go to: https://github.com/farman20ali/kport/releases/new")
    print("3. Create a new tag (e.g., v1.0.0)")
    print("4. Add release notes")
    print("5. Attach dist files (optional)")
    print("\nUsers can then install with:")
    print("  pip install git+https://github.com/farman20ali/kport.git")


def check_license_metadata():
    """Verify that PyPI license expressions and LICENSE files match Apache-2.0"""
    print("\n⚖️ Checking license metadata...")
    pyproject = REPO_ROOT / "pyproject.toml"
    license_file = REPO_ROOT / "LICENSE"
    
    if not pyproject.exists():
        print("❌ pyproject.toml not found")
        sys.exit(1)
        
    pyproject_text = pyproject.read_text(encoding="utf-8")

    license_pattern = re.compile(
        r'^\s*license\s*=\s*(?:"Apache-2\.0"|\{[^\n]*text\s*=\s*"Apache-2\.0"[^\n]*\}|\{[^\n]*file\s*=\s*"LICENSE"[^\n]*\})',
        re.MULTILINE,
    )

    if not license_pattern.search(pyproject_text):
        print("❌ pyproject.toml does not specify Apache-2.0 license correctly under project metadata")
        sys.exit(1)
        
    if not license_file.exists():
        print("❌ LICENSE file not found at repo root")
        sys.exit(1)
        
    license_text = license_file.read_text(encoding="utf-8")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        print("❌ LICENSE file does not appear to contain Apache License 2.0 text")
        sys.exit(1)
        
    print("✅ License metadata check passed")


def download_pypi_from_release(version: str, dest_dir: Path) -> list[Path]:
    """Download .whl and .tar.gz from GitHub release."""
    tag = f"v{version}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    print(f"\n📦 Querying GitHub Release {tag} for PyPI packages (.whl, .tar.gz)…")
    
    whl_name = f"kport-{version}-py3-none-any.whl"
    sdist_name = f"kport-{version}.tar.gz"
    whl_url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{whl_name}"
    sdist_url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{sdist_name}"

    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "kport-pypi-publish/1.0", "Accept": "application/vnd.github+json"})
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        assets = data.get("assets", [])
        for a in assets:
            if a["name"].endswith(".whl"):
                whl_name = a["name"]
                whl_url = a["browser_download_url"]
            elif a["name"].endswith(".tar.gz"):
                sdist_name = a["name"]
                sdist_url = a["browser_download_url"]
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  GitHub API note: {exc}. Using direct release URL fallback.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    for fname, furl in [(whl_name, whl_url), (sdist_name, sdist_url)]:
        dest_file = dest_dir / fname
        print(f"  Downloading {fname}…")
        req = urllib.request.Request(furl, headers={"User-Agent": "kport-pypi-publish/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest_file, "wb") as fh:
            while chunk := resp.read(65536):
                fh.write(chunk)
        print(f"  ✅ Saved: {dest_file.name} ({dest_file.stat().st_size // 1024:,} KB)")
        downloaded_files.append(dest_file)

    return downloaded_files


def main():
    """Main function"""
    _configure_stdio()

    parser = argparse.ArgumentParser(description="Publish kport to PyPI / TestPyPI")
    parser.add_argument("--check", action="store_true", help="Check license and requirements only")
    parser.add_argument("--publish", action="store_true", help="Upload packages to PyPI")
    parser.add_argument("--test", "--test-pypi", dest="test_pypi", action="store_true", help="Upload to TestPyPI")
    parser.add_argument("--from-release", action="store_true", help="Download artifacts from live GitHub release")
    parser.add_argument("--version", help="Override version when fetching from release")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    parser.add_argument("--files", nargs="*", help="Specific package files to upload")

    args = parser.parse_args()

    # CLI mode
    if any([args.check, args.publish, args.from_release, args.dry_run]):
        check_license_metadata()
        if args.check:
            check_requirements()
            print("✅ Prerequisites check passed.")
            return

        if args.from_release:
            v = args.version or "5.1.0"
            dl_files = download_pypi_from_release(v, Path("dist/pypi-from-release"))
            file_args = " ".join(str(f) for f in dl_files)
        elif args.files:
            file_args = " ".join(args.files)
        else:
            file_args = "dist/*"

        repo_flag = "--repository testpypi " if args.test_pypi else ""
        cmd = f"{sys.executable} -m twine upload {repo_flag}{file_args}"

        if args.dry_run:
            print(f"[DRY-RUN] Would execute: {cmd}")
            return

        run_command(cmd, f"Uploading to {'TestPyPI' if args.test_pypi else 'PyPI'}")
        return

    # Interactive mode
    print("="*60)
    print("🚀 kport Publishing Tool")
    print("="*60)
    
    print("\nWhat would you like to do?")
    print("1. Check requirements and build package")
    print("2. Build and upload to Test PyPI (recommended first)")
    print("3. Build and upload to PyPI (production)")
    print("4. Show GitHub release instructions")
    print("5. Do everything (build + test + production)")
    print("6. Download from live GitHub Release and upload to PyPI")
    print("0. Exit")
    
    choice = input("\nEnter your choice (0-6): ").strip()
    
    if choice == '0':
        print("👋 Goodbye!")
        sys.exit(0)
    
    if choice in ['1', '2', '3', '5']:
        check_license_metadata()
        check_requirements()
        clean_build()
        build_package()
    
    if choice in ['2', '5']:
        upload_to_test_pypi()
    
    if choice in ['3', '5']:
        if choice == '5':
            print("\n⚠️  Test PyPI upload completed.")
            proceed = input("Continue to production PyPI? (y/N): ")
            if proceed.lower() not in ['y', 'yes']:
                print("Stopped before production upload")
                sys.exit(0)
        upload_to_pypi()
    
    if choice == '4':
        create_github_release()

    if choice == '6':
        check_license_metadata()
        ver = input("Enter release version (e.g. 5.1.0): ").strip() or "5.1.0"
        dl = download_pypi_from_release(ver, Path("dist/pypi-from-release"))
        files_str = " ".join(str(f) for f in dl)
        test_q = input("Upload to TestPyPI first? [y/N]: ").strip().lower()
        repo_flag = "--repository testpypi " if test_q in ['y', 'yes'] else ""
        run_command(f"{sys.executable} -m twine upload {repo_flag}{files_str}", "Uploading downloaded release packages")
    
    if choice not in ['0', '1', '2', '3', '4', '5', '6']:
        print("❌ Invalid choice")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("✅ All done!")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user")
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error: {e}")
        sys.exit(1)
