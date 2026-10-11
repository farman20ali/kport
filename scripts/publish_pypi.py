#!/usr/bin/env python3
"""
Script to help publish kport to PyPI
"""
import io
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def main():
    """Main function"""
    _configure_stdio()
    print("="*60)
    print("🚀 kport Publishing Tool")
    print("="*60)
    
    print("\nWhat would you like to do?")
    print("1. Check requirements and build package")
    print("2. Build and upload to Test PyPI (recommended first)")
    print("3. Build and upload to PyPI (production)")
    print("4. Show GitHub release instructions")
    print("5. Do everything (build + test + production)")
    print("0. Exit")
    
    choice = input("\nEnter your choice (0-5): ").strip()
    
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
    
    if choice not in ['0', '1', '2', '3', '4', '5']:
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
