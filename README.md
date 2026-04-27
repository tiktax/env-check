# env-check

GitHub リポジトリ・Claude スキル・MCP プラグインを**導入前**に自分の開発環境との適合性をチェックするツール。

## 対象環境

- macOS (Intel x86_64 / Apple Silicon arm64)
- Node.js / Python / npm の実行環境

## セットアップ

```bash
# 1. クローン
git clone https://github.com/Take/env-check ~/ClaudeCode/Project/env-check

# 2. スクリプトをスキルから参照できるようシンボリックリンク作成
ln -sf ~/ClaudeCode/Project/env-check/env_check.py ~/.claude/scripts/env_check.py

# 3. スキルファイルをコピー（Claude Code の skills ディレクトリへ）
mkdir -p ~/.claude/skills/env-check
cp ~/.claude/scripts/../ClaudeCode/Project/env-check/SKILL.md ~/.claude/skills/env-check/SKILL.md
# または手動で ~/.claude/skills/env-check/SKILL.md を配置
```

## 使い方

### CLI から直接

```bash
# GitHub リポジトリ
python3 env_check.py https://github.com/anthropics/claude-code

# Claude スキルファイル
python3 env_check.py ~/.claude/skills/coding-playbook/SKILL.md

# MCP npm パッケージ
python3 env_check.py @modelcontextprotocol/server-github

# ローカルディレクトリ
python3 env_check.py ./my-project
```

### Claude スキルとして

```
/env-check https://github.com/anthropics/claude-code
/env-check ~/.claude/skills/coding-playbook/SKILL.md
/env-check @modelcontextprotocol/server-github
```

## 出力例

```
🔍 env-check: https://github.com/anthropics/claude-code  [github]
────────────────────────────────────────────────────
環境スナップショット:
  Node.js : v25.8.1
  Python  : 3.9.6
  macOS   : 13.7.8 (x86_64)

チェック結果:
  ✅ Node.js 要件 (>=18.0.0)              現在: v25.8.1
  ✅ Dev Container                         devcontainer.json あり
  ⚠️  CI アーキテクチャ                   CI に arm64 ランナーが含まれます

総合判定: ⚠️ 条件付き適合  警告 1 件（要確認）
────────────────────────────────────────────────────
```

## チェック内容

| 対象 | チェック項目 |
|---|---|
| GitHub リポジトリ | `engines.node`、Python バージョン制約、Dev Container 有無、CI matrix、Dockerfile アーキテクチャ |
| Claude スキル | bash コードブロックのコマンド存在確認、環境変数の設定状況、pip/npm 依存 |
| MCP プラグイン | npm engines.node、os/cpu フィールド、既存 MCP との重複 |

## 依存

Python 3.9+ の標準ライブラリのみ（外部パッケージ不要）。
GitHub ファイル取得は公開 API（認証不要）。

## ライセンス

MIT
