#!/usr/bin/env python3
"""Debian packaging helper for kport.

This script is intentionally simple and interactive (like publish.py).
It helps you:
- check Debian build prerequisites
- build a .deb using dpkg-buildpackage
- show the produced .deb path

Usage:
  python build_deb.py

Notes:
- Building Debian packages is supported on Debian/Ubuntu (or derivatives).
- Installing build deps uses apt-get and requires sudo.
- This repo does NOT commit a debian/ directory. Instead, this script generates
    a minimal debian/ packaging skeleton inside a temporary build directory.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist" / "deb"


def run(cmd: list[str], description: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("\n" + "=" * 60)
    print(f"🔄 {description}")
    print("=" * 60)
    print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd or REPO_ROOT), check=False)
    if proc.returncode != 0:
        print(f"❌ Failed: {description}")
        sys.exit(proc.returncode)
    print(f"✅ Success: {description}")
    return proc


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def is_debian_like() -> bool:
    if platform.system() != "Linux":
        return False
    return Path("/etc/debian_version").exists() or Path("/etc/os-release").read_text(errors="ignore").lower().find("debian") >= 0


def read_project_version() -> str | None:
    """Extract version from pyproject.toml (preferred) or src/kport/__init__.py."""
    for candidate in [
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "src" / "kport" / "__init__.py",
    ]:
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        m = re.search(
            r"""(?:^version\s*=\s*["']|__version__\s*=\s*["'])([^"']+)""",
            text, re.MULTILINE,
        )
        if m:
            return m.group(1).strip()
    return None


def check_layout() -> None:
    required = ["pyproject.toml", "__main__.py"]
    missing = [p for p in required if not (REPO_ROOT / p).exists()]
    if missing:
        print("❌ Missing required project files: " + ", ".join(missing))
        sys.exit(1)


def check_build_tools() -> list[str]:
    missing = []
    for tool in ["dpkg-buildpackage", "dpkg", "fakeroot", "dpkg-checkbuilddeps"]:
        if not command_exists(tool):
            missing.append(tool)
    return missing


def _debhelper_compat_level() -> int:
    """Return a debhelper compat level that should work on this system."""
    # Ubuntu 20.04 ships debhelper 12, so default to 12.
    if not command_exists("dh"):
        return 12
    try:
        out = subprocess.check_output(["dh", "--version"], text=True, stderr=subprocess.STDOUT)
    except Exception:  # noqa: BLE001
        return 12
    m = re.search(r"debhelper\s+version\s+(\d+)", out, re.IGNORECASE)
    if not m:
        return 12
    major = int(m.group(1))
    # compat levels are typically aligned with the debhelper major version.
    return max(12, major)


def _maintainer_identity() -> tuple[str, str]:
    name = os.environ.get("DEBFULLNAME") or os.environ.get("NAME") or "kport builder"
    email = os.environ.get("DEBEMAIL") or os.environ.get("EMAIL") or "builder@localhost"
    return name, email


def _rfc2822_now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")


def generate_debian_skeleton(work_dir: Path) -> str:
    """Generate a minimal debian/ directory inside work_dir.

    Returns the Debian version string.
    """
    debian_dir = work_dir / "debian"
    debian_dir.mkdir(parents=True, exist_ok=True)
    (debian_dir / "source").mkdir(parents=True, exist_ok=True)

    project_version = read_project_version() or "0.0.0"
    deb_version = f"{project_version}-1"
    compat = _debhelper_compat_level()
    maint_name, maint_email = _maintainer_identity()

    control = textwrap.dedent(
        f"""\
        Source: kport
        Section: utils
        Priority: optional
        Maintainer: {maint_name} <{maint_email}>
        Build-Depends: debhelper (>= {min(compat, 12)})
        Standards-Version: 4.5.0
        Homepage: https://github.com/farman20ali/kport
        Rules-Requires-Root: no

        Package: kport
        Architecture: all
        Depends: ${{misc:Depends}}, python3, python3-psutil
        Description: Cross-platform port inspector and killer
         kport helps you list, inspect, and kill processes using ports.
         It can also show Docker port mappings when Docker is installed.
        """
    )

    rules = (
        "#!/usr/bin/make -f\n"
        "\n"
        "%:\n"
        "\tdh $@\n"
        "\n"
        "override_dh_update_autotools_config:\n"
        "\ttrue\n"
        "\n"
        "override_dh_autoreconf:\n"
        "\ttrue\n"
        "\n"
        "override_dh_auto_configure:\n"
        "\ttrue\n"
        "\n"
        "override_dh_auto_clean:\n"
        "\ttrue\n"
        "\n"
        "override_dh_auto_build:\n"
        "\ttrue\n"
        "\n"
        "override_dh_auto_test:\n"
        "\ttrue\n"
        "\n"
        "override_dh_auto_install:\n"
        "\tinstall -D -m 0755 __main__.py debian/kport/usr/bin/kport\n"
        "\tmkdir -p debian/kport/usr/lib/python3/dist-packages\n"
        "\tcp -r src/kport debian/kport/usr/lib/python3/dist-packages/\n"
        "\tinstall -D -m 0644 assets/icons/icon_512_512.png debian/kport/usr/share/icons/hicolor/512x512/apps/kport.png\n"
        "\tmkdir -p debian/kport/usr/share/applications\n"
        "\techo '[Desktop Entry]\\nName=KPort\\nComment=Port Inspector and Killer\\nExec=kport\\nIcon=kport\\nTerminal=true\\nType=Application\\nCategories=Utility;System;' > debian/kport/usr/share/applications/kport.desktop\n"
    )

    changelog = textwrap.dedent(
        f"""\
        kport ({deb_version}) unstable; urgency=medium

          * Auto-generated Debian packaging.

         -- {maint_name} <{maint_email}>  {_rfc2822_now_utc()}
        """
    )

    (debian_dir / "control").write_text(control, encoding="utf-8")
    (debian_dir / "rules").write_text(rules, encoding="utf-8")
    (debian_dir / "rules").chmod(0o755)
    (debian_dir / "changelog").write_text(changelog, encoding="utf-8")
    (debian_dir / "compat").write_text(str(compat) + "\n", encoding="utf-8")
    (debian_dir / "source" / "format").write_text("3.0 (native)\n", encoding="utf-8")

    return deb_version


def _parse_unmet_build_deps(output: str) -> list[str]:
    """Parse `dpkg-checkbuilddeps` output and return missing package names."""
    # Example:
    # dpkg-checkbuilddeps: error: Unmet build dependencies: debhelper-compat (= 13)
    m = re.search(r"Unmet build dependencies:\s*(.*)$", output, re.IGNORECASE | re.MULTILINE)
    if not m:
        return []
    deps = m.group(1)
    parts = [p.strip() for p in deps.split(",") if p.strip()]
    pkgs: list[str] = []
    for p in parts:
        # Strip version constraints: "foo (>= 1.2)" -> "foo"
        name_m = re.match(r"^([a-z0-9+.-]+)", p, re.IGNORECASE)
        if name_m:
            pkgs.append(name_m.group(1))
    return pkgs


def check_debian_build_deps(cwd: Path) -> list[str]:
    """Return missing Build-Depends packages (best effort)."""
    if not command_exists("dpkg-checkbuilddeps"):
        return []
    proc = subprocess.run(["dpkg-checkbuilddeps"], cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return []
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return _parse_unmet_build_deps(combined)


def install_build_deps() -> None:
    print("\n📦 Installing Debian build tools (requires sudo)...")
    run(["sudo", "apt-get", "update"], "apt-get update", cwd=REPO_ROOT)
    run(
        [
            "sudo",
            "apt-get",
            "install",
            "-y",
            "build-essential",
            "dpkg-dev",
            "devscripts",
            "debhelper",
            "dh-python",
            "python3-all",
        ],
        "Install build dependencies",
        cwd=REPO_ROOT,
    )


def install_missing_packages(pkgs: list[str]) -> None:
    if not pkgs:
        return
    print("\n📦 Installing missing Build-Depends (requires sudo)...")
    run(["sudo", "apt-get", "update"], "apt-get update", cwd=REPO_ROOT)
    run(["sudo", "apt-get", "install", "-y", *pkgs], f"Install: {' '.join(pkgs)}", cwd=REPO_ROOT)


def _copy_new_debs(build_parent: Path, before: set[str]) -> list[Path]:
    after = list(build_parent.glob("*.deb"))
    new_files = [p for p in after if p.name not in before]
    if not new_files:
        return []
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for p in new_files:
        dest = DIST_DIR / p.name
        shutil.copy2(p, dest)
        copied.append(dest)
    return copied


def build_deb() -> Path | None:
    """Build the .deb from a generated packaging skeleton.

    The generated debian/ files live only in a temporary build tree.
    The produced .deb is copied into dist/deb/.
    """
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kport-deb-") as td:
        tmp_root = Path(td)
        work_dir = tmp_root / "kport"

        def _ignore(dirpath: str, names: list[str]) -> set[str]:
            ignored = {
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".venv",
                "venv",
                "dist",
                "build",
                "*.egg-info",
            }
            out: set[str] = set()
            for n in names:
                if n in ignored:
                    out.add(n)
            # ignore any egg-info dirs
            out.update({n for n in names if n.endswith(".egg-info")})
            return out

        shutil.copytree(REPO_ROOT, work_dir, dirs_exist_ok=True, ignore=_ignore)
        deb_version = generate_debian_skeleton(work_dir)
        print(f"\n📦 Building .deb (generated debian version: {deb_version})")

        # dpkg-buildpackage writes artifacts to the parent directory
        build_parent = work_dir.parent
        before = {p.name for p in build_parent.glob("*.deb")}

        run(["dpkg-buildpackage", "-us", "-uc"], "Build Debian package", cwd=work_dir)

        copied = _copy_new_debs(build_parent, before)
        if not copied:
            return None

        # Prefer the main package over dbgsym/source packages
        copied.sort(key=lambda p: ("dbgsym" in p.name, p.name))
        return copied[0]


def show_install_hint(deb_path: Path) -> None:
    print("\n🎉 Build complete")
    print(f".deb: {deb_path}")
    print("Install:")
    print(f"  sudo dpkg -i {deb_path}")
    print("Then run:")
    print("  kport --version")
    print("  kport list")


def main() -> None:
    print("=" * 60)
    print("🚀 kport Debian Publishing Tool")
    print("=" * 60)

    check_layout()

    if not is_debian_like():
        print("⚠️  This does not look like a Debian/Ubuntu system.")
        print("Building .deb typically requires Debian/Ubuntu tooling (dpkg-buildpackage, debhelper).")

    if len(sys.argv) > 1:
        choice = sys.argv[1].strip()
    else:
        print("\nWhat would you like to do?")
        print("1. Check build tools")
        print("2. Install build dependencies (apt-get, requires sudo)")
        print("3. Build .deb")
        print("4. Build .deb and show install command")
        print("0. Exit")

        try:
            choice = input("\nEnter your choice (0-4): ").strip()
        except EOFError:
            print("0")
            choice = "0"

    if choice == "0":
        print("👋 Goodbye!")
        return

    if choice == "1":
        missing = check_build_tools()
        if missing:
            print("❌ Missing tools: " + ", ".join(missing))
            print("Run option 2 to install build dependencies.")
            sys.exit(1)
        print("✅ Build tools look installed")
        # Generate a tiny build tree so dpkg-checkbuilddeps has a debian/control.
        with tempfile.TemporaryDirectory(prefix="kport-deb-check-") as td:
            tmp_root = Path(td)
            work_dir = tmp_root / "kport"
            shutil.copytree(REPO_ROOT, work_dir, dirs_exist_ok=True)
            generate_debian_skeleton(work_dir)
            unmet = check_debian_build_deps(work_dir)
        if unmet:
            print("⚠️  Unmet Build-Depends: " + ", ".join(unmet))
            print("Run option 2 (or install missing packages) before building.")
        return

    if choice == "2":
        install_build_deps()
        return

    if choice in ("3", "4"):
        missing = check_build_tools()
        if missing:
            print("❌ Missing tools: " + ", ".join(missing))
            print("Run option 2 to install build dependencies.")
            sys.exit(1)

        # Preflight: generate packaging into a temp dir and run dpkg-checkbuilddeps.
        with tempfile.TemporaryDirectory(prefix="kport-deb-check-") as td:
            tmp_root = Path(td)
            work_dir = tmp_root / "kport"
            shutil.copytree(REPO_ROOT, work_dir, dirs_exist_ok=True)
            generate_debian_skeleton(work_dir)
            unmet = check_debian_build_deps(work_dir)
        if unmet:
            print("⚠️  Missing Build-Depends: " + ", ".join(unmet))
            try:
                proceed = input("Install missing Build-Depends now? (y/N): ").strip().lower()
            except EOFError:
                proceed = "n"
                print()
            if proceed in ("y", "yes"):
                install_missing_packages(unmet)
            else:
                print("❌ Cannot build until Build-Depends are installed.")
                sys.exit(1)

        deb = build_deb()
        if not deb:
            print("⚠️  Build finished but the .deb file was not located automatically.")
            print("Checked temporary build output; nothing copied to dist/deb/.")
            return
        if choice == "4":
            show_install_hint(deb)
        else:
            print(str(deb))
        return

    print("❌ Invalid choice")
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user")
        sys.exit(0)
