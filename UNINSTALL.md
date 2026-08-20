# uninstall.sh 設計方針

## 概要

`uninstall.sh` は Ollama Agent Proxy のsystemd設定と、`install-manifest.txt` に記載されたランタイムファイルを削除します。Ollama本体、モデル、journalctlのログは削除しません。

## 対象

- `/etc/systemd/system/ollama-agent-proxy.service`
- `/etc/systemd/system/ollama-agent-proxy.service.d/`
- `/opt/ollama-agent-proxy/install-manifest.txt` に記載されたファイル
- 管理対象削除後に空になったディレクトリ

## 安全性

- マニフェストの空行とコメントは無視する。
- 絶対パスまたは `..` を含むエントリは削除せず、警告する。
- マニフェストにないファイルは管理外として必ず保持する。
- 管理外ファイルが残る場合、`/opt/ollama-agent-proxy` 自体も残す。
- 旧単一ファイル版にマニフェストがない場合は、`proxy.py` と `proxy.py.bak` だけを削除する。

## 実行フロー

1. root権限とインストール状態を確認する。
2. サービスが稼働中なら停止確認を行う。
3. `override.conf` の内容を表示し、必要なら実行元ユーザーのホームへバックアップする。
4. 管理外ファイルを列挙し、保持されることを表示する。
5. 削除対象の最終確認を行う。
6. サービスをstop・disableし、systemd設定を削除する。
7. マニフェスト記載ファイルとマニフェスト自体を削除する。
8. 空ディレクトリを取り除き、`systemctl daemon-reload` を実行する。

## バックアップ

`override.conf` は確認後、次の場所へバックアップできます。

```text
<実行元ユーザーのホー>/ollama-agent-proxy-backup/override.conf
```

`sudo` 経由の場合は `SUDO_USER` から実行元ユーザーのホーディレクトリを解決します。
