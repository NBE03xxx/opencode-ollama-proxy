# OpenCode Qwen Ollama Proxy

OpenCodeとOllamaの間で、OpenAI互換APIの差異を吸収するために使用した軽量なプロキシです。

## 目的

今回の検証では、OpenCodeからOllamaを直接利用した際に、OpenAI互換APIの実装差異、特に **Tools / tool calling** に関する問題が発生しました。

そのため、OpenCodeとOllamaを分離することを目的とするのではなく、

* OpenCodeから受け取ったOpenAI互換APIリクエストを処理する
* Ollamaが扱える形式に調整する
* OllamaからのレスポンスをOpenCodeへ返す

というAPI互換性の補助層としてproxyを配置しました。

構成は次のとおりです。

| コンポーネント        | 役割                |
| -------------- | ----------------- |
| OpenCode       | ローカル開発エージェント      |
| proxy.py       | OpenAI互換APIの差異を吸収 |
| Ollama         | LLM推論サーバー         |
| Qwen3.6:27B-Q6 | 使用したローカルモデル       |

```text
OpenCode
   ↓
proxy.py
   ↓
Ollama
   ↓
Qwen3.6:27B-Q6
```

## 配置

今回の検証では、proxyはAIサーバー上に配置しました。

```text
/opt/opencode-qwen-proxy/
├── proxy.py
└── ...
```

proxyは `0.0.0.0:8000` で待ち受け、OllamaのAPIへリクエストを転送します。

Ollamaは通常、

```text
http://127.0.0.1:11434
```

で動作します。

## 主な役割

proxyでは、OpenCodeとOllamaの間で発生したAPI上の差異を吸収します。

今回の環境では特に、Ollamaのthinking機能についてOpenCodeとの組み合わせを調整し、推論リクエストを安定して処理できるようにしました。

また、長時間のLLM処理を考慮して、通常の短いHTTPリクエストより長い読み取りタイムアウトを設定しています。

さらに、複数のリクエストを処理できるよう `ThreadingHTTPServer` を使用しています。

## systemdによるサービス化

検証の途中では手動でproxyを起動していましたが、最終的にはsystemdサービスとして常時起動する構成にしました。

サービスファイル：

```text
/etc/systemd/system/opencode-qwen-proxy.service
```

主な設定は次のとおりです。

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

### 起動

```bash
sudo systemctl start opencode-qwen-proxy
```

### 自動起動

```bash
sudo systemctl enable opencode-qwen-proxy
```

### 状態確認

```bash
systemctl status opencode-qwen-proxy
```

### ログ確認

```bash
journalctl -u opencode-qwen-proxy
```

リアルタイムで確認する場合：

```bash
journalctl -u opencode-qwen-proxy -f
```

## Ollamaとの関係

proxyはOllamaそのものを置き換えるものではありません。

役割はあくまで、

```text
OpenCode
   │
   │ OpenAI互換API
   ▼
proxy.py
   │
   │ Ollama API
   ▼
Ollama
   │
   ▼
Qwen3.6:27B-Q6
```

というAPI変換・調整層です。

そのため、Ollama単体でも利用できます。

## 動作確認

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

## 今回の検証での位置付け

このproxyは、LLMそのものの性能を向上させるために導入したものではありません。

今回の目的は、ローカルLLMをOpenCodeなどの開発エージェントから実用的に利用できるかを確認することでした。

その過程で、OpenCodeとOllamaを直接接続した場合のOpenAI互換APIの差異が問題となったため、proxyを導入しました。

したがって、今回の最終構成ではproxyも含めて評価しています。

## 注意点

このproxyは今回の環境に合わせた検証用の実装です。

特定のAPI仕様やOllamaの挙動を前提としているため、すべてのOpenAI互換APIサーバーでそのまま利用できることを保証するものではありません。

また、proxy自体はLLM推論を行いません。推論性能やGPU性能はOllamaおよび使用するモデル、GPU、ROCm環境に依存します。

