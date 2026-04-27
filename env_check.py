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
import urllib.error
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ANSI カラー
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"

PASS = f"{GREEN}✅{RESET}"
WARN = f"{YELLOW}⚠️ {RESET}"
FAIL = f"{RED}❌{RESET}"

@dataclass
class Issue:
    level: str          # "ok" | "warn" | "error"
    label: str
    detail: str

@dataclass
class Report:
    target: str
    target_type: str
    snapshot: dict
    issues: list = field(default_factory=list)


# ─────────────────────────────────────────────
# 環境スナップショット取得
# ─────────────────────────────────────────────

def run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception:
        return ""


def get_env_snapshot() -> dict:
    node_raw = run(["node", "--version"])
    py_raw   = run(["python3", "--version"])
    macos    = run(["sw_vers", "-productVersion"])
    arch     = run(["uname", "-m"])

    def parse_ver(s: str) -> tuple[int, ...]:
        nums = re.findall(r"\d+", s)
        return tuple(int(n) for n in nums[:3]) if nums else (0,)

    return {
        "node_raw": node_raw,
        "node":     parse_ver(node_raw),
        "python_raw": py_raw.replace("Python ", ""),
        "python":   parse_ver(py_raw),
        "macos":    macos,
        "arch":     arch,
    }


# ─────────────────────────────────────────────
# バージョン制約パーサー
# ─────────────────────────────────────────────

def parse_semver_constraint(constraint: str) -> list[tuple[str, tuple[int, ...]]]:
    """'>=18.0.0 <20' などを [(op, version), ...] のリストに変換。"""
    pattern = r"(>=|<=|>|<|=|~|\^)?\s*(\d+(?:\.\d+)*)"
    results = []
    for op, ver in re.findall(pattern, constraint):
        op = op or "="
        if op == "~":
            op = ">="
        elif op == "^":
            op = ">="
        parts = tuple(int(x) for x in ver.split("."))
        results.append((op, parts))
    return results


def satisfies(current: tuple[int, ...], constraint: str) -> bool:
    """current バージョンが constraint を満たすか判定。"""
    if not constraint or constraint in ("*", ""):
        return True
    ops = parse_semver_constraint(constraint)
    if not ops:
        return True

    def pad(t, n=3):
        return t + (0,) * (n - len(t))

    cur = pad(current)
    for op, req in ops:
        req = pad(req)
        if op == ">=":
            if cur < req:
                return False
        elif op == ">":
            if cur <= req:
                return False
        elif op == "<=":
            if cur > req:
                return False
        elif op == "<":
            if cur >= req:
                return False
        elif op == "=":
            if cur != req:
                return False
    return True


# ─────────────────────────────────────────────
# GitHub ファイル取得
# ─────────────────────────────────────────────

def github_raw_url(repo_url: str, path: str, branch: str = "main") -> str:
    m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    if not m:
        return ""
    slug = m.group(1)
    return f"https://raw.githubusercontent.com/{slug}/{branch}/{path}"


def fetch_url(url: str) -> Optional[str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_github_file(repo_url: str, path: str) -> Optional[str]:
    for branch in ("main", "master"):
        content = fetch_url(github_raw_url(repo_url, path, branch))
        if content is not None:
            return content
    return None


def list_github_files(repo_url: str, dir_path: str) -> list[str]:
    """gh CLI または GitHub API でディレクトリ内ファイル名を取得。"""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not m:
        return []
    owner, repo = m.group(1), m.group(2)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}"
    content = fetch_url(api_url)
    if not content:
        return []
    try:
        items = json.loads(content)
        return [item["name"] for item in items if isinstance(item, dict)]
    except Exception:
        return []


# ─────────────────────────────────────────────
# チェック: GitHub リポジトリ
# ─────────────────────────────────────────────

def check_github(url: str, snap: dict) -> list[Issue]:
    issues = []

    # package.json
    pkg_json = fetch_github_file(url, "package.json")
    if pkg_json:
        try:
            pkg = json.loads(pkg_json)
            engines = pkg.get("engines", {})
            node_req = engines.get("node", "")
            if node_req:
                ok = satisfies(snap["node"], node_req)
                issues.append(Issue(
                    "ok" if ok else "error",
                    f"Node.js 要件 ({node_req})",
                    f"現在: {snap['node_raw']}" + ("" if ok else f" → 要件を満たしていません"),
                ))
            else:
                issues.append(Issue("ok", "Node.js 要件", "engines.node 未指定（制約なし）"))
            npm_req = engines.get("npm", "")
            if npm_req:
                npm_ver_raw = run(["npm", "--version"])
                npm_ver = tuple(int(x) for x in npm_ver_raw.split(".") if x.isdigit())
                ok = satisfies(npm_ver, npm_req)
                issues.append(Issue(
                    "ok" if ok else "warn",
                    f"npm 要件 ({npm_req})",
                    f"現在: v{npm_ver_raw}",
                ))
        except json.JSONDecodeError:
            issues.append(Issue("warn", "package.json", "JSON パースエラー"))
    else:
        issues.append(Issue("ok", "package.json", "なし（Node.js プロジェクトでない可能性）"))

    # requirements.txt
    req_txt = fetch_github_file(url, "requirements.txt")
    if req_txt:
        issues.append(Issue("ok", "requirements.txt", f"{len(req_txt.splitlines())} 行検出（Python プロジェクト）"))
        py_ver = ".".join(str(x) for x in snap["python"][:3])
        issues.append(Issue("ok", "Python バージョン", f"現在: {snap['python_raw']}（制約は pyproject.toml を確認）"))

    # pyproject.toml
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

    # .devcontainer
    devcontainer = fetch_github_file(url, ".devcontainer/devcontainer.json")
    if devcontainer:
        issues.append(Issue("ok", "Dev Container", "devcontainer.json あり（隔離環境で試せます）"))
    else:
        issues.append(Issue("ok", "Dev Container", "なし"))

    # .github/workflows
    wf_files = list_github_files(url, ".github/workflows")
    if wf_files:
        wf_issues = []
        arm_required = False
        for wf_name in wf_files[:5]:
            wf = fetch_github_file(url, f".github/workflows/{wf_name}")
            if not wf:
                continue
            # runs-on
            runs_on = re.findall(r"runs-on:\s*(.+)", wf)
            for r in runs_on:
                r = r.strip().strip("'\"")
                if "arm" in r.lower() or "aarch64" in r.lower():
                    arm_required = True
            # node-version
            node_matrix = re.findall(r"node-version['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)*)['\"]?", wf)
            if node_matrix:
                wf_issues.append(f"{wf_name}: Node {', '.join(node_matrix)}")
        if arm_required and snap["arch"] == "x86_64":
            issues.append(Issue("warn", "CI アーキテクチャ", "CI に arm64 ランナーが含まれます（現在: x86_64）"))
        if wf_issues:
            issues.append(Issue("ok", "CI Node バージョン matrix", "; ".join(wf_issues)))
        else:
            issues.append(Issue("ok", "GitHub Actions", f"{len(wf_files)} ワークフロー検出"))
    else:
        issues.append(Issue("ok", "GitHub Actions", "なし"))

    # Dockerfile
    dockerfile = fetch_github_file(url, "Dockerfile")
    if dockerfile:
        from_lines = re.findall(r"^FROM\s+(.+)", dockerfile, re.MULTILINE | re.IGNORECASE)
        arch_note = ""
        for fl in from_lines:
            if "arm" in fl.lower() or "aarch64" in fl.lower():
                arch_note = f"arm系イメージ検出: {fl}"
        if arch_note and snap["arch"] == "x86_64":
            issues.append(Issue("warn", "Dockerfile", arch_note + "（x86_64 で動作しない可能性）"))
        else:
            issues.append(Issue("ok", "Dockerfile", f"FROM: {from_lines[0] if from_lines else '不明'}"))

    return issues


# ─────────────────────────────────────────────
# チェック: Claude スキル (.md)
# ─────────────────────────────────────────────

def check_skill(path_str: str, snap: dict) -> list[Issue]:
    issues = []
    p = Path(path_str).expanduser()

    if p.is_dir():
        candidates = list(p.glob("SKILL.md")) + list(p.glob("*.md"))
        if not candidates:
            issues.append(Issue("error", "スキルファイル", f"{p} に .md ファイルが見つかりません"))
            return issues
        p = candidates[0]

    if not p.exists():
        issues.append(Issue("error", "ファイル", f"{p} が存在しません"))
        return issues

    text = p.read_text(encoding="utf-8", errors="replace")

    # frontmatter の requires / tools フィールド
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for key in ("requires", "tools", "dependencies"):
            vals = re.findall(rf"^{key}:\s*(.+)", fm, re.MULTILINE)
            if vals:
                issues.append(Issue("ok", f"frontmatter.{key}", vals[0]))
    else:
        issues.append(Issue("ok", "frontmatter", "なし（標準形式）"))

    # bash コードブロックからコマンド抽出
    bash_blocks = re.findall(r"```(?:bash|sh|zsh)\n(.*?)```", text, re.DOTALL)
    commands_seen = set()
    missing_cmds = []
    for block in bash_blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 先頭トークンをコマンドとみなす（環境変数展開・パイプ除く）
            tokens = re.split(r"[\s|;&>]", line)
            cmd = tokens[0].lstrip("$").strip()
            if not cmd or cmd in commands_seen or re.match(r"^\$?\{", cmd):
                continue
            # 組み込みや一般的なものはスキップ
            skip = {"echo", "export", "source", "set", "if", "fi", "for", "do",
                    "done", "then", "else", "elif", "cd", "mkdir", "cp", "mv",
                    "rm", "cat", "grep", "sed", "awk", "ls", "pwd", "true", "false",
                    "read", "exit", "return", "local", "declare", "unset", "test",
                    "printf", "touch", "chmod", "chown", "ln", "python3", "python",
                    "node", "npm", "pip3", "pip", "brew", "git", "curl", "wget"}
            if cmd in skip:
                continue
            commands_seen.add(cmd)
            which = run(["which", cmd])
            if not which:
                missing_cmds.append(cmd)
            else:
                issues.append(Issue("ok", f"コマンド: {cmd}", which))
    if missing_cmds:
        for mc in missing_cmds:
            issues.append(Issue("error", f"コマンド未インストール: {mc}", "which で見つかりません"))

    # 環境変数
    env_vars = set(re.findall(r"\$([A-Z][A-Z0-9_]{2,})", text))
    env_vars -= {"PATH", "HOME", "USER", "SHELL", "TERM", "ARGUMENTS", "PWD", "OLDPWD"}
    missing_envs = [v for v in sorted(env_vars) if not os.environ.get(v)]
    set_envs     = [v for v in sorted(env_vars) if os.environ.get(v)]
    for v in set_envs:
        issues.append(Issue("ok", f"環境変数: ${v}", "設定済み"))
    for v in missing_envs:
        issues.append(Issue("warn", f"環境変数未設定: ${v}", "未設定（スキルが依存する場合は要設定）"))

    # pip install / npm install
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
    issues = []

    # npm info で JSON 取得
    raw = run(["npm", "info", pkg_name, "--json"])
    if not raw:
        issues.append(Issue("error", "npm info", f"{pkg_name} が見つかりません"))
        return issues

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        issues.append(Issue("warn", "npm info", "JSON パースエラー"))
        return issues

    # バージョン
    version = info.get("version", "不明")
    issues.append(Issue("ok", "パッケージバージョン", version))

    # engines.node
    engines = info.get("engines", {})
    node_req = engines.get("node", "")
    if node_req:
        ok = satisfies(snap["node"], node_req)
        issues.append(Issue(
            "ok" if ok else "error",
            f"Node.js 要件 ({node_req})",
            f"現在: {snap['node_raw']}" + ("" if ok else " → 要件を満たしていません"),
        ))
    else:
        issues.append(Issue("ok", "Node.js 要件", "未指定（制約なし）"))

    # os / cpu フィールド
    os_field  = info.get("os", [])
    cpu_field = info.get("cpu", [])

    if os_field:
        os_ok = any("darwin" in o.lower() or o == "*" for o in os_field)
        issues.append(Issue(
            "ok" if os_ok else "error",
            f"OS 互換 ({', '.join(os_field)})",
            "macOS 対応" if os_ok else "macOS 非対応の可能性",
        ))

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
    config_paths = [
        Path.home() / ".claude" / "claude_desktop_config.json",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    ]
    for cp in config_paths:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text())
                servers = cfg.get("mcpServers", {})
                pkg_base = pkg_name.split("/")[-1]
                duplicates = [k for k in servers if pkg_base in k]
                if duplicates:
                    issues.append(Issue("warn", "重複 MCP", f"既存設定に類似エントリ: {', '.join(duplicates)}"))
                else:
                    issues.append(Issue("ok", "MCP 重複確認", "既存設定と重複なし"))
            except Exception:
                pass
            break

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
            if list(p.glob("*.md")):
                return "skill"
            return "local"
    # npm パッケージ名パターン（例: @scope/pkg または pkg-name）
    if re.match(r"^(@[\w-]+/)?[\w][\w.\-]*$", arg) and "/" not in arg.lstrip("@").replace("/", ""):
        return "mcp"
    # スラッシュ入り npm スコープパッケージ
    if re.match(r"^@[\w-]+/[\w.\-]+$", arg):
        return "mcp"
    return "local"


# ─────────────────────────────────────────────
# レポート描画
# ─────────────────────────────────────────────

def render(report: Report) -> str:
    snap = report.snapshot
    arch = snap["arch"]
    lines = []
    lines.append(f"\n{BOLD}🔍 env-check: {report.target}{RESET}  {DIM}[{report.target_type}]{RESET}")
    lines.append("─" * 52)
    lines.append(f"{BOLD}環境スナップショット:{RESET}")
    lines.append(f"  Node.js : {snap['node_raw'] or '未検出'}")
    lines.append(f"  Python  : {snap['python_raw'] or '未検出'}")
    lines.append(f"  macOS   : {snap['macos']} ({arch})")
    lines.append("")
    lines.append(f"{BOLD}チェック結果:{RESET}")

    errors = warns = oks = 0
    for issue in report.issues:
        if issue.level == "ok":
            icon = PASS
            oks += 1
        elif issue.level == "warn":
            icon = WARN
            warns += 1
        else:
            icon = FAIL
            errors += 1
        label = issue.label.ljust(36)
        lines.append(f"  {icon} {label} {DIM}{issue.detail}{RESET}")

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

HELP = f"""
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

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(HELP)
        sys.exit(0)

    arg = sys.argv[1]
    snap = get_env_snapshot()
    target_type = detect_type(arg)

    print(f"{DIM}対象タイプ: {target_type} / スナップショット取得中...{RESET}", flush=True)

    if target_type == "github":
        issues = check_github(arg, snap)
    elif target_type == "skill":
        issues = check_skill(arg, snap)
    elif target_type == "mcp":
        issues = check_mcp(arg, snap)
    else:
        # ローカルディレクトリ: package.json / requirements.txt を直接読む
        p = Path(arg).expanduser()
        issues = []
        pkg = p / "package.json"
        req = p / "requirements.txt"
        pyt = p / "pyproject.toml"
        skill_md = list(p.glob("SKILL.md")) + list(p.glob("*.md"))

        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                node_req = data.get("engines", {}).get("node", "")
                if node_req:
                    ok = satisfies(snap["node"], node_req)
                    issues.append(Issue("ok" if ok else "error",
                        f"Node.js 要件 ({node_req})", f"現在: {snap['node_raw']}"))
                else:
                    issues.append(Issue("ok", "package.json", f"name: {data.get('name','?')} v{data.get('version','?')}"))
            except Exception:
                issues.append(Issue("warn", "package.json", "パースエラー"))
        if req.exists():
            issues.append(Issue("ok", "requirements.txt", f"{len(req.read_text().splitlines())} 依存"))
        if pyt.exists():
            m = re.search(r'python\s*=\s*["\']([^"\']+)["\']', pyt.read_text())
            if m:
                ok = satisfies(snap["python"], m.group(1))
                issues.append(Issue("ok" if ok else "error",
                    f"Python 要件 ({m.group(1)})", f"現在: {snap['python_raw']}"))
        if skill_md:
            issues += check_skill(str(skill_md[0]), snap)
        if not issues:
            issues.append(Issue("warn", "解析対象なし",
                "package.json / requirements.txt / SKILL.md が見つかりません"))

    report = Report(target=arg, target_type=target_type, snapshot=snap, issues=issues)
    print(render(report))


if __name__ == "__main__":
    main()
