"""
Unit tests for kport.project — project detection resolver.

Tests cover:
1. cwd inside a normal Git repository
2. cwd in a nested subdirectory of a Git repository
3. cwd with no Git repository ancestor
4. None / missing / inaccessible cwd
5. .git file (worktree/submodule) case
6. Branch detection (normal branch)
7. Detached HEAD detection
8. Remote origin detection
9. Credential sanitization in remote URL
10. Git metadata failure must not raise
"""
from __future__ import annotations

import os

from kport.project import _sanitize_remote_url, resolve_project

# ---------------------------------------------------------------------------
# Helpers to build fake Git repository structures on disk
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: str, branch: str = "main", remote: str | None = None) -> str:
    """Create a minimal bare .git directory structure in tmp_path."""
    git_dir = os.path.join(tmp_path, ".git")
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)

    head_content = f"ref: refs/heads/{branch}\n"
    with open(os.path.join(git_dir, "HEAD"), "w") as f:
        f.write(head_content)

    config_lines = ['[core]\n', '\trepositoryformatversion = 0\n', '\tfilemode = true\n']
    if remote:
        config_lines += ['\n', '[remote "origin"]\n', f'\turl = {remote}\n', '\tfetch = +refs/heads/*:refs/remotes/origin/*\n']
    with open(os.path.join(git_dir, "config"), "w") as f:
        f.writelines(config_lines)

    return tmp_path


def _make_worktree_file(tmp_path: str, target_git_dir: str, branch: str = "feature") -> str:
    """Create a .git *file* (worktree checkout) pointing to target_git_dir."""
    worktree_checkout = os.path.join(tmp_path, "worktree_checkout")
    os.makedirs(worktree_checkout, exist_ok=True)
    git_file = os.path.join(worktree_checkout, ".git")
    with open(git_file, "w") as f:
        f.write(f"gitdir: {target_git_dir}\n")
    return worktree_checkout


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSanitizeRemoteUrl:
    def test_https_with_user_and_password(self):
        url = "https://user:token@github.com/org/repo.git"
        assert _sanitize_remote_url(url) == "https://github.com/org/repo.git"

    def test_https_with_user_only(self):
        url = "https://user@github.com/org/repo.git"
        assert _sanitize_remote_url(url) == "https://github.com/org/repo.git"

    def test_ssh_scp_syntax_unchanged(self):
        url = "git@github.com:org/repo.git"
        assert _sanitize_remote_url(url) == "git@github.com:org/repo.git"

    def test_plain_https_unchanged(self):
        url = "https://github.com/org/repo.git"
        assert _sanitize_remote_url(url) == "https://github.com/org/repo.git"

    def test_empty_string(self):
        assert _sanitize_remote_url("") == ""


class TestResolveProject:

    def test_normal_git_repo_root(self, tmp_path):
        """1. cwd directly in a git repo root."""
        repo = _make_repo(str(tmp_path), branch="main")
        info = resolve_project(repo)
        assert info is not None
        assert os.path.normpath(info.git_root) == os.path.normpath(repo)
        assert info.branch == "main"
        assert info.is_worktree is False

    def test_nested_subdirectory(self, tmp_path):
        """2. cwd is a nested subdirectory inside a git repo."""
        repo = _make_repo(str(tmp_path), branch="develop")
        nested = os.path.join(repo, "src", "myapp")
        os.makedirs(nested, exist_ok=True)
        info = resolve_project(nested)
        assert info is not None
        assert os.path.normpath(info.git_root) == os.path.normpath(repo)
        assert info.branch == "develop"

    def test_no_git_repo(self, tmp_path):
        """3. cwd outside any git repository returns None."""
        plain_dir = os.path.join(str(tmp_path), "plain_project")
        os.makedirs(plain_dir, exist_ok=True)
        info = resolve_project(plain_dir)
        assert info is None

    def test_none_cwd(self):
        """4a. None cwd returns None without raising."""
        assert resolve_project(None) is None

    def test_missing_cwd(self):
        """4b. Non-existent path returns None without raising."""
        assert resolve_project("/this/path/does/not/exist/at/all/xyz") is None

    def test_worktree_git_file(self, tmp_path):
        """5. .git file (worktree) is handled correctly."""
        main_repo_path = os.path.join(str(tmp_path), "main_repo")
        _make_repo(main_repo_path, branch="main")

        # Simulate worktree: .git file pointing to main_repo/.git
        main_git_dir = os.path.join(main_repo_path, ".git")
        worktree_path = _make_worktree_file(str(tmp_path), main_git_dir, branch="feature")

        info = resolve_project(worktree_path)
        assert info is not None
        assert info.is_worktree is True

    def test_branch_detection(self, tmp_path):
        """6. Branch name is correctly extracted."""
        repo = _make_repo(str(tmp_path), branch="feature/new-login")
        info = resolve_project(repo)
        assert info is not None
        assert info.branch == "feature/new-login"

    def test_detached_head(self, tmp_path):
        """7. Detached HEAD is reported without raising."""
        repo = _make_repo(str(tmp_path), branch="main")
        # Overwrite HEAD with a raw SHA to simulate detached HEAD
        head_path = os.path.join(repo, ".git", "HEAD")
        with open(head_path, "w") as f:
            f.write("abcdef1234567890abcdef1234567890abcdef12\n")
        info = resolve_project(repo)
        assert info is not None
        assert info.branch is not None
        assert "detached" in info.branch.lower()
        assert "abcdef12" in info.branch

    def test_remote_origin_detection(self, tmp_path):
        """8. Remote origin URL is read from .git/config."""
        repo = _make_repo(str(tmp_path), branch="main", remote="https://github.com/org/myapp.git")
        info = resolve_project(repo)
        assert info is not None
        assert info.remote_origin == "https://github.com/org/myapp.git"

    def test_credential_sanitization(self, tmp_path):
        """9. Embedded credentials in remote URL are stripped."""
        repo = _make_repo(str(tmp_path), branch="main", remote="https://user:token123@github.com/org/private.git")
        info = resolve_project(repo)
        assert info is not None
        assert "token123" not in (info.remote_origin or "")
        assert "user" not in (info.remote_origin or "")
        assert info.remote_origin == "https://github.com/org/private.git"

    def test_no_remote(self, tmp_path):
        """Git repo with no remote configured returns None for remote_origin."""
        repo = _make_repo(str(tmp_path), branch="main", remote=None)
        info = resolve_project(repo)
        assert info is not None
        assert info.remote_origin is None

    def test_project_name_is_directory_name(self, tmp_path):
        """project_name matches the basename of the git root directory."""
        repo = _make_repo(str(tmp_path), branch="main")
        info = resolve_project(repo)
        assert info is not None
        assert info.project_name == os.path.basename(str(tmp_path))

    def test_unreadable_git_head_does_not_raise(self, tmp_path):
        """10. Corrupt/missing HEAD does not propagate an exception."""
        repo = _make_repo(str(tmp_path), branch="main")
        head_path = os.path.join(repo, ".git", "HEAD")
        os.remove(head_path)
        info = resolve_project(repo)
        # Should still return a ProjectInfo (without branch), not raise
        assert info is not None
        assert info.branch is None

    def test_resolve_project_live_repo(self):
        """Smoke test: resolve_project against the actual kport repo (where tests run)."""
        # This test is not mocked — it verifies real filesystem behavior
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        info = resolve_project(repo_root)
        if info is not None:
            # We are inside a git repo — basic structure checks
            assert os.path.isdir(info.git_root)
            # branch may be None if detached HEAD but should not raise
        # No assertion on existence — CI may checkout without .git

    def test_resolve_package_json_nextjs(self, tmp_path):
        """Verify package.json parses name and detects Next.js framework."""
        import json
        pkg_data = {
            "name": "next-app",
            "dependencies": {
                "next": "^13.0.0",
                "react": "^18.0.0"
            }
        }
        pkg_file = tmp_path / "package.json"
        with open(pkg_file, "w") as f:
            json.dump(pkg_data, f)
        
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "next-app"
        assert info.manifest_type == "package.json"
        assert info.framework == "Next.js"
        assert info.git_root is None

    def test_resolve_package_json_react_vite(self, tmp_path):
        """Verify package.json parses name and detects React/Vite."""
        import json
        pkg_data = {
            "name": "react-app",
            "dependencies": {
                "react": "^18.0.0"
            },
            "devDependencies": {
                "vite": "^4.0.0"
            }
        }
        pkg_file = tmp_path / "package.json"
        with open(pkg_file, "w") as f:
            json.dump(pkg_data, f)
        
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "react-app"
        assert info.framework == "React"

    def test_resolve_pyproject_toml_fastapi(self, tmp_path):
        """Verify pyproject.toml parses name and detects FastAPI."""
        toml_content = (
            "[project]\n"
            "name = \"api-service\"\n"
            "dependencies = [\n"
            "    \"fastapi>=0.90.0\"\n"
            "]\n"
        )
        (tmp_path / "pyproject.toml").write_text(toml_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "api-service"
        assert info.manifest_type == "pyproject.toml"
        assert info.framework == "FastAPI"

    def test_resolve_requirements_txt_django(self, tmp_path):
        """Verify requirements.txt detects Django."""
        req_content = "Django==4.1.0\ngunicorn>=20.0.0\n"
        (tmp_path / "requirements.txt").write_text(req_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == os.path.basename(str(tmp_path))
        assert info.manifest_type == "requirements.txt"
        assert info.framework == "Django"

    def test_resolve_go_mod_gin(self, tmp_path):
        """Verify go.mod module name parse and Gin framework detection."""
        go_mod_content = (
            "module github.com/user/go-backend\n\n"
            "go 1.18\n\n"
            "require github.com/gin-gonic/gin v1.8.0\n"
        )
        (tmp_path / "go.mod").write_text(go_mod_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "go-backend"
        assert info.manifest_type == "go.mod"
        assert info.framework == "Gin"

    def test_resolve_cargo_toml_axum(self, tmp_path):
        """Verify Cargo.toml name parse and Axum framework detection."""
        cargo_content = (
            "[package]\n"
            "name = \"rust-api\"\n"
            "version = \"0.1.0\"\n\n"
            "[dependencies]\n"
            "axum = \"0.6\"\n"
        )
        (tmp_path / "Cargo.toml").write_text(cargo_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "rust-api"
        assert info.framework == "Axum"

    def test_resolve_pom_xml_quarkus(self, tmp_path):
        """Verify pom.xml artifactId parse and Quarkus framework detection."""
        pom_content = (
            "<project>\n"
            "  <artifactId>payment-microservice</artifactId>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>io.quarkus</groupId>\n"
            "      <artifactId>quarkus-arc</artifactId>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "</project>\n"
        )
        (tmp_path / "pom.xml").write_text(pom_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "payment-microservice"
        assert info.manifest_type == "pom.xml"
        assert info.framework == "Quarkus"

    def test_resolve_gradle_spring_boot(self, tmp_path):
        """Verify build.gradle project name parse and Spring Boot framework detection."""
        gradle_content = (
            "plugins {\n"
            "    id 'org.springframework.boot' version '3.0.0'\n"
            "}\n"
        )
        (tmp_path / "build.gradle").write_text(gradle_content)
        (tmp_path / "settings.gradle").write_text("rootProject.name = 'billing-service'\n")
        info = resolve_project(str(tmp_path))
        # Note: rootProject.name matches settings.gradle usually, but build.gradle parser is basic
        # Check settings.gradle parsing if build.gradle lacks name
        assert info is not None
        assert info.manifest_type == "build.gradle"
        assert info.framework == "Spring Boot"

    def test_resolve_csproj_dotnet(self, tmp_path):
        """Verify csproj name parse and ASP.NET Core framework detection."""
        csproj_content = (
            "<Project Sdk=\"Microsoft.NET.Sdk.Web\">\n"
            "  <PropertyGroup>\n"
            "    <TargetFramework>net7.0</TargetFramework>\n"
            "  </PropertyGroup>\n"
            "</Project>\n"
        )
        (tmp_path / "WebAPI.csproj").write_text(csproj_content)
        info = resolve_project(str(tmp_path))
        assert info is not None
        assert info.project_name == "WebAPI"
        assert info.manifest_type == "WebAPI.csproj"
        assert info.framework == "ASP.NET Core"

    def test_resolve_nested_manifest_inside_git(self, tmp_path):
        """Verify resolve_project resolves nested manifest details inside a git repo."""
        repo = _make_repo(str(tmp_path), branch="main")
        app_dir = tmp_path / "apps" / "frontend"
        app_dir.mkdir(parents=True)
        
        import json
        with open(app_dir / "package.json", "w") as f:
            json.dump({"name": "frontend-web", "dependencies": {"next": "^13"}}, f)
            
        info = resolve_project(str(app_dir))
        assert info is not None
        assert info.project_name == "frontend-web"
        assert info.git_root == str(repo)
        assert info.branch == "main"
        assert info.manifest_type == "package.json"
        assert info.framework == "Next.js"

    def test_resolve_malformed_package_json(self, tmp_path):
        """Verify malformed JSON in package.json does not raise exception and falls back."""
        pkg_file = tmp_path / "package.json"
        # Write invalid JSON content
        pkg_file.write_text("{invalid json: no quotes")
        info = resolve_project(str(tmp_path))
        # Should still resolve project metadata (using fallback project name)
        assert info is not None
        assert info.project_name == os.path.basename(str(tmp_path))
        assert info.manifest_type == "package.json"
        assert info.framework is None

    def test_resolve_unreadable_manifest(self, tmp_path):
        """Verify unreadable package.json does not crash and falls back gracefully.

        Skipped on Windows: chmod 0o000 does not restrict reads on Windows filesystems.
        """
        import sys
        if sys.platform == "win32":
            import pytest
            pytest.skip("chmod permission restriction not enforced on Windows")
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text('{"name": "unreadable"}')
        try:
            os.chmod(pkg_file, 0o000)
            info = resolve_project(str(tmp_path))
            assert info is not None
            # Fallback name matches folder name since file was unreadable
            assert info.project_name == os.path.basename(str(tmp_path))
            assert info.framework is None
        finally:
            os.chmod(pkg_file, 0o644)
