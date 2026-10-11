# Publishing Guide — kport

> **Already live:** PyPI · Snap · APT/.deb · Chocolatey (in review)

---

## Prerequisites

```bash
pip install build twine
```

Configure `~/.pypirc` with your PyPI API token:

```ini
[pypi]
username = __token__
password = pypi-AgEI...your-token

[testpypi]
username = __token__
password = pypi-AgEN...your-test-token
```

---

## PyPI

```bash
# Bump version atomically
python manage.py sync-version X.Y.Z

# Build
python -m build

# Test first (optional)
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ --no-deps kport==X.Y.Z

# Publish
python -m twine upload dist/*
```

Or use the helper script: `python scripts/publish_pypi.py`

**Troubleshooting:**
- `403` → check API token / package name not taken
- `File already exists` → increment version with `python manage.py sync-version X.Y.Z`

---

## Debian (.deb)

```bash
python3 scripts/build_deb.py
sudo dpkg -i dist/deb/kport_*_all.deb
```

Attach the `.deb` to the GitHub Release. See `BUILD_GUIDE.md` for APT repo options.

---

## Standalone Executables (PyInstaller)

```bash
pip install pyinstaller
pyinstaller --onefile --name kport src/kport/__main__.py
# Output: dist/kport  (or dist/kport.exe on Windows)
```

Attach the binary to the GitHub Release.

---

## Homebrew Tap

**Goal:** `brew tap farman20ali/kport && brew install kport`

1. Create a GitHub repo named **`homebrew-kport`** (the prefix is required).
2. Add `Formula/kport.rb`:

```ruby
class Kport < Formula
  include Language::Python::Virtualenv

  desc "Cross-platform port inspector and killer"
  homepage "https://github.com/farman20ali/kport"
  url "https://github.com/farman20ali/kport/releases/download/vX.Y.Z/kport-X.Y.Z.tar.gz"
  sha256 "<sha256 of tarball>"
  license "Apache-2.0"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "X.Y.Z", shell_output("#{bin}/kport -v")
  end
end
```

3. Push to `main`, then verify:

```bash
brew tap farman20ali/kport
brew install kport && kport -v
brew test kport
```

---

## AUR (Arch Linux)

**Requires human step:** create an AUR account + SSH key at https://aur.archlinux.org.

Once access exists, write `PKGBUILD`:

```bash
pkgname=kport
pkgver=X.Y.Z
pkgrel=1
pkgdesc="Cross-platform port inspector and killer"
arch=('any')
url="https://github.com/farman20ali/kport"
license=('Apache')
depends=('python')
makedepends=('python-build' 'python-installer' 'python-wheel')
source=("https://github.com/farman20ali/kport/releases/download/v$pkgver/kport-$pkgver.tar.gz")
sha256sums=('<sha256>')

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl
}
```

Then:

```bash
makepkg --printsrcinfo > .SRCINFO
git remote add origin ssh://aur@aur.archlinux.org/kport.git
git add PKGBUILD .SRCINFO && git commit -m "kport X.Y.Z"
git push origin master
```

---

## Fedora COPR

**Requires human step:** create a FAS account at https://copr.fedorainfracloud.org.

```bash
copr-cli create kport --chroot fedora-40-x86_64 --chroot fedora-41-x86_64 \
  --description "Cross-platform port inspector and killer"

copr-cli build kport \
  https://github.com/farman20ali/kport/releases/download/vX.Y.Z/kport-X.Y.Z-1.noarch.rpm
```

Install: `dnf copr enable farman20ali/kport && dnf install kport`

---

## VS Code Marketplace + Open VSX

**Requires human step:** create publisher tokens (`VSCE_PAT` for Marketplace, `OVSX_PAT` for Open VSX).

```bash
npx vsce publish --packagePath kport-vscode-X.Y.Z.vsix -p $VSCE_PAT
npx ovsx publish kport-vscode-X.Y.Z.vsix -p $OVSX_PAT
```

---

## Winget (Windows Package Manager)

### Token Setup (`WINGET_GITHUB_TOKEN`)
To submit or update manifests directly to `microsoft/winget-pkgs`, generate a GitHub Personal Access Token (classic PAT):
1. Navigate to: [GitHub Token Settings](https://github.com/settings/tokens/new)
2. Note: `winget submission token`
3. Scopes: Check `public_repo`
4. Set the environment variable:
   - **PowerShell:** `$env:WINGET_GITHUB_TOKEN = "ghp_..."`
   - **CMD:** `set WINGET_GITHUB_TOKEN=ghp_...`
   - **Bash:** `export WINGET_GITHUB_TOKEN="ghp_..."`

### Silent Installation Verification
kport uses NSIS for Windows packaging. Verify silent installation:
```powershell
kport-X.Y.Z-setup.exe /S
```

### Publishing Commands

#### 1. Automated via `manage.py`
```powershell
# Publish current version to WinGet
python manage.py publish --winget

# Publish specific version override
python manage.py publish --winget --winget-version 5.1.0

# Using live GitHub Release assets
python manage.py publish --winget --from-release
```

#### 2. First-Time Package Submission (v5.1.0)
Because `farman20ali.kport` is submitted for the first time, `microsoft/winget-pkgs` does not have an existing package for `wingetcreate update` to modify. Use the first-time onboarding flow:
```powershell
# Auto-generate validated manifests and submit PR:
python scripts/publish_winget.py --publish --first-time

# Or directly submit generated manifest directory:
wingetcreate submit dist/winget/manifests/f/farman20ali/kport/5.1.0 --token $env:WINGET_GITHUB_TOKEN
```

#### 3. Subsequent Updates (Next Time)
Once the first PR is accepted and merged into `microsoft/winget-pkgs`, subsequent versions are fully automated:
```powershell
python scripts/publish_packages.py --winget
# or: wingetcreate update farman20ali.kport --version 5.2.0 --urls "https://github.com/farman20ali/kport/releases/download/v5.2.0/kport-5.2.0-setup.exe" --submit --token $env:WINGET_GITHUB_TOKEN
```

A PR is opened against `microsoft/winget-pkgs`. Automated validation tests run, followed by human review.

---

### Downloading Artifacts from GitHub Releases (`--from-release`)
Just like Chocolatey, you can pull artifacts directly from GitHub Releases to publish without needing local builds:
```powershell
# PyPI: downloads .whl and .tar.gz from GitHub Release and uploads via twine
python manage.py publish --pypi --from-release

# Snap Store: downloads .snap from GitHub Release and uploads via snapcraft
python manage.py publish --snap --from-release

# Chocolatey: rebuilds from live release checksum and pushes to Chocolatey
python manage.py publish --choco --from-release

# WinGet: computes live checksum from release installer and submits manifest PR
python manage.py publish --winget --from-release
```

---

## Sequencing Summary

| Order | Channel | Review latency | Notes |
|---|---|---|---|
| 1 | PyPI | Instant | Already live |
| 1 | Snap / APT | Instant | Already live |
| 1 | Homebrew Tap | None | Fully agent-executable |
| 1 | AUR | None after signup | Needs human AUR account once |
| 1 | Fedora COPR | None after signup | Needs human FAS account once |
| 1 | VS Code / Open VSX | Automated scan only | Needs human token creation once |
| 2 | Winget | Days (automated + human mod) | Needs silent-install check + PR monitoring |
| 2 | Homebrew Core | 1–3 weeks | Open after tap proves adoption |
| 3 | Chocolatey | In review | Submitted |

---

## Resources

- https://pypi.org/help/
- https://packaging.python.org/
- https://twine.readthedocs.io/
