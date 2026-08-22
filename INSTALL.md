# install.sh 設計方針

## 概要

`install.sh` は複数ファイルで構成される Ollama Agent Proxy を `/opt/ollama-agent-proxy` へ一括配置し、systemd サービスとして起動します。

## 実行条件

- Linux、systemd、root権限
- Python 3.10以上、`curl`、`tar`
- SHA-256検証を行う場合は `sha256sum`
- Ollamaはローカルまたは別ホストで起動済みであること

## 配布単位

`proxy.py`だけを個別に取得せず、次の環境変数で指定した単一のGitHubアーカイブを取得します。

| 変数 | 説明 | 既定値 |
|---|---|---|
| `OLLAMA_AGENT_PROXY_VERSION` | GitタグまたはコミットSHA | `main` |
| `OLLAMA_AGENT_PROXY_SHA256` | アーカイブの期待SHA-256 | 未指定 |

タグまたはコミットを指定する場合はSHA-256も必須です。`main` の開発用インストールだけは、ハッシュ未指定時に警告して続行します。

```bash
sudo OLLAMA_AGENT_PROXY_VERSION=v1.0.0 \
  OLLAMA_AGENT_PROXY_SHA256=<archive-sha256> \
  ./install.sh
```

## ステージングと切り替え

1. `/opt` 配下に `mktemp` で一時ディレクトリを作成する。
2. アーカイブを一回だけ取得し、指定済みならSHA-256を検証する。
3. `install-manifest.txt` の存在と各管理対象ファイルを確認する。
4. 一時ディレクトリ上で `py_compile` と import を検証する。
5. 旧インストール一式をバックアップ名へ rename し、検証済みディレクトリを正式名へ rename する。
6. systemdの起動確認後にバックアップを削除する。

配置、systemd設定、起動確認のいずれかが失敗した場合は、新しい配置を取り除いて旧ディレクトリ一式を戻します。

## 管理対象

`install-manifest.txt` に記載されたランタイムファイルだけを配置します。現在の主な構成は次のとおりです。

```text
/opt/ollama-agent-proxy/
├── proxy.py
├── common.py
├── ollama.py
├── agents/
│   ├── __init__.py
│   ├── opencode.py
│   ├── codex.py
│   └── claudecode.py
├── LICENSE
└── install-manifest.txt
```

## systemd

- サービス: `/etc/systemd/system/ollama-agent-proxy.service`
- 設定drop-in: `/etc/systemd/system/ollama-agent-proxy.service.d/override.conf`
- `WorkingDirectory`: `/opt/ollama-agent-proxy`
- `ExecStart`: `/usr/bin/python3 /opt/ollama-agent-proxy/proxy.py`

インストール中に `OLLAMA_HOST`、`LISTEN_HOST`、`LISTEN_PORT` を入力し、drop-inへ保存します。Ollamaが別ホストにある場合は `OLLAMA_HOST` にそのURLを指定します。

`OLLAMA_THINK`（`false` / `true` / `low` / `medium` / `high`）と、Claude Code向け`ANTHROPIC_HEARTBEAT_INTERVAL`（デフォルト15秒）を変更する場合は、同じdrop-inへ追記します。heartbeatは0より大きく300未満にする必要があります。
