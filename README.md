# OpenCode Ollama Proxy

OpenCodeとOllamaの間に配置し、OpenAI互換APIの差異を吸収するための軽量なHTTPプロキシです。

```text
OpenCode
   ↓
OpenCode Ollama Proxy
   ↓
Ollama
   ↓
Local LLM
```

## Overview

OpenCodeからOllamaを直接利用する際、OpenAI互換APIの実装差異、特にTools / tool callingやthinkingに関する挙動が問題となる場合があります。

このプロジェクトでは、OpenCodeとOllamaの間にプロキシを配置し、APIリクエストとレスポンスを調整することで、ローカルLLMを開発エージェントから利用するための補助層として機能させます。

このproxyはLLM推論そのものを行うものではありません。

役割はあくまで、

```text
OpenCode
   │
   │ OpenAI-compatible API
   ▼
proxy.py
   │
   │ Ollama API
   ▼
Ollama
   │
   ▼
Local LLM
```

というAPI変換・調整層です。

## Features

* OpenAI互換API endpointの提供
* Ollama APIへのリクエスト転送
* streaming response対応
* Ollamaのthinking機能に関する調整
* 長時間のLLM処理を考慮したread timeout
* `ThreadingHTTPServer`による複数リクエストへの対応
* systemdによる常駐運用

## Requirements

* Linux
* Python 3
* Ollama
* OpenCodeなどのOpenAI互換APIクライアント

## Current Configuration

現在のバージョンでは、接続先やモデル名、listen addressなどの設定値は `proxy.py` に固定されています。

そのため、現時点では特定の環境を前提とした実装になっています。

設定値の環境変数化は今後の改善項目です。

## Tested Environment

このプロジェクトは、ローカルAIコーディングエージェント環境の検証の一環として開発しました。

検証時には、以下の構成で動作を確認しています。

* OpenCode
* Ollama
* Qwen3.6:27B-Q6
* Linux
* AMD GPU / ROCm環境

Qwen3.6:27B-Q6は検証時に使用したモデルであり、このproxy自体がQwen専用という意味ではありません。

## Running

proxy.pyを直接実行する場合：

```bash
python3 proxy.py
```

現在の検証環境では、proxyは `0.0.0.0:8000` で待ち受け、OllamaのAPIへリクエストを転送します。

Ollamaは通常、

```text
http://127.0.0.1:11434
```

で動作します。

## systemd

検証環境では、最終的にproxyをsystemdサービスとして常時起動する構成にしました。

サービスファイル：

```text
/etc/systemd/system/opencode-qwen-proxy.service
```

現在使用しているサービス定義の例：

```ini
[Unit]
Description=OpenCode Qwen Ollama Proxy
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=user
Group=user

WorkingDirectory=/opt/opencode-qwen-proxy
ExecStart=/usr/bin/python3 /opt/opencode-qwen-proxy/proxy.py

Restart=always
RestartSec=3

Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Start

```bash
sudo systemctl start opencode-qwen-proxy
```

### Enable at boot

```bash
sudo systemctl enable opencode-qwen-proxy
```

### Check status

```bash
systemctl status opencode-qwen-proxy
```

### Check logs

```bash
journalctl -u opencode-qwen-proxy
```

Follow logs in real time:

```bash
journalctl -u opencode-qwen-proxy -f
```

> 上記のサービス名、配置先、ユーザー名などは今回の検証環境に合わせたものです。

## Testing

proxyが起動していることを確認したうえで、OpenAI互換APIとしてリクエストを送信できます。

例：

```bash
curl http://<AI_SERVER>:8000/v1/chat/completions \
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

正常に動作すると、Ollamaで生成された応答がOpenAI互換形式で返ります。

## Background

このproxyは、ローカルAIコーディングエージェント環境の検証プロジェクトから生まれました。

検証では、OpenCodeからOllamaを直接利用する構成を試したところ、OpenAI互換APIの実装差異が問題となりました。

そのため、OpenCodeとOllamaの間にAPI互換性を補助するproxyを配置し、実際の開発エージェント環境で動作を検証しました。

その後、このproxyを独立したコンポーネントとして利用できるよう、検証用リポジトリから分離しました。

## Limitations

現在のバージョンには以下の制限があります。

* 設定値が `proxy.py` に固定されています。
* 特定のOllama APIの挙動を前提としています。
* すべてのOpenAI互換APIクライアントやサーバーで動作することを保証するものではありません。
* 認証、アクセス制御、TLS終端などの機能は提供していません。
* proxy自体はLLM推論を行いません。

推論性能やGPU性能は、Ollama、使用するモデル、GPU、ドライバ、ROCmなどの実行環境に依存します。

## Future Improvements

今後、以下の改善を予定しています。

* 接続先Ollamaサーバーの環境変数化
* 使用モデルの環境変数化
* listen address / portの環境変数化
* 設定方法の整理
* systemdサービス定義の汎用化
* エラー処理およびログ出力の改善

## License

This project is licensed under the MIT License.

See [LICENSE](LICENSE) for details.
