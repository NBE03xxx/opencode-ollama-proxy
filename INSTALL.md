# install.sh 設計方針

## 概要

`proxy.py` を systemd サービスとして常時稼働させるためのインストールスクリプト `install.sh` の設計方針です。

---

## 1. スクリプト名・実行条件

| 項目 | 値 |
|------|-----|
| ファイル名 | `install.sh` |
| 実行権限 | root 権限必須（sudo で実行） |
| 依存 OS | Linux（systemd 搭載） |
| 依存ソフトウェア | Python 3、Ollama（起動済み） |

---

## 2. proxy.py の保存先

**インストール先ディレクトリ**: `/opt/ollama-agent-proxy`

### ディレクトリ処理ロジック

1. `/opt/ollama-agent-proxy` が存在しない場合
   - `mkdir -p` で作成
   - 適切な権限を付与（owner: root, mode: 0755）

2. ディレクトリが既に存在する場合
   - 中に既存の `proxy.py` があるか確認
   - 存在する場合はユーザーに上書きを促す（y/n 確認）
   - 「n」の場合はインストールを中止（その際、作成した変更はすべて元に戻す）
    - 「y」の場合は上書きを行う

3. GitHub から `proxy.py` を取得
    - 取得先: `https://raw.githubusercontent.com/NBE03xxx/ollama-agent-proxy/main/proxy.py`
    - `curl -fsSL` でダウンロード
    - ダウンロード失敗時はインストールを中止（ロールバック）
    - 実行権限（0755）を付与

---

## 2-1. proxy.py の取得先

**GitHub Raw URL**: `https://raw.githubusercontent.com/NBE03xxx/ollama-agent-proxy/main/proxy.py`

インストールスクリプトは、上記の raw コンテントURL から `curl -fsSL` を用いて `proxy.py` をダウンロードします。

### ダウンロード処理ロジック

1. `curl -fsSL` で取得先 URL からダウンロード
2. HTTP エラー（404 など）やネットワークエラーで失敗した場合、インストールを中止しロールバック
3. ダウンロード成功時にファイルに実行権限（0755）を付与

---

## 3. 事前チェック（インストール前の確認フェーズ）

実際にファイルを書き込む前に、以下のチェックを実行します。いずれかのチェックでキャンセルされた場合、それまでに実行した変更はすべて元に戻し、クリーンな状態で終了します。

### 3-1. Ollama サービスの動作確認

`ollama list` コマンドを実行して、Ollama が起動しているか確認します。公式インストールスクリプトにより `ollama` は `/usr/local/bin/ollama`（または `/usr/bin/ollama`）に `root:root 755` でインストールされるため、root から実行可能です。

- **ollama コマンドが存在しない**
  - まず `curl -s http://127.0.0.1:11434/api/tags` で Ollama API に直接アクセスするフォールバックを試みます
  - こちらも失敗した場合はメッセージ出力後、キャンセル処理へ

- **Ollama に接続できない**
  - 「Ollama が起動していない可能性があります」と警告
  - インストールを続けるか確認（y/n）
  - 「n」の場合はキャンセル処理へ

### 3-2. モデルの確認

`ollama list`（またはフォールバックの API エンドポイント `GET /api/tags`）の出力から、モデル名に `qwen3.6` が含まれているか確認します。`ollama list` の第1列（NAME）に対して `grep -i 'qwen3\.6'` で部分一致させます（タグ部分 `:latest`, `:27b-Q6` などは含めてマッチ）。

- **qwen3.6 が見つからない**
  - 「qwen3.6 モデルがインストールされていません。インストールしてから続行することをお勧めします」と注意喚起
  - インストールを続けるか確認（y/n）
  - 「n」の場合はキャンセル処理へ

### 3-3. ポート競合の確認

ユーザーが入力した `LISTEN_PORT` が既に使用中かどうかを確認します。

**チェック方法**:

1. **優先**: `ss -tlnp` で確認
2. **フォールバック**: `/proc/net/tcp` を直接パース。ポート番号を16進数に変換し、STATE 列が `0A`（LISTEN）の行をマッチさせる

- **ポートが使用中の場合**
  - 「ポート XXXX は既に使用されています」と警告
  - 別のポートを入力するよう促す（再入力を繰り返し、空入力＝キャンセルした場合、キャンセル処理へ）
  - 空いているポートが入力されるまでループ

---

## 4. systemd サービス設定

### サービスファイル

**パス**: `/etc/systemd/system/ollama-agent-proxy.service`

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

### 設計上の判断

| 項目 | 方針 | 理由 |
|------|------|------|
| User/Group | root | `/opt` に配置しシステムサービスとして運用するため。 |
| Restart | always | 常時稼働要件を満たすため |
| RestartSec | 3秒 | 再起動間隔を短く設定して可用性を高める |
| PYTHONUNBUFFERED | 1 | ログ出力が journalctl でリアルタイムに確認できるようにする |

---

## 5. 環境変数（drop-in）

**パス**: `/etc/systemd/system/ollama-agent-proxy.service.d/override.conf`

README.md に記載されている推奨設定に従い、drop-in ファイルで環境変数を設定します。

```ini
[Service]
Environment="OLLAMA_HOST=http://127.0.0.1:11434"
Environment="LISTEN_HOST=0.0.0.0"
Environment="LISTEN_PORT=8000"
```

### インストール時のインタラクティブ設定

事前チェックフェーズでポート競合確認を含む入力を行います。デフォルト値を設定し、Enter でそのまま適用可能です：

| 環境変数 | デフォルト値 | 説明 |
|----------|-------------|------|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama サーバーの接続先 |
| `LISTEN_HOST` | `0.0.0.0` | プロキシの待ち受けアドレス |
| `LISTEN_PORT` | `8000` | プロキシの待ち受けポート（競合時は再入力） |

---

## 6. インストールフロー（全体的な処理順序）

```
【フェーズ1: 前提条件チェック】
1. root権限チェック
   → 失敗即終了（変更なし）
2. Python3 の存在確認
   → 失敗即終了（変更なし）
3. systemd の存在確認
   → 失敗即終了（変更なし）

【フェーズ2: 事前チェック】
4. Ollama サービスの動作確認（ollama list）
5. qwen3.6 モデルの有無確認
6. 環境変数のインタラクティブ入力（OLLAMA_HOST, LISTEN_HOST, LISTEN_PORT）
7. ポート競合の確認 → 使用中の場合は別のポートを再入力

   ※ フェーズ2でキャンセルされた場合、フェーズ1・2ではファイル変更を行わないため、
      クリーンな状態のまま終了できる。

【フェーズ3: ファイル書き込み】
8. 保存先ディレクトリの作成／確認
    ┣ ディレクトリ不存在 → mkdir -p
    ┗ ディレクトリ既存 → proxy.pyの上書き確認（y/n）
        → 「n」の場合: キャンセル処理へ（9以降をスキップ、10へ）

9. GitHub から proxy.py をダウンロードし、/opt/ollama-agent-proxy/ に配置・権限付与
10. systemd サービスファイルの生成
11. drop-in 環境変数設定の生成

【フェーズ4: サービス起動】
12. systemctl daemon-reload
13. systemctl enable（自動起動有効化）
14. systemctl start（サービス開始）
15. 状態確認・インストール完了メッセージ出力

【キャンセル処理: フェーズ3で中止された場合】
10. これまでに作成した変更をすべて元に戻す
    - コピーした proxy.py の削除
    - 新規作成したディレクトリの場合は削除
    - サービスファイル・drop-in が生成されていれば削除
    - daemon-reload を実行（必要に応じて）
```

---

## 7. キャンセル時のロールバック処理

フェーズ3（ファイル書き込みフェーズ）でユーザーが中止を選択した場合、あるいはその後の処理で失敗した場合、以下の変更を元に戻します。

| 変更内容 | ロールバック方法 |
|----------|-----------------|
| `/opt/ollama-agent-proxy/` の新規作成 | ディレクトリが空の場合のみ `rmdir` で削除 |
| `proxy.py` のダウンロード・上書き | 上書きの場合はバックアップから復元。新規ダウンロードの場合はファイル削除 |
| サービスファイルの生成 | `/etc/systemd/system/ollama-agent-proxy.service` を削除 |
| drop-in ファイルの生成 | `/etc/systemd/system/ollama-agent-proxy.service.d/` ディレクトリを削除 |
| daemon-reload の実行 | ロールバック後に再度 `systemctl daemon-reload` を実行 |

### バックアップ方針

既存の `proxy.py` が上書きされる場合、ダウンロード前に `.bak` 拡張子でバックアップを作成します。ロールバック時にはこのバックアップから復元します。

---

## 8. エラーハンドリング方針

| エラー | 対応 |
|--------|------|
| root権限なし | メッセージ出力後、即座に終了（exit 1）。変更は一切行わない。 |
| Python3 未インストール | メッセージ出力後、即座に終了（exit 1） |
| systemd 不可用 | メッセージ出力後、即座に終了（exit 1） |
| ディスク書き込み失敗 | エラーメッセージ出力後、ロールバック処理を実行し終了（exit 1） |
| サービス起動失敗 | エラーメッセージ出力し、`journalctl -u ollama-agent-proxy -f` の実行を提示（exit 1） |

---

## 9. アンインストール

現時点では未実装。install.sh の完成後に別途着手します。

---

## 10. 既知の制約・考慮事項

- **User/Group のカスタマイズ**: 現状 root を固定する方針です（将来的課題）
- **ログ出力先**: `journalctl` に依存する設計です（将来的課題）
- **Ollama モデル**: `qwen3.6` の有無は注意喚起のみで、強制インストールは行いません。ユーザーの判断に委ねます
- **ポート競合チェック方法**: 優先して `ss -tlnp` を使用し、利用できない場合は `/proc/net/tcp` の直接パース（16進数変換 + STATE=0A マッチ）でフォールバックします
- **ollama コマンドのフォールバック**: `command -v ollama` で存在確認後、`ollama list` が失敗する場合は `curl http://127.0.0.1:11434/api/tags` で Ollama API に直接アクセスします
- **proxy.py の取得方法**: ローカルコピーではなく、GitHub の raw コンテントURL から `curl -fsSL` でダウンロードします。取得先は `https://raw.githubusercontent.com/NBE03xxx/ollama-agent-proxy/main/proxy.py` です
- **モデル名検出方法**: `grep -i 'qwen3\.6'` で部分一致。タグ（`:latest`, `:27b-Q6` など）を含めてマッチさせるため、厳密なバージョン指定は行いません
