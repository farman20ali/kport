"""
project.py — Project detection module for kport.

Resolves a filesystem path (typically a process cwd) to Git project metadata.

OBSERVATIONS produced:
  - git_root: the repository root directory (if found)
  - project_name: directory name of the git root
  - branch: current branch name

INFERENCES produced by callers:
  - "PID <N> appears to belong to project <X>" — caller's responsibility

Design constraints:
  - never raises: all failures return None or empty fields
  - never performs network calls (no git fetch/pull)
  - never exposes credentials in remote URLs
  - independently unit-testable: resolve_project(cwd) -> ProjectInfo | None
  - handles .git directories, .git files (worktrees), detached HEAD
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ProjectInfo:
    """Observed Git project metadata and manifest resolved from a filesystem path.

    All fields are optional — set to None if unavailable.
    Fields are OS facts (observations), not inferences.
    """
    git_root: str | None = None
    project_name: str | None = None
    branch: str | None = None
    remote_origin: str | None = None   # always credential-sanitized
    is_worktree: bool = False
    manifest_path: str | None = None
    manifest_type: str | None = None
    framework: str | None = None


# ---------------------------------------------------------------------------
# Credential sanitization
# ---------------------------------------------------------------------------

_CRED_PATTERN = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+\-.]*://)(?:[^@/\s]+@)(?P<host>.+)"
)


def _sanitize_remote_url(url: str) -> str:
    """Strip user:password@ from a remote URL.

    Examples:
        https://user:token@github.com/org/repo  → https://github.com/org/repo
        git@github.com:org/repo.git             → unchanged (SCP syntax, no creds)
        https://user@github.com/org/repo        → https://github.com/org/repo
    """
    url = url.strip()
    m = _CRED_PATTERN.match(url)
    if m:
        return m.group("scheme") + m.group("host")
    return url


# ---------------------------------------------------------------------------
# Filesystem Git root walker
# ---------------------------------------------------------------------------

def _find_git_dir(start: str) -> tuple[str, str, bool] | None:
    """Walk ancestor directories looking for .git.

    Returns (git_root, git_dir_or_file_path, is_worktree) or None.

    .git can be:
      - a directory  → normal repository
      - a file       → worktree or submodule (contains "gitdir: ...")
    """
    current = os.path.abspath(start)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.exists(candidate):
            if os.path.isfile(candidate):
                return current, candidate, True
            elif os.path.isdir(candidate):
                return current, candidate, False
        parent = os.path.dirname(current)
        if parent == current:
            # Reached filesystem root without finding .git
            return None
        current = parent


# ---------------------------------------------------------------------------
# Branch resolution
# ---------------------------------------------------------------------------

def _read_branch(git_dir: str, is_worktree: bool, git_file_path: str) -> str | None:
    """Read the current branch name from HEAD.

    Handles:
      - normal branch: "ref: refs/heads/main\n"
      - detached HEAD: a raw commit SHA
      - worktree .git file pointing elsewhere
      - missing / unreadable HEAD
    """
    # For worktree .git file, resolve the actual gitdir
    actual_git_dir = git_dir
    if is_worktree:
        try:
            with open(git_file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
            if content.startswith("gitdir:"):
                resolved = content[len("gitdir:"):].strip()
                if not os.path.isabs(resolved):
                    resolved = os.path.join(os.path.dirname(git_file_path), resolved)
                resolved = os.path.normpath(resolved)
                if os.path.isdir(resolved):
                    actual_git_dir = resolved
        except OSError:
            pass

    head_path = os.path.join(actual_git_dir, "HEAD")
    try:
        with open(head_path, "r", encoding="utf-8", errors="ignore") as f:
            head_content = f.read().strip()
    except OSError:
        return None

    if head_content.startswith("ref: refs/heads/"):
        return head_content[len("ref: refs/heads/"):]
    if head_content.startswith("ref: "):
        # Unusual ref (e.g. refs/remotes/...) — return as-is
        return head_content[len("ref: "):]
    # Detached HEAD — raw SHA or other
    if re.match(r"^[0-9a-f]{4,}", head_content):
        return f"(detached HEAD {head_content[:8]})"
    return None


# ---------------------------------------------------------------------------
# Remote origin resolution
# ---------------------------------------------------------------------------

def _read_remote_origin(git_path: str, is_worktree: bool, git_file_path: str) -> str | None:
    """Read the remote 'origin' URL from the git config file.

    git_path: the .git directory (non-worktree) or .git file (worktree).
    Never exposes credentials.
    """
    if is_worktree:
        # git_path is a .git file; resolve the actual git directory from it
        actual_git_dir = None
        try:
            with open(git_file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
            if content.startswith("gitdir:"):
                resolved = content[len("gitdir:"):].strip()
                if not os.path.isabs(resolved):
                    resolved = os.path.join(os.path.dirname(git_file_path), resolved)
                resolved = os.path.normpath(resolved)
                # worktree gitdirs are like .git/worktrees/<name>/; config lives in .git/
                candidate = resolved
                for _ in range(5):  # safety bound
                    config_path_candidate = os.path.join(candidate, "config")
                    if os.path.isfile(config_path_candidate):
                        actual_git_dir = candidate
                        break
                    candidate = os.path.dirname(candidate)
        except OSError:
            pass
        if actual_git_dir is None:
            return None
        config_path = os.path.join(actual_git_dir, "config")
    else:
        # git_path is the .git directory
        config_path = os.path.join(git_path, "config")
    try:
        with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
            config_text = f.read()
    except OSError:
        return None

    # Parse [remote "origin"] section manually — avoid dependency on configparser
    in_origin = False
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped == '[remote "origin"]':
            in_origin = True
            continue
        if in_origin:
            if stripped.startswith("["):
                break  # Next section — origin block ended without url
            if stripped.startswith("url"):
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    raw_url = parts[1].strip()
                    return _sanitize_remote_url(raw_url)
    return None


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

import json


def _detect_framework_node(data: dict) -> str | None:
    deps = data.get("dependencies", {})
    dev_deps = data.get("devDependencies", {})
    all_deps = {**deps, **dev_deps}
    if "next" in all_deps:
        return "Next.js"
    if "react" in all_deps:
        return "React"
    if "vue" in all_deps:
        return "Vue"
    if "nuxt" in all_deps:
        return "Nuxt"
    if "svelte" in all_deps:
        return "Svelte"
    if "express" in all_deps:
        return "Express"
    if "@nestjs/core" in all_deps:
        return "NestJS"
    if "vite" in all_deps:
        return "Vite"
    return None


def _parse_package_json(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        name = data.get("name")
        framework = _detect_framework_node(data)
        return name, framework
    except Exception:
        return None, None


def _parse_pyproject_toml(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        name_match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        name = name_match.group(1) if name_match else None
        
        framework = None
        content_lower = content.lower()
        if "django" in content_lower:
            framework = "Django"
        elif "fastapi" in content_lower:
            framework = "FastAPI"
        elif "flask" in content_lower:
            framework = "Flask"
        elif "streamlit" in content_lower:
            framework = "Streamlit"
        return name, framework
    except Exception:
        return None, None


def _parse_requirements_txt(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().lower()
        framework = None
        if "django" in content:
            framework = "Django"
        elif "fastapi" in content:
            framework = "FastAPI"
        elif "flask" in content:
            framework = "Flask"
        elif "streamlit" in content:
            framework = "Streamlit"
        return None, framework
    except Exception:
        return None, None


def _parse_go_mod(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        mod_match = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
        name = None
        if mod_match:
            name = mod_match.group(1).split("/")[-1]
        
        framework = None
        content_lower = content.lower()
        if "github.com/gin-gonic/gin" in content_lower:
            framework = "Gin"
        elif "github.com/gofiber/fiber" in content_lower:
            framework = "Fiber"
        elif "github.com/astaxie/beego" in content_lower:
            framework = "Beego"
        elif "github.com/labstack/echo" in content_lower:
            framework = "Echo"
        return name, framework
    except Exception:
        return None, None


def _parse_cargo_toml(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        name_match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        name = name_match.group(1) if name_match else None
        
        framework = None
        content_lower = content.lower()
        if "axum" in content_lower:
            framework = "Axum"
        elif "actix-web" in content_lower:
            framework = "Actix Web"
        elif "rocket" in content_lower:
            framework = "Rocket"
        elif "warp" in content_lower:
            framework = "Warp"
        return name, framework
    except Exception:
        return None, None


def _parse_pom_xml(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        art_match = re.search(r'<artifactId>([^<]+)</artifactId>', content)
        name = art_match.group(1) if art_match else None
        
        framework = None
        content_lower = content.lower()
        if "spring-boot" in content_lower or "springboot" in content_lower or "springframework.boot" in content_lower:
            framework = "Spring Boot"
        elif "quarkus" in content_lower:
            framework = "Quarkus"
        elif "micronaut" in content_lower:
            framework = "Micronaut"
        return name, framework
    except Exception:
        return None, None


def _parse_gradle(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        name_match = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", content)
        name = name_match.group(1) if name_match else None
        
        framework = None
        content_lower = content.lower()
        if "spring-boot" in content_lower or "springboot" in content_lower or "springframework.boot" in content_lower:
            framework = "Spring Boot"
        elif "quarkus" in content_lower:
            framework = "Quarkus"
        elif "micronaut" in content_lower:
            framework = "Micronaut"
        return name, framework
    except Exception:
        return None, None


def _parse_csproj(path: str) -> tuple[str | None, str | None]:
    try:
        name = os.path.basename(path)
        if name.lower().endswith(".csproj"):
            name = name[:-7]
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        framework = None
        if "Microsoft.NET.Sdk.Web" in content:
            framework = "ASP.NET Core"
        return name, framework
    except Exception:
        return None, None


def _find_manifest_and_parse(
    cwd: str, git_root: str | None
) -> tuple[str | None, str | None, str | None, str | None] | None:
    """Walk up the directory tree and find the first matching manifest.
    
    Returns (manifest_path, manifest_type, manifest_project_name, framework) or None.
    """
    current = os.path.abspath(cwd)
    stop_dir = os.path.abspath(git_root) if git_root else None
    
    levels_limit = 5
    level = 0
    
    while True:
        manifests = [
            ("package.json", _parse_package_json),
            ("pyproject.toml", _parse_pyproject_toml),
            ("requirements.txt", _parse_requirements_txt),
            ("go.mod", _parse_go_mod),
            ("Cargo.toml", _parse_cargo_toml),
            ("pom.xml", _parse_pom_xml),
            ("build.gradle", _parse_gradle),
            ("build.gradle.kts", _parse_gradle),
        ]
        
        csproj_files = []
        try:
            if os.path.isdir(current):
                csproj_files = [f for f in os.listdir(current) if f.lower().endswith(".csproj")]
        except Exception:
            pass
            
        for csproj in csproj_files:
            manifests.append((csproj, _parse_csproj))
            
        for fname, parser in manifests:
            path = os.path.join(current, fname)
            if os.path.isfile(path):
                name, framework = parser(path)
                return path, fname, name, framework
                
        if stop_dir and current == stop_dir:
            break
            
        parent = os.path.dirname(current)
        if parent == current:
            break
            
        if not stop_dir:
            level += 1
            if level >= levels_limit:
                break
                
        current = parent
        
    return None


def resolve_project(cwd: str | None) -> ProjectInfo | None:
    """Resolve Git project and/or manifest metadata from a working directory path.

    Args:
        cwd: Filesystem path to start the search from (process cwd, or any path).
             May be None, a deleted path, or an inaccessible path.

    Returns:
        ProjectInfo if a Git repository or manifest was found, None otherwise.
        Never raises.
    """
    if not cwd:
        return None

    # Guard: path must exist and be accessible
    try:
        if not os.path.exists(cwd):
            return None
    except (OSError, ValueError):
        return None

    git_res = None
    try:
        git_res = _find_git_dir(cwd)
    except Exception:
        pass

    git_root = None
    git_path = None
    is_worktree = False
    project_name = None
    branch = None
    remote_origin = None

    if git_res is not None:
        git_root, git_path, is_worktree = git_res
        project_name = os.path.basename(git_root) or None
        try:
            branch = _read_branch(git_path, is_worktree, git_path)
        except Exception:
            pass
        try:
            remote_origin = _read_remote_origin(git_path, is_worktree, git_path)
        except Exception:
            pass

    # Manifest resolution
    manifest_path = None
    manifest_type = None
    manifest_name = None
    framework = None
    
    try:
        manifest_res = _find_manifest_and_parse(cwd, git_root)
        if manifest_res:
            manifest_path, manifest_type, manifest_name, framework = manifest_res
    except Exception:
        pass

    # If we found neither a git repo nor a project manifest, return None
    if git_root is None and manifest_path is None:
        return None

    # Use manifest name as project name if available, fallback to path basenames
    effective_project_name = manifest_name or project_name
    if not effective_project_name:
        if manifest_path:
            effective_project_name = os.path.basename(os.path.dirname(manifest_path)) or None
        elif git_root:
            effective_project_name = os.path.basename(git_root) or None

    return ProjectInfo(
        git_root=git_root,
        project_name=effective_project_name,
        branch=branch,
        remote_origin=remote_origin,
        is_worktree=is_worktree,
        manifest_path=manifest_path,
        manifest_type=manifest_type,
        framework=framework,
    )
