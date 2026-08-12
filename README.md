# OpenCode Ollama Proxy

OpenAI互換APIクライアント（OpenCode など）から Ollama を利用するための軽量なHTTPプロキシです。

```text
OpenCode（OpenAI互換APIクライアント）
   ↓
OpenCode Ollama Proxy
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
OpenCode
   │
   │ POST /v1/chat/completions (OpenAI-compatible)
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

- クライアントは `POST /v1/chat/completions` に OpenAI 互換形式でリクエストを送信
- プロキシが Ollama の `/api/chat` へ変換して転送
- Ollama のレスポンスを OpenAI 互換形式へ変換してクライアントに返す

## Features

| 機能 | 説明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI 互換の chat completions エンドポイント |
| Streaming (SSE) | `stream: true` を指定すると Server-Sent Events でストリーミング応答 |
| Tool calling | OpenAI 形式 ⇔ Ollama 形式の tool call 双方向変換 |
| Thinking 制御 | リクエスト時に `"think": false` を設定（Qwen thinking の無効化） |
| `max_tokens` 変換 | `max_tokens` を Ollama の `options.num_predict` に変換 |
| Content 正規化 | OpenAI の content parts（配列形式）を plain text に正規化 |
| ThreadingHTTPServer | 複数リクエストの同時処理に対応 |
| 環境変数設定 | 接続先・待ち受け先を環境変数で柔軟に設定可能 |

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

- Linux
- Python 3
- Ollama（起動済み）
- OpenCode など OpenAI 互換 API クライアント

## Configuration

主要な接続・待ち受け設定は、環境変数で上書きできます。`proxy.py` 内には各設定のデフォルト値が存在し、環境変数が未設定の場合に使用されます。

| 環境変数 | 説明 | デフォルト値 |
|----------|------|-------------|
| `OLLAMA_HOST` | Ollama サーバーの接続先（末尾に `/api/chat` が自動付加） | `http://127.0.0.1:11434` |
| `LISTEN_HOST` | プロキシの待ち受けアドレス | `0.0.0.0` |
| `LISTEN_PORT` | プロキシの待ち受けポート | `8000` |

### 内部接続動作

プロキシは `OLLAMA_HOST` に `/api/chat` を付加して Ollama と通信します。例えば、`OLLAMA_HOST=http://localhost:11434` の場合、実際には `http://localhost:11434/api/chat` へリクエストを送信します。

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
OpenCode / Ollama proxy
======================================================================
Server : OpenCode/Ollama native tool-calling proxy
Listen : http://0.0.0.0:8000
Ollama : http://127.0.0.1:11434/api/chat
======================================================================
```

## systemd Setup

常時稼働させる場合は、systemd サービスとして運用できます。以下は一般的な設定例です。サービス名やパスは実環境に合わせて変更してください。

### Main service file

`/etc/systemd/system/opencode-ollama-proxy.service`：

```ini
[Unit]
Description=OpenCode Ollama Proxy
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=<user>
Group=<group>
WorkingDirectory=/path/to/opencode-ollama-proxy
ExecStart=/usr/bin/python3 /path/to/opencode-ollama-proxy/proxy.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Environment override (drop-in)

環境変数は drop-in で設定するのが推奨です。`/etc/systemd/system/opencode-ollama-proxy.service.d/override.conf`：

```ini
[Service]
Environment="OLLAMA_HOST=http://127.0.0.1:11434"
Environment="LISTEN_HOST=0.0.0.0"
Environment="LISTEN_PORT=8000"
```

> Ollama が別のマシンで動作している場合は `OLLAMA_HOST` を適切なアドレスに変更してください。

### Service management

以下の例ではサービス名を `opencode-ollama-proxy` としています。実環境のサービス名に合わせて読み替えてください。

```bash
# 有効化（自動起動）
sudo systemctl enable opencode-ollama-proxy

# 開始
sudo systemctl start opencode-ollama-proxy

# 設定変更後の再起動
sudo systemctl daemon-reload
sudo systemctl restart opencode-ollama-proxy

# 状態確認
systemctl status opencode-ollama-proxy

# ログ確認
journalctl -u opencode-ollama-proxy -f
```

## API Usage

### Endpoint

プロキシは以下の OpenAI 互換エンドポイントを公開します：

| Method | Path | 説明 |
|--------|------|------|
| `POST` | `/v1/chat/completions` | Chat completion（非ストリーム・ストリーム両対応） |

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

出力例：

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1700000000,"model":"qwen3.6:27b-Q6","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1700000000,"model":"qwen3.6:27b-Q6","choices":[{"index":0,"delta":{"content":"こんにちは"},"finish_reason":null}]}

...

data: [DONE]
```

### Tool calling

OpenAI 互換の tool calling を使用できます。プロキシが OpenAI 形式と Ollama 形式の間で変換を行います：

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

### Model specification

モデルはプロキシの設定では固定せず、API リクエストの `model` フィールドで指定します。Ollama で読み込み済みの任意のモデル名を指定できます：

```json
{ "model": "qwen3.6:27b-Q6", ... }
{ "model": "llama3.1:8b", ... }
{ "model": "mistral:7b", ... }
```

## Limitations

- **特定 API 挙動への依存**：Ollama `/api/chat` の特定のレスポンス形式を前提としています
- **クライアント互換性**：すべての OpenAI 互換 API クライアントで完全な動作を保証するものではありません
- **認証機能なし**：認証、アクセス制御、TLS 終端は提供しません。プロキシは内部ネットワークでの利用を想定しています
- **推論環境への依存**：推論性能や GPU 利用状況は Ollama、モデル、GPU、ドライバ環境に依存します

## Tested Environment

以下の構成で動作を確認しています：

| コンポーネント | 値 |
|---------------|-----|
| クライアント | OpenCode |
| バックエンド | Ollama |
| モデル | Qwen3.6:27B-Q6 |
| OS | Linux |
| GPU | AMD / ROCm |

> Qwen3.6:27B-Q6 は検証時に使用したモデルです。プロキシ自体が特定のモデル専用というわけではありません。

## Background

このプロキシは、ローカル AI コーディングエージェント環境の検証プロジェクトから生まれました。OpenCode から Ollama を直接利用する構成で API 実装差異に起因する問題が発生したため、中間層として API 変換プロキシを開発しました。その後、独立したコンポーネントとして分離・公開しています。

## License

This project is licensed under the MIT License.

See [LICENSE](LICENSE) for details.
