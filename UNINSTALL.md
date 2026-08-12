# uninstall.sh 設計方針

## 概要

`install.sh` でインストールされた `proxy.py`（systemd サービス）をクリーンにアンインストールするためのスクリプト `uninstall.sh` の設計方針です。

---

## 1. スクリプト名・実行条件

| 項目 | 値 |
|------|-----|
| ファイル名 | `uninstall.sh` |
| 実行権限 | root 権限必須（sudo で実行） |
| 依存 OS | Linux（systemd 搭載） |
| 依存ソフトウェア | なし（インストール済みであることが前提） |

---

## 2. アンインストール対象

インストール時に作成された以下のリソースを削除します。

| 対象 | パス |
|------|------|
| systemd サービスファイル | `/etc/systemd/system/opencode-ollama-proxy.service` |
| drop-in ディレクトリ（環境変数） | `/etc/systemd/system/opencode-ollama-proxy.service.d/` |
| インストールディレクトリ | `/opt/opencode-ollama-proxy/` |

---

## 3. 事前チェック（アンインストール前の確認フェーズ）

実際にファイルを削除する前に、以下のチェックを実行します。いずれかのチェックでキャンセルされた場合、変更は一切行わずに終了します。

### 3-1. root 権限の確認

スクリプトが root 権限で実行されているか確認します。

- **root でない場合**
  - 「このスクリプトは root 権限（sudo）で実行してください」とメッセージ出力後、即座に終了（exit 1）

### 3-2. インストール済みの確認

アンインストール対象となる各リソースが存在するか確認します。

**チェック項目**:
1. `/etc/systemd/system/opencode-ollama-proxy.service` の存在確認
2. `/etc/systemd/system/opencode-ollama-proxy.service.d/` ディレクトリの存在確認
3. `/opt/opencode-ollama-proxy/proxy.py` の存在確認

**判定ロジック**:

| サービスファイル | drop-in ディレクトリ | proxy.py | 判定 |
|------------------|---------------------|----------|------|
| 不存在 | 不存在 | 不存在 | インストールされていない → メッセージ出力後、終了（exit 0） |
| その他 (1つ〜2つが存在) | — | — | 不完全な状態 → 警告 + y/n 確認 |
| 存在 | 存在 | 存在 | 完全インストール → 処理続行 |

- **完全に不存在の場合**
  - 「OpenCode Ollama Proxy はインストールされていないようです」とメッセージ出力後、終了（exit 0）

- **不完全な状態が検出された場合 (1つ〜2つのリソースのみが存在)**
  - 「不完全な状態が検出されました。以下のリソースを削除してもよろしいですか？」と警告
  - 存在するリソースの一覧を表示
  - ユーザーに確認（y/n）
  - 「n」の場合は終了（exit 0）

### 3-3. サービス稼働中の確認

`systemctl is-active opencode-ollama-proxy` でサービスの稼働状態を確認します。

- **サービスが active の場合**
  - 「OpenCode Ollama Proxy は現在稼働中です。アンインストールするとサービスが停止します」と警告
  - ユーザーに確認（y/n）
  - 「n」の場合は終了（exit 0）

### 3-4. drop-in ファイルのバックアップ確認

`/etc/systemd/system/opencode-ollama-proxy.service.d/override.conf` が存在する場合、ユーザーが手動で書き換えや追加設定を行っている可能性があるため、削除前にホームディレクトリへバックアップを取得するかどうかを確認します。

**判定ロジック**:

1. `override.conf` の存在確認
2. **存在しない場合**
   - スキップして次のチェックへ進む
3. **存在する場合**
    - ファイルの内容を表示（または行数・最終更新日などの情報を出力）
    - 実行元ユーザーのホームディレクトリにバックアップを取得するか確認（y/n）：
      - バックアップ先: `<実行元ユーザーのホーム>/opencode-ollama-proxy-backup/override.conf`
      - `sudo` で実行した場合は `$SUDO_USER` のホームが使用される（例: `/home/yoshimi/opencode-ollama-proxy-backup/override.conf`）
      - 「y」の場合: `<実行元ユーザーのホーム>/opencode-ollama-proxy-backup/` を作成してコピー
      - 「n」の場合: バックアップなしで処理続行

### 3-5. インストールディレクトリの中身確認

`/opt/opencode-ollama-proxy/` が存在する場合、中身のファイルを確認します。

- **予期しないファイルが存在する場合**
  - 「インストールディレクトリに予期しないファイルが含まれています」と警告
  - 含まれるファイルの一覧を表示
  - ディレクトリ全体を削除する場合は確認（y/n）、デフォルトは proxy.py のみ削除しディレクトリを残す
  - 「n」の場合はディレクトリの削除をスキップする旨を出力して処理続行

### 3-6. アンインストール内容の確認

上記チェックの結果に基づき、削除対象リソースの一覧を表示し、ユーザーの最終承認を得ます。

```
以下のリソースが削除されます:

  [ ] /etc/systemd/system/opencode-ollama-proxy.service
  [ ] /etc/systemd/system/opencode-ollama-proxy.service.d/
  [ ] /opt/opencode-ollama-proxy/proxy.py
    ※ ディレクトリには予期しないファイルが含まれているため、ディレクトリ自体は残します

本当にアンインストールしますか？ (y/n)
```

- 「n」の場合は終了（exit 0）
- 「y」の場合のみ削除処理へ進む

---

## 4. アンインストールフロー（全体的な処理順序）

```
【フェーズ1: 前提条件チェック】
1. root権限チェック
   → 失敗即終了（変更なし）

【フェーズ2: インストール状態確認】
2. サービスファイル・drop-inディレクトリ・proxy.py の存在確認
    → 全て不存在: メッセージ出力後、終了（exit 0）
3. 不完全な状態の検出
    → 警告 + y/n 確認
4. サービス稼働中か確認
    → active の場合: 警告 + y/n 確認

【フェーズ3: 設定ファイルバックアップ確認】
5. override.conf の存在確認
    → 不存在: スキップ
    → 存在: ホームディレクトリへのバックアップを促す（y/n）

【フェーズ4: ディレクトリ内容確認】
6. /opt/opencode-ollama-proxy/ の中身を確認
    → 予期しないファイルがある場合: 警告、削除範囲の調整

【フェーズ5: ユーザー最終承認】
7. 削除対象リソースの一覧表示（調整済み）
8. アンインストール実行の最終確認（y/n）
    → 「n」の場合: 終了（exit 0）

【フェーズ6: サービス停止】
9. systemctl stop opencode-ollama-proxy
    → サービスが存在しない・既に停止している場合はスキップ
10. systemctl disable opencode-ollama-proxy
    → 無効化されていない場合はスキップ

【フェーズ7: ファイル削除】
11. /etc/systemd/system/opencode-ollama-proxy.service を削除
12. /etc/systemd/system/opencode-ollama-proxy.service.d/ ディレクトリを削除
13. /opt/opencode-ollama-proxy/proxy.py を削除
    ※ 予期しないファイルがない場合はディレクトリ全体を削除

【フェーズ8: クリーンアップ】
14. systemctl daemon-reload
15. アンインストール完了メッセージ出力
```

---

## 5. サービス停止処理

### 5-1. stop 処理

`systemctl stop opencode-ollama-proxy` を実行してサービスを停止します。

- **サービスが存在しない場合**
  - エラーを抑制し、スキップメッセージを出力
- **既に停止している場合**
  - エラーを抑制し、スキップメッセージを出力

### 5-2. disable 処理

`systemctl disable opencode-ollama-proxy` を実行して自動起動を無効化します。

- **既に無効化されている場合**
  - エラーを抑制し、スキップメッセージを出力

---

## 6. ファイル削除処理

### 6-1. サービスファイルの削除

`/etc/systemd/system/opencode-ollama-proxy.service` を `rm -f` で削除します。

- **既に存在しない場合**
  - スキップメッセージを出力

### 6-2. drop-in ディレクトリの削除

`/etc/systemd/system/opencode-ollama-proxy.service.d/` ディレクトリ全体を `rm -rf` で削除します。

- **既に存在しない場合**
  - スキップメッセージを出力

### 6-3. インストールディレクトリの処理

フェーズ4の確認結果に基づいて処理を分岐します。

#### 予期しないファイルがない場合

`/opt/opencode-ollama-proxy/` ディレクトリ全体を `rm -rf` で削除します。

#### 予期しないファイルがある場合

`proxy.py` のみを `rm` で削除し、ディレクトリ自体は残します。ユーザーに「ディレクトリ /opt/opencode-ollama-proxy/ は予期しないファイルが含まれているためそのまま残しました」とメッセージを出力します。

- **ディレクトリが既に存在しない場合**
  - スキップメッセージを出力

---

## 7. クリーンアップ処理

### 7-1. daemon-reload

`systemctl daemon-reload` を実行して systemd に変更を反映させます。

- **失敗した場合**
  - 警告メッセージを出力するが、アンインストール完了として処理を続行

### 7-2. 完了メッセージ

削除結果に応じて以下の情報を表示します：

**完全な削除が成功した場合**:
```
アンインストールが完了しました。

削除されたリソース:
  ✓ /etc/systemd/system/opencode-ollama-proxy.service
  ✓ /etc/systemd/system/opencode-ollama-proxy.service.d/
  ✓ /opt/opencode-ollama-proxy/
```

**一部のリソースが残っている場合**:
```
アンインストールが完了しました（一部リソースが残っています）。

削除されたリソース:
  ✓ /etc/systemd/system/opencode-ollama-proxy.service
  ✓ /etc/systemd/system/opencode-ollama-proxy.service.d/
  ✓ /opt/opencode-ollama-proxy/proxy.py

残っているリソース:
  ! /opt/opencode-ollama-proxy/ （予期しないファイルが含まれているため）
```

**バックアップを取得した場合**:
```
設定ファイルをバックアップしました:
  ~ ~/opencode-ollama-proxy-backup/override.conf
（※ sudo で実行した場合は実行元ユーザーのホームディレクトリ配下）
```

### 7-3. journalctl ログの削除を促すメッセージ

完了メッセージの後、以下を出力します：

```
※ サービスのログ履歴は journalctl に残っています。
  削除する場合は以下のコマンドを実行してください:
    journalctl --rotate && journalctl --vacuum-time=1s
```

---

## 8. エラーハンドリング方針

| エラー | 対応 | exit コード |
|--------|------|-------------|
| root権限なし | メッセージ出力後、即座に終了。変更は一切行わない。 | 1 |
| インストールされていない | メッセージ出力後、正常終了 | 0 |
| サービス停止失敗 | 警告メッセージを出力し、削除処理は続行 | — |
| ファイル削除失敗（権限など） | エラーメッセージを出力し、該当項目のステータスを「✗」として記録。他の処理は続行 | 1 |
| daemon-reload 失敗 | 警告メッセージを出力し、完了として終了 | 0 |

### exit コードの決定ルール

フェーズ7（ファイル削除）終了後、以下のロジックで exit コードを決定します：

- **全てのリソースが正常に削除された** → exit 0
- **1つでも削除失敗がある** → exit 1（完了メッセージに「✗」を含む）

---

## 9. 既知の制約・考慮事項

- **ログの保持**: アンインストール時に journalctl のログ履歴は自動削除しません。ユーザーに対して手動で削除するコマンドを提示します
- **drop-in ファイルのバックアップ**: `override.conf` が存在する場合、実行元ユーザーのホームディレクトリ（`<SUDO_USER のホーム>/opencode-ollama-proxy-backup/override.conf`）へのバックアップを取得するかどうかをユーザーに確認します。sudo で実行した場合は `$HOME`(`/root`) ではなく実行元の一般ユーザーのホームが使用されます。再インストール時にはこのバックアップから復元可能です
- **Ollama そのもの**: Ollama サービスやモデルはアンインストール対象外です。影響しないことを明記します
- **再インストール**: アンインストール後に再度 `install.sh` を実行すれば、新しくインストール可能です
