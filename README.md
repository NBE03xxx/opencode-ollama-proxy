# Ollama Agent Proxy

OpenCode、Codex、Claude Code から Ollama 上のローカル LLM を利用するための軽量な HTTP 変換プロキシです。

OpenCode 向けに OpenAI Chat Completions 互換 API、Codex 向けに OpenAI Responses API 互換 API、Claude Code 向けに Anthropic Messages API 互換 API を提供します。

```text
OpenCode / Codex / Claude Code
   ↓ OpenAI / Anthropic 互換 API
Ollama Agent Proxy
   ↓ Ollama native API
Ollama
   ↓
Local LLM
```

## Overview

OpenCode や Codex から Ollama を直接利用する場合、API の実装差異（tools / tool calling、thinking、リクエスト／レスポンス形式など）が問題となることがあります。

このプロキシは、各agentが使用する API のサブセットを Ollama の `/api/chat` 形式へ変換し、Ollama のレスポンスをクライアント固有の形式へ変換して返します。OpenAI APIまたはAnthropic APIの完全互換実装ではありません。

**役割はあくまで API 変換・調整層です。** LLM 推論そのものを行うものではありません。

## Architecture

```text
OpenCode ── POST /v1/chat/completions ──┐
                                        ├──> proxy.py
Codex    ── POST /v1/responses ─────────┤      ├── agents/   API 形式変換
Claude   ── POST /v1/messages ──────────┘      ├── ollama.py Ollama HTTP 通信
                                               └── common.py 共通処理
                                                      │
                                                      │ POST /api/chat
                                                      ▼
                                                   Ollama
                                                      │
                                                      ▼
                                                  Local LLM
```

- OpenCode は `POST /v1/chat/completions`（OpenAI Chat Completions 互換）を使用
- Codex は `POST /v1/responses`（OpenAI Responses API 互換）を使用
- Claude Code は `POST /v1/messages`（Anthropic Messages API 互換）を使用
- プロキシがリクエストを Ollama の `/api/chat` 形式へ変換して転送
- Ollama のレスポンスをクライアントが使用した API 形式（Chat Completions / Responses / Messages）へ変換して返す

### 対応状況

| AI エージェント | API | 状態 |
|-----------------|-----|------|
| OpenCode | `POST /v1/chat/completions` | 対応 |
| Codex | `POST /v1/responses` | 対応 |
| Claude Code | `POST /v1/messages` | 対応 |

## Features

| 機能 | 説明 |
|------|------|
| `POST /v1/chat/completions` | OpenCode 向けの OpenAI Chat Completions 互換エンドポイント（`max_tokens` / `tool_choice` / `tools` に対応） |
| `POST /v1/responses` | Codex 向けの OpenAI Responses API 互換エンドポイント。`input` / `instructions` / フラット形式の `tools` / `function_call` / `function_call_output` を受け付け、Responses 形式のレスポンス（および SSE イベント）を返す |
| `POST /v1/messages` | Claude Code 向け Anthropic Messages API 互換エンドポイント。text、client tool、tool result、named SSE に対応 |
| `GET /v1/models` | Ollama の `/api/tags` からモデル一覧を OpenAI 形式で返す |
| `GET /health` / `GET /v1/health` | ヘルスチェック（`{"status":"ok"}` を返す） |
| Streaming (SSE) | Chat Completions、Responses、Anthropic Messages の各wire formatでストリーミング応答 |
| Tool calling | OpenAI / Anthropic形式とOllama形式のclient tool callを双方向変換 |
| Thinking 制御 | `OLLAMA_THINK` で `false` / `true` / `low` / `medium` / `high` を指定。thinking本文は全クライアントで非公開 |
| `max_tokens` / `max_output_tokens` 変換 | Chat Completions の `max_tokens`、Responses の `max_output_tokens` を Ollama の `options.num_predict` に変換 |
| Content 正規化 | Chat Completions の `text` part、Responses の `input_text` / `output_text` / `text` part をプレーンテキストに正規化 |
| ThreadingHTTPServer | 複数リクエストの同時処理に対応 |
| タイムアウト設定 | 応答受信タイムアウト（6時間）やストリーミングアイドルタイムアウト（10分）を環境変数で設定可能 |
| Nginx 対応 | `X-Accel-Buffering: no` ヘッダを送出し、リバースプロキシ環境でもストリーミングが機能するよう配慮 |
| 環境変数設定 | 接続先・待ち受け先・タイムアウト・リクエストサイズ上限などを環境変数で柔軟に設定可能 |
| リクエストサイズ制限 | `/v1/responses` と `/v1/messages` のリクエストボディが上限（デフォルト 64MB）を超える場合は `413 Request Entity Too Large` を返す |

## Requirements

### Direct execution

- Python 3.10 以上
- Ollama（起動済み）
- OpenCode、Codex、Claude Codeのいずれか

ランタイムは Python 標準ライブラリだけで動作します。

### Installer

`install.sh` を使用する場合は、上記に加えて以下が必要です。

- Linux（systemd 搭載）
- root 権限
- `curl`
- `tar`
- `sha256sum`（`OLLAMA_AGENT_PROXY_SHA256` を指定する場合）

## Quick Start (Using Scripts)

`install.sh` / `uninstall.sh` を利用すると、GitHub の単一アーカイブからランタイム一式をダウンロードし、systemd サービスとしてインストール・アンインストールできます。`proxy.py`、`common.py`、`ollama.py`、`agents/` は必ず同じバージョンから配置されます。

1. リポジトリからスクリプトをダウンロードします：

```bash
curl -fsSL https://raw.githubusercontent.com/NBE03xxx/ollama-agent-proxy/main/install.sh -o install.sh
curl -fsSL https://raw.githubusercontent.com/NBE03xxx/ollama-agent-proxy/main/uninstall.sh -o uninstall.sh
```

2. 実行権限を付与します：

```bash
chmod +x install.sh uninstall.sh
```

3. `sudo` でインストールスクリプトを実行します：

```bash
sudo ./install.sh
```

特定タグまたはコミットとリリースの SHA-256 を指定する場合：

```bash
sudo OLLAMA_AGENT_PROXY_VERSION=<tag-or-commit> \
  OLLAMA_AGENT_PROXY_SHA256=<archive-sha256> \
  ./install.sh
```

`main` の開発用インストールで `OLLAMA_AGENT_PROXY_SHA256` を省略すると、警告後にハッシュ検証なしで続行します。タグまたはコミットを指定する場合、SHA-256 は必須です。

インストール時に、接続先・待ち受けポートなどの設定をインタラクティブに入力できます（Enter でデフォルト値を採用）。Ollama の動作確認、モデルの確認、ポート競合チェックが自動的に行われます。

### アンインストール

同様に `uninstall.sh` をダウンロードして実行権限を付与した上で、`sudo` で実行します：

```bash
sudo ./uninstall.sh
```

サービス停止、ファイル削除、systemd のクリーンアップが自動的に行われます。既存の `override.conf` については、ホームディレクトリにバックアップを作成するかどうか確認されます。
ランタイムは `install-manifest.txt` に記載されたファイルだけが削除され、管理外ファイルは保持されます。

> **注意**: スクリプトは root 権限が必要であり、`sudo` で実行してください。Ollama そのものはアンインストール対象外です。

> **セキュリティ上の注意**: このプロキシは認証と TLS 終端を提供せず、デフォルトでは `0.0.0.0` で待ち受けます。信頼できる内部ネットワークで使用するか、`LISTEN_HOST=127.0.0.1` に変更してください。

## Configuration

主要な接続・待ち受け設定は、環境変数で上書きできます。`proxy.py` 内には各設定のデフォルト値が存在し、環境変数が未設定の場合に使用されます。

| 環境変数 | 説明 | デフォルト値 |
|----------|------|-------------|
| `OLLAMA_HOST` | Ollama サーバーの接続先（末尾に `/api/chat` が自動付加） | `http://127.0.0.1:11434` |
| `LISTEN_HOST` | プロキシの待ち受けアドレス | `0.0.0.0` |
| `LISTEN_PORT` | プロキシの待ち受けポート | `8000` |
| `CONNECT_TIMEOUT` | Ollama への接続確立タイムアウト（秒） | `30` |
| `READ_TIMEOUT` | Ollama からの応答受信タイムアウト（秒） | `21600`（6時間） |
| `STREAM_IDLE_TIMEOUT` | Responses API ストリーミング時のアイドルタイムアウト（秒）。この間応答が止まると、ストリームをタイムアウトとして終了します | `600`（10分） |
| `MAX_REQUEST_BYTES` | `/v1/responses` と `/v1/messages` で許容するリクエストボディの最大サイズ（バイト） | `67108864`（64MB） |
| `OLLAMA_KEEP_ALIVE` | Ollama へのリクエストに付与する `keep_alive` 値。モデルのメモリ保持期間を制御します | `30m` |
| `OLLAMA_THINK` | `false` / `true` または対応モデル向けの `low` / `medium` / `high`。不正値は起動エラーになります | `false` |
| `ANTHROPIC_HEARTBEAT_INTERVAL` | Claude Code向けSSEで、Ollamaが無音の間に`ping`を送る間隔（秒）。0より大きく300未満 | `60` |
| `DEBUG` | `1` / `true` / `yes` を設定すると、Ollama送信直前のメッセージ総数、各role・content型・文字数、system/developerの件数と位置をログへ出力します。本文は出力しません | 無効 |

### 内部接続動作

プロキシは `OLLAMA_HOST` に `/api/chat` を付加して Ollama と通信します。例えば、`OLLAMA_HOST=http://localhost:11434` の場合、実際には `http://localhost:11434/api/chat` へリクエストを送信します。

### タイムアウト設定

タイムアウトは環境変数で上書きできます（上の表を参照）。

- `CONNECT_TIMEOUT`：Ollama への接続確立タイムアウト
- `READ_TIMEOUT`：Ollama からの応答受信タイムアウト。長文生成や大規模な tool calling 処理に対応するため、デフォルトは比較的大きな値です
- `STREAM_IDLE_TIMEOUT`：Responses API のストリーミング応答中に、Ollama からこの時間何も受信できなかった場合にストリームをタイムアウトとして終了させます
- `ANTHROPIC_HEARTBEAT_INTERVAL`：Claude Codeの300秒watchdogより短い間隔でSSE `ping`を送り、長いthinking中の切断を防ぎます

## Running

### Direct execution

```bash
python3 proxy.py
```

環境変数を指定して実行：

```bash
OLLAMA_HOST=http://localhost:11434 LISTEN_PORT=8000 python3 proxy.py
```

起動時の出力例：

```
======================================================================
Ollama Agent Proxy
======================================================================
Server : Ollama Agent Proxy v1.1
Listen : http://0.0.0.0:8000
Ollama : http://127.0.0.1:11434/api/chat
======================================================================
```

## Agent Configuration

プロキシをデフォルト設定で起動した場合、各agentでは次の接続先を使用します。

| AI エージェント | ベース URL | 使用する API |
|-----------------|------------|--------------|
| OpenCode | `http://localhost:8000/v1` | Chat Completions (`/chat/completions`) |
| Codex | `http://localhost:8000/v1` | Responses API (`/responses`) |
| Claude Code | `http://localhost:8000` | Anthropic Messages API (`/v1/messages`) |

具体的な設定ファイル名や設定キーはagentのバージョンによって異なります。このプロキシ自身は API キーを検証しません。

Claude Code CLI の例：

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_AUTH_TOKEN=ollama-agent-proxy
export ANTHROPIC_MODEL=qwen3.6:27b-Q6
export ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen3.6:27b-Q6
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=131072
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
export CLAUDE_CODE_ATTRIBUTION_HEADER=0
export ENABLE_TOOL_SEARCH=false
claude
```

`ANTHROPIC_AUTH_TOKEN` はClaude Codeがgateway credentialとして送りますが、このproxyは値を検証しません。公開ネットワークでは認証reverse proxyとTLSを追加してください。Ollamaモデル名はClaude Codeの標準model pickerに出ない場合があるため、`ANTHROPIC_MODEL`またはsettingsの`model`で明示します。

このproxyはClaude Codeのsystem blockをOllama向けに統合するため、`CLAUDE_CODE_ATTRIBUTION_HEADER=0`でgateway attribution blockを省くことを推奨します。また、custom `ANTHROPIC_BASE_URL`ではTool Searchは通常upfront loadingへfallbackしますが、利用者設定による強制有効化を避けるため`ENABLE_TOOL_SEARCH=false`を推奨します。`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`だけではadaptive thinkingや明示的に有効化されたTool Searchを抑止できません。

## systemd Setup

### Automated installation (Recommended)

[Quick Start](#quick-start-using-scripts) の `install.sh` を利用すると、systemd サービスの作成・設定・起動が自動的に行われます。詳細な設計方針は [INSTALL.md](INSTALL.md)、[UNINSTALL.md](UNINSTALL.md) を参照してください。

### Manual setup

手動で systemd サービスを設定する場合、以下を参考にしてください。サービス名やパスは実環境に合わせて変更してください。

#### Main service file

`/etc/systemd/system/ollama-agent-proxy.service`：

```ini
[Unit]
Description=Ollama Agent Proxy
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/ollama-agent-proxy
ExecStart=/usr/bin/python3 /opt/ollama-agent-proxy/proxy.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

#### Environment override (drop-in)

環境変数は drop-in で設定するのが推奨です。`/etc/systemd/system/ollama-agent-proxy.service.d/override.conf`：

```ini
[Service]
Environment="OLLAMA_HOST=http://127.0.0.1:11434"
Environment="LISTEN_HOST=0.0.0.0"
Environment="LISTEN_PORT=8000"
```

> Ollama が別のマシンで動作している場合は `OLLAMA_HOST` を適切なアドレスに変更してください。

#### Service management

以下の例ではサービス名を `ollama-agent-proxy` としています。実環境のサービス名に合わせて読み替えてください。

```bash
# 有効化（自動起動）
sudo systemctl enable ollama-agent-proxy

# 開始
sudo systemctl start ollama-agent-proxy

# 設定変更後の再起動
sudo systemctl daemon-reload
sudo systemctl restart ollama-agent-proxy

# 状態確認
systemctl status ollama-agent-proxy

# ログ確認
journalctl -u ollama-agent-proxy -f
```

## API Usage

### Endpoint

プロキシは以下のエンドポイントを公開します：

| Method | Path | 説明 |
|--------|------|------|
| `POST` | `/v1/chat/completions` | OpenCode 向け OpenAI Chat Completions（非ストリーム・ストリーム両対応） |
| `POST` | `/v1/responses` | Codex 向け OpenAI Responses API（非ストリーム・ストリーム両対応） |
| `POST` | `/v1/messages` | Claude Code向けAnthropic Messages API（非ストリーム・named SSE対応） |
| `GET` | `/v1/models` | Ollama のモデル一覧（OpenAI 形式） |
| `GET` | `/health` ・ `/v1/health` | ヘルスチェック |
| `HEAD` | `/api/hello` | Claude Codeの接続ウォームアップ |

その他のパスへのリクエストは `404 Not Found` で返ります。

### Non-streaming request

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6:27b-Q6",
    "messages": [
      {
        "role": "user",
        "content": "こんにちは。"
      }
    ]
  }'
```

レスポンス（OpenAI 互換形式）：

```json
{
  "id": "chatcmpl-xxxxxxxxxxxxxxxx",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "qwen3.6:27b-Q6",
  "system_fingerprint": "fp_ollama",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "こんにちは！..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  }
}
```

### Streaming request

`stream: true` を指定すると Server-Sent Events (SSE) でストリーミング応答が返ります：

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -N \
  -d '{
    "model": "qwen3.6:27b-Q6",
    "stream": true,
    "messages": [
      {
        "role": "user",
        "content": "こんにちは。"
      }
    ]
  }'
```

#### ストリーミング時のヘッダ仕様

プロキシはストリーミング応答の HTTP ヘッダに以下の値を設定します：

- `Content-Type: text/event-stream; charset=utf-8` — SSE 形式のコンテンツタイプを示す
- `Cache-Control: no-cache` — クライアント側のキャッシュを無効化する
- `Connection: close` — 応答完了後に接続を閉じる
- `X-Accel-Buffering: no` — Nginx などのリバースプロキシがレスポンスをバッファリングしないよう指示する

特に `X-Accel-Buffering: no` は、プロキシの手前に Nginx を配置している場合に、ストリーミング応答がリアルタイムでクライアントに配信されるようにするために重要です。このヘッダがないと Nginx がレスポンスをバッファリングし、ストリーミングの効果が失われる可能性があります。

また、Ollama へのアップストリームリクエストでは、ストリーミング時の `Accept` ヘッダを `application/x-ndjson` に設定しています。これにより Ollama から行区切りの JSON（NDJSON）形式でストリーミングデータを受信します。非ストリーミング時は `application/json` が使用されます。

出力例：

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1700000000,"model":"qwen3.6:27b-Q6","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1700000000,"model":"qwen3.6:27b-Q6","choices":[{"index":0,"delta":{"content":"こんにちは"},"finish_reason":null}]}

...

data: [DONE]
```

### Tool calling

OpenAI 互換の tool calling を使用できます。プロキシが OpenAI 形式と Ollama 形式の間で変換を行います。

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6:27b-Q6",
    "messages": [
      {
        "role": "user",
        "content": "東京の天気は何ですか？"
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get the current weather for a location.",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {
                "type": "string",
                "description": "The city name"
              }
            },
            "required": ["location"]
          }
        }
      }
    ]
  }'
```

#### tool_choice の動作

`tool_choice` には以下の値を指定できます：

| 値 | 動作 |
|----|------|
| `"auto"` | Ollama に `tools` をそのまま渡し、モデルが判断します |
| `"none"` | Ollama への `tools` を送信しません。モデルは関数を呼び出せなくなります |
| `"required"` | ツール呼び出しを強制する指定は Ollama に渡されません。ツールは送信されますが、呼び出しはモデルの判断に委ねられます |
| `{"type": "function", "function": {"name": "..."}}` | 関数指定の強制として Ollama に渡します（Ollama のバージョンにより対応状況が異なります） |

### Responses API / Codex (`/v1/responses`)

OpenAI の [Responses API](https://platform.openai.com/docs/api-reference/responses) 互換エンドポイントを提供します。Codex CLI がこの形式を使用するため、Codex CLI を Ollama で利用する場合はこちらのエンドポイントを指定します。

#### リクエスト形式

Chat Completions とは入力の形式が異なります。

- `instructions`：システムプロンプト相当（任意）
- `input`：文字列、または item の配列。item は `message` / `function_call` / `function_call_output` / `reasoning` などの型を持ちます。`reasoning` 型の item は無視されます
- `input` 内の `system` / `developer` ロールの `message`：`instructions` とともに出現順で連結され、Ollama へ渡す履歴の先頭に単一の `system` メッセージとして配置されます
- `tools`：**フラット形式**（`name` / `description` / `parameters` が `function` キーの下にない形式）。プロキシが Ollama の `function` ネスト形式へ変換します（Chat Completions 形式のネスト済み tools もそのまま受け付けます）
- `max_output_tokens`：Chat Completions の `max_tokens` 相当

```bash
curl http://localhost:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6:27b-Q6",
    "instructions": "You are a helpful assistant.",
    "input": [
      {
        "type": "message",
        "role": "user",
        "content": [
          { "type": "input_text", "text": "東京の天気は何ですか？" }
        ]
      }
    ],
    "tools": [
      {
        "type": "function",
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "parameters": {
          "type": "object",
          "properties": {
            "location": { "type": "string", "description": "The city name" }
          },
          "required": ["location"]
        }
      }
    ]
  }'
```

#### 非ストリーミングレスポンス

Responses 形式の `response` オブジェクトを返します。`output` 配列には `message`（テキスト）と `function_call`（ツール呼び出し）の item が入ります。

```json
{
  "id": "resp_xxxxxxxxxxxxxxxxxx",
  "object": "response",
  "created_at": 1700000000,
  "model": "qwen3.6:27b-Q6",
  "status": "completed",
  "output": [
    {
      "type": "function_call",
      "id": "fc_xxxxxxxxxxxxxxxxxx",
      "status": "completed",
      "call_id": "call_0_xxxxxxxxxxxxxx",
      "name": "get_weather",
      "arguments": "{\"location\":\"Tokyo\"}"
    }
  ],
  "usage": {
    "input_tokens": 10,
    "output_tokens": 5,
    "total_tokens": 15
  },
  "error": null,
  "incomplete_details": null
}
```

#### ツール結果の再生（マルチターン）

クライアント（Codex CLI など）は、モデルが返した `function_call` と実行結果 `function_call_output` を次のリクエストの `input` に含めて送り返します。プロキシはこれを Ollama の履歴（assistant の `tool_calls` と `role: tool` のメッセージ）へ変換し、モデルがツール結果を踏まえた続きを生成できるようにします。

```json
{
  "input": [
    { "type": "message", "role": "user", "content": [{ "type": "input_text", "text": "東京の天気は？" }] },
    { "type": "function_call", "call_id": "call_0_abc", "name": "get_weather", "arguments": "{\"location\":\"Tokyo\"}" },
    { "type": "function_call_output", "call_id": "call_0_abc", "output": "{\"temp\":22,\"condition\":\"sunny\"}" },
    { "type": "message", "role": "user", "content": [{ "type": "input_text", "text": "大阪はどう？" }] }
  ]
}
```

#### ストリーミングレスポンス

`stream: true` を指定すると、Responses API の SSE イベント系列を返します。各イベントには `sequence_number` が付与されます。主なイベントは以下の通りです：

| イベント | 説明 |
|----------|------|
| `response.created` | レスポンスの開始（`status: in_progress`） |
| `response.in_progress` | 処理中の状態通知 |
| `response.output_item.added` | 出力 item（message / function_call）の追加 |
| `response.content_part.added` / `response.content_part.done` | メッセージのテキスト part の開始・終了 |
| `response.output_text.delta` / `response.output_text.done` | テキストの逐次配信・確定 |
| `response.function_call_arguments.delta` / `...done` | ツール引数の逐次配信・確定 |
| `response.output_item.done` | 出力 item の確定 |
| `response.completed` | 正常終了。`response.output` に全 item（message と function_call）を含む |
| `response.failed` | エラー発生時の終了（タイムアウト、Ollama のエラー、ストリーム途中での異常終了など）。`response.error` にエラー情報を含む |

- ツール呼び出しは Ollama の `id`（無い場合はストリーム上の安定した位置）で識別し、同一の呼び出しに対して複数の item が作成されないよう管理します。引数イベントは Ollama の `done` マーカーを受信した後にまとめて送信されます
- ストリーミング中に応答が `STREAM_IDLE_TIMEOUT` 以上止まると、`response.failed`（`code: stream_timeout`）で終了します
- Ollama のストリームに `error` フィールドや不正な JSON が含まれる場合も、`response.failed` で終了します
- Ollama が HTTP エラーを返した場合や接続できない場合も、通常の OpenAI エラーオブジェクトを単独で送るのではなく、`response.created` に続いて `response.failed` を送信します。`response.failed.response.error` の `code` は `upstream_error` または `connection_error` となり、最後に `data: [DONE]` を送信します

> **重要**：Codex CLI はツール呼び出しを `response.completed` の `output` から読み取ります。そのため、ストリーミングであっても `response.completed` に `output` を含めて送信します（`output: []` にしない）。

### Messages API / Claude Code (`/v1/messages`)

Claude Codeが送るAnthropic Messages APIのサブセットをOllamaへ変換します。`system`、text content block、`tools`、`tool_choice`、`tool_use`、`tool_result`、複数tool call、`max_tokens`、`stop_sequences`に対応します。トップレベルの`system`と履歴内の`system` / `developer`は、空でないtext内容を出現順に連結し、Ollamaへ渡す履歴の先頭に単一の`system`メッセージとして配置します。

```bash
curl http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "qwen3.6:27b-Q6",
    "max_tokens": 1024,
    "messages": [{"role":"user","content":"READMEを要約してください"}]
  }'
```

ストリーミングではAnthropicのnamed SSEを返します。基本系列は`message_start`、content block events、`message_delta`、`message_stop`であり、OpenAI形式の`data: [DONE]`は送りません。Ollamaから応答がない間は`ping`を送ります。

Ollamaはprompt token数をstream終端で報告するため、`message_start.usage.input_tokens`は0で開始し、最終`message_delta.usage`で`prompt_eval_count`と`eval_count`に基づく累積input/output token数を通知します。

Ollamaのmodel loadやprompt評価でupstreamのHTTP response自体がまだ開始していない間も、proxyは先にSSEを開始して`ping`を送ります。これによりcustom gatewayが300秒無通信になるClaude Codeのwatchdog条件を回避します。

Claude Codeから送られる`thinking`指定はOllamaへ直接転送しません。Ollamaのthinkingはサーバー側の`OLLAMA_THINK`だけで制御し、`message.thinking`はOpenCode、Codex、Claude Codeのすべてで破棄します。Anthropicの署名付きthinking blockには変換しません。thinkingのみが生成されて可視textもtool callもない場合は、thinking本文の代わりにproxy生成の短い診断文を返し、Claude Codeが無反応に見える状態を避けます。

`POST /v1/messages/count_tokens`は未実装で、Anthropic形式の`404 not_found_error`を返します。Claude Codeは推論endpointを利用する方式へフォールバックしますが、追加の推論requestを消費します。image、document、server-side tool、prompt caching、Anthropic固有beta機能は初期対応範囲外です。

### Claude Code / Ollamaの既知制約

- Claude Codeでcustom `ANTHROPIC_BASE_URL`を使うと、Remote Controlが無効になる版があります。fast modeの可用性確認やWebFetchのdomain safety確認など、一部通信はgatewayを通らず`api.anthropic.com`へ直接送られます
- Auto permission modeのsafety classifier requestもgatewayへ届く版があり、その判定は指定したOllama modelの能力に依存します。Anthropic側のclassifierが実行されるとは限りません
- `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`でもadaptive thinkingは残ります。本proxyはそのfieldを無視し、Ollama側thinkingは`OLLAMA_THINK`だけで決定します
- 一部のQwen系modelでは、tool-call-only assistant履歴の空contentや不完全なtool markupにより、Ollamaがtool callを通常textとして返す問題が報告されています。本proxyは安全性のため通常textを推測でtool callへ変換せず、Ollamaが返す構造化`message.tool_calls`だけを使用します
- `ENABLE_TOOL_SEARCH=true`などでTool Searchを強制すると、local model／proxyが完全対応していない`tool_reference` blockが使われる可能性があります。初期構成では`ENABLE_TOOL_SEARCH=false`を推奨します

### Model specification

モデルはプロキシの設定では固定せず、API リクエストの `model` フィールドで指定します。Ollama で読み込み済みの任意のモデル名を指定できます：

```json
{ "model": "qwen3.6:27b-Q6", ... }
{ "model": "llama3.1:8b", ... }
{ "model": "mistral:7b", ... }
```

## Limitations

- **特定 API 挙動への依存**：Ollama の `/api/chat` の特定のレスポンス形式を前提としています
- **対応クライアント**：対象は OpenCode、Codex、Claude Code CLIです。その他のOpenAI／Anthropic互換クライアントで完全な動作を保証するものではありません
- **Messages APIの範囲**：Claude Codeのclient tool loopに必要なサブセットを対象とし、token counting、image/document、server-side tool、prompt caching、署名付きthinkingは未対応です
- **認証機能なし**：認証、アクセス制御、TLS 終端は提供しません。プロキシは内部ネットワークでの利用を想定しています
- **リクエストサイズ制限**：`/v1/responses` と `/v1/messages` はデフォルトで64MBまでです。`/v1/chat/completions`には適用されません
- **`tool_choice` の制約**：`required` によるツール呼び出し強制は Ollama 側に渡されません（モデルの判断に委ねられます）
- **推論環境への依存**：推論性能や GPU 利用状況は Ollama、モデル、GPU、ドライバの環境に依存します

## Tested Environment

以下の構成で動作を確認しています：

| コンポーネント | 値 |
|---------------|-----|
| クライアント | OpenCode、Codex CLI、Claude Code CLI（実CLIスモークテストおよびprotocol自動テスト） |
| バックエンド | Ollama |
| モデル | Qwen3.6:27B-Q6 |
| OS | Linux |
| GPU | AMD / ROCm |

> Qwen3.6:27B-Q6 は検証時に使用したモデルです。プロキシ自体が特定のモデル専用というわけではありません。

## Background

このプロキシは、ローカル AI コーディングエージェント環境の検証プロジェクトから生まれました。OpenCode から Ollama を直接利用する構成で API 実装差異に起因する問題が発生したため中間層として開発し、その後 Codex の Responses API と Claude Code の Messages API に対応しました。

## License

This project is licensed under the MIT License.

See [LICENSE](LICENSE) for details.
