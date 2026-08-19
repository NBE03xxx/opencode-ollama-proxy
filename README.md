# Ollama Agent Proxy

OpenAI互換APIクライアント（OpenCode など）から Ollama を利用するための軽量なHTTPプロキシです。

```text
OpenCode（OpenAI互換APIクライアント）
   ↓
Ollama Agent Proxy
   ↓
Ollama
   ↓
Local LLM
```

## Overview

OpenCode などの OpenAI 互換 API クライアントから Ollama を直接利用する場合、API の実装差異（Tools / tool calling、thinking、リクエスト/レスポンス形式など）が問題となることがあります。

このプロキシは、クライアントからの OpenAI 互換 API リクエストを Ollama の `/api/chat` 形式へ変換し、Ollama のレスポンスを OpenAI 互換形式へ変換して返すことで、API 間の差異を吸収します。

**役割はあくまで API 変換・調整層です。** LLM 推論そのものを行うものではありません。

## Architecture

```text
OpenCode / Codex CLI
    │
    │ POST /v1/chat/completions (OpenAI Chat Completions)
    │ POST /v1/responses        (OpenAI Responses API)
    ▼
proxy.py
    │
    │ POST /api/chat (Ollama native)
    ▼
Ollama
    │
    ▼
Local LLM
```

- クライアントは `POST /v1/chat/completions`（OpenAI Chat Completions）または `POST /v1/responses`（OpenAI Responses API）にリクエストを送信
- プロキシがリクエストを Ollama の `/api/chat` 形式へ変換して転送
- Ollama のレスポンスをクライアントが使用した API 形式（Chat Completions / Responses）へ変換して返す

## Features

| 機能 | 説明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI Chat Completions 互換エンドポイント（`max_tokens` / `tool_choice` / tools に対応） |
| `POST /v1/responses` | OpenAI Responses API 互換エンドポイント（Codex CLI 対応）。`input` / `instructions` / フラット形式の `tools` / `function_call` / `function_call_output` を受け付け、Responses 形式のレスポンス（および SSE イベント）を返す |
| `GET /v1/models` | Ollama の `/api/tags` からモデル一覧を OpenAI 形式で返す |
| `GET /health` / `GET /v1/health` | ヘルスチェック（`{"status":"ok"}` を返す） |
| Streaming (SSE) | `stream: true` を指定すると Server-Sent Events でストリーミング応答（Chat Completions は `chat.completion.chunk`、Responses は `response.*` イベント系列） |
| Tool calling | OpenAI 形式 ⇔ Ollama 形式の tool call 双方向変換（Chat Completions と Responses の両形式に対応） |
| Thinking 制御 | Ollama リクエストに `think` フラグを設定。環境変数 `OLLAMA_THINK` で ON/OFF を切り替え可能（デフォルトは OFF） |
| `max_tokens` / `max_output_tokens` 変換 | Chat Completions の `max_tokens`、Responses の `max_output_tokens` を Ollama の `options.num_predict` に変換 |
| Content 正規化 | OpenAI の content parts（配列形式の `input_text` / `output_text` / `text`）をプレーンテキストに正規化 |
| ThreadingHTTPServer | 複数リクエストの同時処理に対応 |
| タイムアウト設定 | 応答受信タイムアウト（6時間）やストリーミングアイドルタイムアウト（10分）を環境変数で設定可能 |
| Nginx 対応 | `X-Accel-Buffering: no` ヘッダを送出し、リバースプロキシ環境でもストリーミングが機能するよう配慮 |
| 環境変数設定 | 接続先・待ち受け先・タイムアウト・リクエストサイズ上限などを環境変数で柔軟に設定可能 |
| リクエストサイズ制限 | リクエストボディが上限（デフォルト 64MB）を超える場合は `413 Request Entity Too Large` を返す |

### モデル指定について

**モデルはプロキシ側では固定しません。** API リクエストの `model` フィールドで指定したモデル名がそのまま Ollama へ渡されます。

```json
{
  "model": "qwen3.6:27b-Q6",
  "messages": [...]
}
```

この設計により、プロキシの設定を変更せずに任意の Ollama モデルを切り替えて利用できます。

## Requirements

- Linux（systemd 搭載）
- Python 3
- curl
- Ollama（起動済み）
- OpenCode など OpenAI 互換 API クライアント

## Quick Start (Using Scripts)

`install.sh` / `uninstall.sh` を利用すると、GitHub からファイルをダウンロードして systemd サービスとして自動的にインストール・アンインストールできます。

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

インストール時に、接続先・待ち受けポートなどの設定をインタラクティブに入力できます（Enter でデフォルト値を採用）。Ollama の動作確認、モデルの確認、ポート競合チェックが自動的に行われます。

### アンインストール

同様に `uninstall.sh` をダウンロードして実行権限を付与した上で、`sudo` で実行します：

```bash
sudo ./uninstall.sh
```

サービス停止、ファイル削除、systemd のクリーンアップが自動的に行われます。既存の `override.conf` については、ホームディレクトリにバックアップを作成するかどうか確認されます。

> **注意**: スクリプトは root 権限が必要であり、`sudo` で実行してください。Ollama そのものはアンインストール対象外です。

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
| `MAX_REQUEST_BYTES` | 許容するリクエストボディの最大サイズ（バイト）。超過すると `413` を返します | `67108864`（64MB） |
| `OLLAMA_KEEP_ALIVE` | Ollama へのリクエストに付与する `keep_alive` 値。モデルのメモリ保持期間を制御します | `30m` |
| `OLLAMA_THINK` | `1` / `true` / `yes` を設定すると Ollama に `think: true` を送信し、思考モードを有効にします。それ以外は `think: false` | 無効（`think: false`） |
| `DEBUG` | `1` / `true` / `yes` を設定すると、リクエストメッセージのプレビューと Ollama 送信ボディの詳細ログを出力します。デフォルトでは無効です | 無効 |

### 内部接続動作

プロキシは `OLLAMA_HOST` に `/api/chat` を付加して Ollama と通信します。例えば、`OLLAMA_HOST=http://localhost:11434` の場合、実際には `http://localhost:11434/api/chat` へリクエストを送信します。

### タイムアウト設定

タイムアウトは環境変数で上書きできます（上の表を参照）。

- `CONNECT_TIMEOUT`：Ollama への接続確立タイムアウト
- `READ_TIMEOUT`：Ollama からの応答受信タイムアウト。長文生成や大規模な tool calling 処理に対応するため、デフォルトは比較的大きな値です
- `STREAM_IDLE_TIMEOUT`：Responses API のストリーミング応答中に、Ollama からこの時間何も受信できなかった場合にストリームをタイムアウトとして終了させます

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
Server : Ollama Agent Proxy (OpenAI-compatible) v2
Listen : http://0.0.0.0:8000
Ollama : http://127.0.0.1:11434/api/chat
======================================================================
```

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
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions（非ストリーム・ストリーム両対応） |
| `POST` | `/v1/responses` | OpenAI Responses API（非ストリーム・ストリーム両対応、Codex CLI 対応） |
| `GET` | `/v1/models` | Ollama のモデル一覧（OpenAI 形式） |
| `GET` | `/health` ・ `/v1/health` | ヘルスチェック |

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

### Responses API (`/v1/responses`)

OpenAI の [Responses API](https://platform.openai.com/docs/api-reference/responses) 互換エンドポイントを提供します。Codex CLI がこの形式を使用するため、Codex CLI を Ollama で利用する場合はこちらのエンドポイントを指定します。

#### リクエスト形式

Chat Completions とは入力の形式が異なります。

- `instructions`：システムプロンプト相当（任意）
- `input`：文字列、または item の配列。item は `message` / `function_call` / `function_call_output` / `reasoning` などの型を持ちます。`reasoning` 型の item は無視されます
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

> **重要**：Codex CLI はツール呼び出しを `response.completed` の `output` から読み取ります。そのため、ストリーミングであっても `response.completed` に `output` を含めて送信します（`output: []` にしない）。

### Model specification

モデルはプロキシの設定では固定せず、API リクエストの `model` フィールドで指定します。Ollama で読み込み済みの任意のモデル名を指定できます：

```json
{ "model": "qwen3.6:27b-Q6", ... }
{ "model": "llama3.1:8b", ... }
{ "model": "mistral:7b", ... }
```

## Limitations

- **特定 API 挙動への依存**：Ollama の `/api/chat` の特定のレスポンス形式を前提としています
- **クライアント互換性**：すべての OpenAI 互換 API クライアントで完全な動作を保証するものではありません
- **認証機能なし**：認証、アクセス制御、TLS 終端は提供しません。プロキシは内部ネットワークでの利用を想定しています
- **リクエストサイズ制限**：リクエストボディはデフォルトで 64MB（`MAX_REQUEST_BYTES` で変更可能）までです。これを超えるリクエストは `413` で拒否されます
- **`tool_choice` の制約**：`required` によるツール呼び出し強制は Ollama 側に渡されません（モデルの判断に委ねられます）
- **推論環境への依存**：推論性能や GPU 利用状況は Ollama、モデル、GPU、ドライバの環境に依存します

## Tested Environment

以下の構成で動作を確認しています：

| コンポーネント | 値 |
|---------------|-----|
| クライアント | OpenCode、Codex CLI |
| バックエンド | Ollama |
| モデル | Qwen3.6:27B-Q6 |
| OS | Linux |
| GPU | AMD / ROCm |

> Qwen3.6:27B-Q6 は検証時に使用したモデルです。プロキシ自体が特定のモデル専用というわけではありません。

## Background

このプロキシは、ローカル AI コーディングエージェント環境の検証プロジェクトから生まれました。OpenCode から Ollama を直接利用する構成で API 実装差異に起因する問題が発生したため、中間層として API 変換プロキシを開発しました。その後、独立したコンポーネントとして分離し、公開しています。

## License

This project is licensed under the MIT License.

See [LICENSE](LICENSE) for details.
