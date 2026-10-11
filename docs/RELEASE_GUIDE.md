# Release Guide for kport

This document describes how to create and publish releases for kport.

> **Quick path** — use `manage.py` for all release operations. No need to run individual scripts manually.

---

## Quick Start: Automated Release

```bash
python manage.py release
```

This interactive wizard handles:
- ✅ Version validation and synchronisation (`pyproject.toml` + `src/kport/__init__.py`)
- ✅ Running the full test suite
- ✅ Git tag creation
- ✅ PyPI package building and publishing
- ✅ GitHub Release creation with all platform artifacts
- ✅ Release notes generation

---

## Manual Release Process

If you prefer step-by-step control:

### 1. Pre-Release Checklist

- [ ] All tests pass: `pytest`
- [ ] CHANGELOG or release notes prepared
- [ ] Version bumped in `pyproject.toml`
- [ ] No uncommitted changes: `git status`

### 2. Version Update

Edit the single version in `pyproject.toml`:

```toml
[project]
version = "3.2.4"   # change this
```

Then sync all other files automatically:

```bash
python manage.py sync-version 3.2.4
```

This updates `pyproject.toml` **and** `src/kport/__init__.py` in one shot. Commit both:

```bash
git add pyproject.toml src/kport/__init__.py
git commit -m "chore: bump version to 3.2.4"
```

### 3. Create Git Tag

```bash
git tag -a v3.2.4 -m "Release v3.2.4"
git push origin main
git push origin --tags
```

### 4. Build Packages

```bash
# Build all packages for the current platform:
python manage.py build --all

# Or build selectively:
python manage.py build --pypi
python manage.py build --win
python manage.py build --deb --rpm --snap
```

### 5. Publish Packages

```bash
# Publish to PyPI:
python manage.py publish --pypi

# Publish snap (uses strict confinement with system plugs):
python manage.py publish --snap

# Publish to Chocolatey Community Repository:
python manage.py publish --choco
```

### 6. Create GitHub Release

#### Option A: GitHub Web UI

1. Go to: https://github.com/farman20ali/kport/releases/new
2. Select tag: `v3.2.4`
3. Release title: `kport v3.2.4`
4. Add release notes (features, fixes, breaking changes)
5. Attach **built artifacts only** (GitHub auto-attaches source):
   - `dist/kport-*.whl` (Python wheel) ✅
   - `dist/deb/kport_*_all.deb` ✅
   - `dist/rpm/kport-*.rpm` ✅
   - `dist/win/kport-*-setup.exe` ✅
   - `dist/mac/kport-*.pkg` ✅
6. Click "Publish release"

> **Note:** GitHub automatically generates `Source code (zip)` and `Source code (tar.gz)` for every release — do **not** upload `dist/*.tar.gz` manually.

#### Option B: GitHub CLI

```bash
gh release create v3.2.4 \
  --title "kport v3.2.4" \
  --notes-file docs/RELEASE_NOTES_3.2.4.md \
  dist/kport-*.whl \
  dist/deb/*.deb \
  dist/rpm/*.rpm \
  dist/win/*-setup.exe \
  dist/mac/*.pkg
```

---

## Release Checklist

### Before Release

- [ ] `pytest` passes (all 34+ tests green)
- [ ] Version updated in `pyproject.toml` via `python manage.py sync-version X.Y.Z`
- [ ] CHANGELOG updated with new version section
- [ ] All changes committed, `git status` clean

### During Release

- [ ] Git tag created and pushed
- [ ] `python manage.py build --all` succeeded for each platform
- [ ] PyPI upload successful
- [ ] GitHub Release created with artifacts attached

### After Release

- [ ] Test PyPI installation: `pip install kport`
- [ ] Test Debian installation: `sudo dpkg -i kport_*.deb`
- [ ] Test Windows installer (run the setup `.exe`)
- [ ] Announce release in GitHub Discussions / social media

---

## Distribution Channels

| Channel | Command |
|---------|---------|
| PyPI | `pip install kport` |
| Chocolatey (Windows) | `choco install kport` |
| Snap | `sudo snap install kport` *(requires Store approval)* |
| Debian/Ubuntu | `sudo dpkg -i kport_*.deb` |
| RHEL/Fedora | `sudo rpm -i kport-*.rpm` |
| macOS | open `kport-*.pkg` |
| Source | `pip install git+https://github.com/farman20ali/kport.git` |

---

## Troubleshooting

### PyPI Upload Fails

1. Check credentials in `~/.pypirc`
2. Ensure version doesn't already exist on PyPI
3. Test on Test PyPI first: `python manage.py publish --pypi --test`

### Git Tag Already Exists

```bash
git tag -d v3.2.4
git push origin :refs/tags/v3.2.4
git tag -a v3.2.4 -m "Release v3.2.4"
git push origin --tags
```

### Debian Build Fails

```bash
python manage.py build --deb --check   # check prerequisites
sudo apt-get install -y debhelper build-essential python3-all
```

---

## Release Scripts Reference

| Script | Purpose |
|--------|---------|
| `python manage.py release` | Full interactive release wizard |
| `python manage.py sync-version X.Y.Z` | Sync version across all files |
| `python manage.py build --all` | Build all platform packages |
| `python manage.py publish --pypi` | Upload to PyPI |
| `python manage.py publish --snap` | Push snap to Snap Store |
| `python manage.py publish --choco` | Push package to Chocolatey |
| `python scripts/git_release.py` | Automated git tagging + GitHub release |
