#!/usr/bin/env python3
"""
env-check: リポジトリ/スキル/MCPプラグインの環境適合性を導入前にチェックする。
使用方法:
  python3 env_check.py <GitHub URL>
  python3 env_check.py <スキル .md ファイルまたはディレクトリ>
  python3 env_check.py <npm パッケージ名>
  python3 env_check.py <ローカルディレクトリ>
"""

import sys
import os
import re
import json
import subprocess
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal, Optional

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"

PASS = f"{GREEN}✅{RESET}"
WARN = f"{YELLOW}⚠️ {RESET}"
FAIL = f"{RED}❌{RESET}"

# bash/sh/zsh コードブロックでスキップする組み込みコマンド
_SHELL_BUILTINS = frozenset({
    "echo", "export", "source", "set", "if", "fi", "for", "do", "done",
    "then", "else", "elif", "cd", "mkdir", "cp", "mv", "rm", "cat", "grep",
    "sed", "awk", "ls", "pwd", "true", "false", "read", "exit", "return",
    "local", "declare", "unset", "test", "printf", "touch", "chmod", "chown",
    "ln", "python3", "python", "node", "npm", "pip3", "pip", "brew", "git",
    "curl", "wget",
})

# 環境変数チェックで除外するシステム変数
_SYSTEM_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "SHELL", "TERM", "ARGUMENTS", "PWD", "OLDPWD",
})

Level = Literal["ok", "warn", "error"]


# ─────────────────────────────────────────────
# データモデル
# ─────────────────────────────────────────────

@dataclass
class Issue:
    level: Level
    label: str
    detail: str


@dataclass
class Report:
    target: str
    target_type: str
    snapshot: dict
    issues: list[Issue] = field(default_factory=list)


# ─────────────────────────────────────────────
# 環境スナップショット
# ─────────────────────────────────────────────

def _run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception:
        return ""


def _parse_ver(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", s)
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def get_env_snapshot() -> dict:
    node_raw = _run(["node", "--version"])
    py_raw   = _run(["python3", "--version"])
    return {
        "node_raw":   node_raw,
        "node":       _parse_ver(node_raw),
        "python_raw": py_raw.replace("Python ", ""),
        "python":     _parse_ver(py_raw),
        "macos":      _run(["sw_vers", "-productVersion"]),
        "arch":       _run(["uname", "-m"]),
    }


# ─────────────────────────────────────────────
# バージョン制約パーサー
# ─────────────────────────────────────────────

def _parse_semver_constraint(constraint: str) -> list[tuple[str, tuple[int, ...]]]:
    """'>=18.0.0 <20' などを [(op, version), ...] に変換。"""
    results = []
    for op, ver in re.findall(r"(>=|<=|>|<|=|~|\^)?\s*(\d+(?:\.\d+)*)", constraint):
        op = op or "="
        if op in ("~", "^"):
            op = ">="
        results.append((op, tuple(int(x) for x in ver.split("."))))
    return results


def _pad(t: tuple[int, ...], n: int = 3) -> tuple[int, ...]:
    return t + (0,) * (n - len(t))


def satisfies(current: tuple[int, ...], constraint: str) -> bool:
    """current バージョンが constraint を満たすか判定。"""
    if not constraint or constraint == "*":
        return True
    ops = _parse_semver_constraint(constraint)
    if not ops:
        return True
    cur = _pad(current)
    checks = {
        ">=": lambda c, r: c >= r,
        ">":  lambda c, r: c > r,
        "<=": lambda c, r: c <= r,
        "<":  lambda c, r: c < r,
        "=":  lambda c, r: c == r,
    }
    return all(checks.get(op, lambda c, r: True)(cur, _pad(req)) for op, req in ops)


# ─────────────────────────────────────────────
# GitHub ファイル取得
# ─────────────────────────────────────────────

def _fetch_url(url: str) -> Optional[str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _github_slug(repo_url: str) -> Optional[str]:
    m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    return m.group(1) if m else None


def fetch_github_file(repo_url: str, path: str) -> Optional[str]:
    slug = _github_slug(repo_url)
    if not slug:
        return None
    for branch in ("main", "master"):
        content = _fetch_url(f"https://raw.githubusercontent.com/{slug}/{branch}/{path}")
        if content is not None:
            return content
    return None


def list_github_files(repo_url: str, dir_path: str) -> list[str]:
    slug = _github_slug(repo_url)
    if not slug:
        return []
    owner, repo = slug.split("/", 1)
    content = _fetch_url(f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}")
    if not content:
        return []
    try:
        return [item["name"] for item in json.loads(content) if isinstance(item, dict)]
    except Exception:
        return []


# ─────────────────────────────────────────────
# チェック: GitHub リポジトリ（サブチェック分割）
# ─────────────────────────────────────────────

def _check_package_json(url: str, snap: dict) -> list[Issue]:
    raw = fetch_github_file(url, "package.json")
    if not raw:
        return [Issue("ok", "package.json", "なし（Node.js プロジェクトでない可能性）")]
    try:
        pkg = json.loads(raw)
    except json.JSONDecodeError:
        return [Issue("warn", "package.json", "JSON パースエラー")]

    issues = []
    engines = pkg.get("engines", {})

    node_req = engines.get("node", "")
    if node_req:
        ok = satisfies(snap["node"], node_req)
        issues.append(Issue(
            "ok" if ok else "error",
            f"Node.js 要件 ({node_req})",
            f"現在: {snap['node_raw']}" + ("" if ok else " → 要件を満たしていません"),
        ))
    else:
        issues.append(Issue("ok", "Node.js 要件", "engines.node 未指定（制約なし）"))

    npm_req = engines.get("npm", "")
    if npm_req:
        npm_ver_raw = _run(["npm", "--version"])
        npm_ver = tuple(int(x) for x in npm_ver_raw.split(".") if x.isdigit())
        ok = satisfies(npm_ver, npm_req)
        issues.append(Issue(
            "ok" if ok else "warn",
            f"npm 要件 ({npm_req})",
            f"現在: v{npm_ver_raw}",
        ))
    return issues


def _check_python_deps(url: str, snap: dict) -> list[Issue]:
    issues = []
    if fetch_github_file(url, "requirements.txt"):
        issues.append(Issue("ok", "requirements.txt", "検出（Python プロジェクト）"))

    pyproject = fetch_github_file(url, "pyproject.toml")
    if pyproject:
        m = re.search(r'python\s*=\s*["\']([^"\']+)["\']', pyproject)
        if m:
            py_req = m.group(1)
            ok = satisfies(snap["python"], py_req)
            issues.append(Issue(
                "ok" if ok else "error",
                f"Python 要件 ({py_req})",
                f"現在: {snap['python_raw']}" + ("" if ok else " → 要件を満たしていません"),
            ))
    return issues


def _check_devcontainer(url: str) -> list[Issue]:
    has = fetch_github_file(url, ".devcontainer/devcontainer.json") is not None
    detail = "devcontainer.json あり（隔離環境で試せます）" if has else "なし"
    return [Issue("ok", "Dev Container", detail)]


def _check_workflows(url: str, snap: dict) -> list[Issue]:
    wf_files = list_github_files(url, ".github/workflows")
    if not wf_files:
        return [Issue("ok", "GitHub Actions", "なし")]

    issues = []
    arm_required = False
    node_matrix_lines = []

    for wf_name in wf_files[:5]:
        wf = fetch_github_file(url, f".github/workflows/{wf_name}")
        if not wf:
            continue
        for r in re.findall(r"runs-on:\s*(.+)", wf):
            r = r.strip().strip("'\"")
            if "arm" in r.lower() or "aarch64" in r.lower():
                arm_required = True
        node_versions = re.findall(r"node-version['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)*)['\"]?", wf)
        if node_versions:
            node_matrix_lines.append(f"{wf_name}: Node {', '.join(node_versions)}")

    if arm_required and snap["arch"] == "x86_64":
        issues.append(Issue("warn", "CI アーキテクチャ", "CI に arm64 ランナーが含まれます（現在: x86_64）"))
    if node_matrix_lines:
        issues.append(Issue("ok", "CI Node バージョン matrix", "; ".join(node_matrix_lines)))
    else:
        issues.append(Issue("ok", "GitHub Actions", f"{len(wf_files)} ワークフロー検出"))
    return issues


def _check_dockerfile(url: str, snap: dict) -> list[Issue]:
    dockerfile = fetch_github_file(url, "Dockerfile")
    if not dockerfile:
        return []
    from_lines = re.findall(r"^FROM\s+(.+)", dockerfile, re.MULTILINE | re.IGNORECASE)
    arm_line = next((fl for fl in from_lines if "arm" in fl.lower() or "aarch64" in fl.lower()), None)
    if arm_line and snap["arch"] == "x86_64":
        return [Issue("warn", "Dockerfile", f"arm系イメージ検出: {arm_line}（x86_64 で動作しない可能性）")]
    first = from_lines[0] if from_lines else "不明"
    return [Issue("ok", "Dockerfile", f"FROM: {first}")]


def check_github(url: str, snap: dict) -> list[Issue]:
    issues = []
    issues += _check_package_json(url, snap)
    issues += _check_python_deps(url, snap)
    issues += _check_devcontainer(url)
    issues += _check_workflows(url, snap)
    issues += _check_dockerfile(url, snap)
    return issues


# ─────────────────────────────────────────────
# チェック: Claude スキル (.md)
# ─────────────────────────────────────────────

def _resolve_skill_path(path_str: str) -> tuple[Optional[Path], list[Issue]]:
    """スキルのファイルパスを解決する。失敗時は (None, [error_issue]) を返す。"""
    p = Path(path_str).expanduser()
    if p.is_dir():
        candidates = list(p.glob("SKILL.md")) + list(p.glob("*.md"))
        if not candidates:
            return None, [Issue("error", "スキルファイル", f"{p} に .md ファイルが見つかりません")]
        p = candidates[0]
    if not p.exists():
        return None, [Issue("error", "ファイル", f"{p} が存在しません")]
    return p, []


def check_skill(path_str: str, snap: dict) -> list[Issue]:
    skill_path, errors = _resolve_skill_path(path_str)
    if errors:
        return errors

    text = skill_path.read_text(encoding="utf-8", errors="replace")
    issues = []

    # frontmatter の requires / tools / dependencies フィールド
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for key in ("requires", "tools", "dependencies"):
            vals = re.findall(rf"^{key}:\s*(.+)", fm, re.MULTILINE)
            if vals:
                issues.append(Issue("ok", f"frontmatter.{key}", vals[0]))
    else:
        issues.append(Issue("ok", "frontmatter", "なし（標準形式）"))

    # bash コードブロックからコマンド存在確認
    bash_blocks = re.findall(r"```(?:bash|sh|zsh)\n(.*?)```", text, re.DOTALL)
    commands_seen: set[str] = set()
    for block in bash_blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cmd = re.split(r"[\s|;&>]", line)[0].lstrip("$").strip()
            if not cmd or cmd in commands_seen or re.match(r"^\$?\{", cmd):
                continue
            if cmd in _SHELL_BUILTINS:
                continue
            commands_seen.add(cmd)
            which = _run(["which", cmd])
            level: Level = "ok" if which else "error"
            label = f"コマンド: {cmd}" if which else f"コマンド未インストール: {cmd}"
            detail = which if which else "which で見つかりません"
            issues.append(Issue(level, label, detail))

    # 環境変数の設定状況
    env_vars = set(re.findall(r"\$([A-Z][A-Z0-9_]{2,})", text)) - _SYSTEM_ENV_VARS
    for v in sorted(env_vars):
        if os.environ.get(v):
            issues.append(Issue("ok", f"環境変数: ${v}", "設定済み"))
        else:
            issues.append(Issue("warn", f"環境変数未設定: ${v}", "未設定（スキルが依存する場合は要設定）"))

    # pip / npm 依存のリストアップ
    pip_pkgs = re.findall(r"pip3?\s+install\s+([\w\-\[\],>=<. ]+)", text)
    npm_pkgs = re.findall(r"npm\s+(?:install|i)\s+(?:-[gGD]\s+)?([\w\-@/. ]+)", text)
    if pip_pkgs:
        issues.append(Issue("ok", "pip 依存", ", ".join(p.strip() for p in pip_pkgs[:5])))
    if npm_pkgs:
        issues.append(Issue("ok", "npm 依存", ", ".join(p.strip() for p in npm_pkgs[:5])))

    return issues


# ─────────────────────────────────────────────
# チェック: MCP プラグイン (npm パッケージ)
# ─────────────────────────────────────────────

def check_mcp(pkg_name: str, snap: dict) -> list[Issue]:
    raw = _run(["npm", "info", pkg_name, "--json"])
    if not raw:
        return [Issue("error", "npm info", f"{pkg_name} が見つかりません")]

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return [Issue("warn", "npm info", "JSON パースエラー")]

    issues: list[Issue] = [Issue("ok", "パッケージバージョン", info.get("version", "不明"))]

    node_req = info.get("engines", {}).get("node", "")
    if node_req:
        ok = satisfies(snap["node"], node_req)
        issues.append(Issue(
            "ok" if ok else "error",
            f"Node.js 要件 ({node_req})",
            f"現在: {snap['node_raw']}" + ("" if ok else " → 要件を満たしていません"),
        ))
    else:
        issues.append(Issue("ok", "Node.js 要件", "未指定（制約なし）"))

    os_field = info.get("os", [])
    if os_field:
        os_ok = any("darwin" in o.lower() or o == "*" for o in os_field)
        issues.append(Issue(
            "ok" if os_ok else "error",
            f"OS 互換 ({', '.join(os_field)})",
            "macOS 対応" if os_ok else "macOS 非対応の可能性",
        ))

    cpu_field = info.get("cpu", [])
    if cpu_field:
        arch = snap["arch"]
        arch_normalized = "arm64" if "arm" in arch else "x64"
        cpu_ok = any(c in ("*", arch_normalized, arch) for c in cpu_field)
        issues.append(Issue(
            "ok" if cpu_ok else "warn",
            f"CPU アーキテクチャ ({', '.join(cpu_field)})",
            f"現在: {arch}" + ("" if cpu_ok else " → 非対応の可能性"),
        ))

    # 既存 MCP との重複チェック
    for config_path in [
        Path.home() / ".claude" / "claude_desktop_config.json",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    ]:
        if not config_path.exists():
            continue
        try:
            servers = json.loads(config_path.read_text()).get("mcpServers", {})
            pkg_base = pkg_name.split("/")[-1]
            duplicates = [k for k in servers if pkg_base in k]
            label = "重複 MCP" if duplicates else "MCP 重複確認"
            detail = f"既存設定に類似エントリ: {', '.join(duplicates)}" if duplicates else "既存設定と重複なし"
            issues.append(Issue("warn" if duplicates else "ok", label, detail))
        except Exception:
            pass
        break

    return issues


# ─────────────────────────────────────────────
# チェック: ローカルディレクトリ
# ─────────────────────────────────────────────

def check_local(path_str: str, snap: dict) -> list[Issue]:
    p = Path(path_str).expanduser()
    issues: list[Issue] = []

    pkg_file = p / "package.json"
    if pkg_file.exists():
        try:
            data = json.loads(pkg_file.read_text())
            node_req = data.get("engines", {}).get("node", "")
            if node_req:
                ok = satisfies(snap["node"], node_req)
                issues.append(Issue(
                    "ok" if ok else "error",
                    f"Node.js 要件 ({node_req})",
                    f"現在: {snap['node_raw']}",
                ))
            else:
                issues.append(Issue("ok", "package.json", f"name: {data.get('name','?')} v{data.get('version','?')}"))
        except Exception:
            issues.append(Issue("warn", "package.json", "パースエラー"))

    req_file = p / "requirements.txt"
    if req_file.exists():
        issues.append(Issue("ok", "requirements.txt", f"{len(req_file.read_text().splitlines())} 依存"))

    pyt_file = p / "pyproject.toml"
    if pyt_file.exists():
        m = re.search(r'python\s*=\s*["\']([^"\']+)["\']', pyt_file.read_text())
        if m:
            ok = satisfies(snap["python"], m.group(1))
            issues.append(Issue(
                "ok" if ok else "error",
                f"Python 要件 ({m.group(1)})",
                f"現在: {snap['python_raw']}",
            ))

    skill_md = next(iter(list(p.glob("SKILL.md")) + list(p.glob("*.md"))), None)
    if skill_md:
        issues += check_skill(str(skill_md), snap)

    if not issues:
        issues.append(Issue("warn", "解析対象なし", "package.json / requirements.txt / SKILL.md が見つかりません"))

    return issues


# ─────────────────────────────────────────────
# 対象タイプ自動判別
# ─────────────────────────────────────────────

def detect_type(arg: str) -> str:
    if arg.startswith("https://github.com/"):
        return "github"
    p = Path(arg).expanduser()
    if p.exists():
        if p.suffix == ".md":
            return "skill"
        if p.is_dir():
            return "skill" if list(p.glob("*.md")) else "local"
    # npm パッケージ名（@scope/name または plain-name）
    if re.match(r"^(@[\w-]+/)?[\w][\w.\-]*$", arg):
        return "mcp"
    return "local"


# ─────────────────────────────────────────────
# レポート描画
# ─────────────────────────────────────────────

def render(report: Report) -> str:
    snap = report.snapshot
    lines = [
        f"\n{BOLD}🔍 env-check: {report.target}{RESET}  {DIM}[{report.target_type}]{RESET}",
        "─" * 52,
        f"{BOLD}環境スナップショット:{RESET}",
        f"  Node.js : {snap['node_raw'] or '未検出'}",
        f"  Python  : {snap['python_raw'] or '未検出'}",
        f"  macOS   : {snap['macos']} ({snap['arch']})",
        "",
        f"{BOLD}チェック結果:{RESET}",
    ]

    errors = warns = 0
    for issue in report.issues:
        if issue.level == "error":
            icon, errors = FAIL, errors + 1
        elif issue.level == "warn":
            icon, warns = WARN, warns + 1
        else:
            icon = PASS
        lines.append(f"  {icon} {issue.label.ljust(36)} {DIM}{issue.detail}{RESET}")

    lines.append("")
    lines.append("─" * 52)
    if errors == 0 and warns == 0:
        verdict = f"{GREEN}{BOLD}✅ 適合{RESET}  問題なし"
    elif errors == 0:
        verdict = f"{YELLOW}{BOLD}⚠️  条件付き適合{RESET}  警告 {warns} 件（要確認）"
    else:
        verdict = f"{RED}{BOLD}❌ 非適合{RESET}  エラー {errors} 件、警告 {warns} 件"
    lines.append(f"総合判定: {verdict}")
    lines.append("─" * 52)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────

def _help_text() -> str:
    return f"""
{BOLD}env-check{RESET} — 導入前の環境適合性チェッカー

使用方法:
  python3 env_check.py <対象>

対象の種類（自動判別）:
  GitHub URL       https://github.com/owner/repo
  スキルファイル   ~/.claude/skills/my-skill/SKILL.md
  npm パッケージ   @scope/package-name
  ローカルパス     ./some-directory

例:
  python3 env_check.py https://github.com/anthropics/claude-code
  python3 env_check.py ~/.claude/skills/coding-playbook/SKILL.md
  python3 env_check.py @modelcontextprotocol/server-github
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(_help_text())
        sys.exit(0)

    arg = sys.argv[1]
    snap = get_env_snapshot()
    target_type = detect_type(arg)

    print(f"{DIM}対象タイプ: {target_type} / スナップショット取得中...{RESET}", flush=True)

    checkers = {
        "github": check_github,
        "skill":  check_skill,
        "mcp":    check_mcp,
        "local":  check_local,
    }
    issues = checkers[target_type](arg, snap)

    print(render(Report(target=arg, target_type=target_type, snapshot=snap, issues=issues)))


if __name__ == "__main__":
    main()
