# Refactoring Plan

## 1. 目的

現在の `proxy.py` は、HTTP サーバー、設定、OpenAI 互換 API の形式変換、SSE の生成、Ollama との通信、ログ出力を単一ファイルで担当している。

本リファクタリングでは、外部から見た API の挙動を維持したまま、責務を次の単位に分離する。

```text
ollama-agent-proxy/
├── proxy.py
├── agents/
│   ├── __init__.py
│   ├── opencode.py
│   ├── codex.py
│   └── claudecode.py
├── ollama.py
├── common.py
├── tests/
│   ├── fixtures/
│   ├── test_common.py
│   ├── test_ollama.py
│   ├── test_opencode.py
│   ├── test_codex.py
│   ├── test_http.py
│   ├── test_settings.py
│   └── test_distribution.py
├── README.md
├── INSTALL.md
├── UNINSTALL.md
├── install-manifest.txt
├── install.sh
├── uninstall.sh
└── LICENSE
```

初回のリファクタリングでは `claudecode.py` は空の雛形のみを作成し、Claude Code 向けの処理は実装しない。

## 2. 基本方針

- リファクタリング前後で、既存エンドポイント、リクエスト・レスポンス形式、SSE イベント、HTTP ステータス、環境変数の意味を変えない。
- 最初に現行動作をテストで固定し、その後に小さな単位でコードを移動する。
- モジュール間の依存方向を一方向にし、循環 import を作らない。
- `common.py` にはネットワーク、HTTP ハンドラー、環境変数などに依存しない純粋な共通処理だけを置く。
- エージェント固有モジュールは形式変換に集中させ、Ollama への実通信を行わない。
- `ollama.py` はクライアント固有の OpenAI 互換レスポンスを組み立てない。
- `proxy.py` は薄い入口とし、ルーティングと HTTP 入出力の調整に集中させる。
- 初回は機能追加や仕様改善を混ぜず、動作を保存する構造変更として実施する。

想定する依存方向は次のとおり。

```text
proxy.py
  ├── agents（公開 API のみ）
  ├── ollama
  └── common

agents.__init__ ──> agents.opencode / agents.codex
agents.* ──> common
ollama.py ──> common（必要な場合のみ）
common.py ──> Python 標準ライブラリのみ
```

`proxy.py` は `agents.opencode` や `agents.codex` を直接 import せず、`agents/__init__.py` が公開する API だけを使用する。`agents.*` と `ollama.py` は相互に import しない。両者の連携は `proxy.py` が仲介する。

## 3. 各ファイルの責務

### `proxy.py`

アプリケーションのエントリーポイントおよび HTTP 境界を担当する。

- 環境変数からの設定読み込み
- `ThreadingHTTPServer` の生成、起動、停止
- URL ルーティング
- リクエストボディのサイズ確認、JSON 読み込み
- JSON / SSE の HTTP ヘッダーとレスポンス書き込み
- エージェント固有変換と Ollama クライアントのオーケストレーション
- HTTP 境界での例外処理、接続切断処理、アクセスログ
- `/health` と `/v1/health` の処理

形式変換の詳細や `urllib` による Ollama 通信は置かない。

### `agents/__init__.py`

エージェント処理パッケージの公開境界とする。

- 外部へ公開する変換関数またはアダプターの明示
- `/v1/models` 用の Ollama モデル一覧から OpenAI 互換形式への純粋な変換関数の公開
- 必要になった場合のエンドポイント／クライアント別ディスパッチ
- パッケージ外から内部実装へ直接依存させないための窓口

初回は過度な抽象クラス化を避け、共通インターフェースが実際に必要になった時点で導入する。

### `agents/opencode.py`

OpenCode が利用する OpenAI Chat Completions 互換処理を担当する。

- `/v1/chat/completions` リクエストの検証と Ollama 用データへの変換
- Chat Completions の `tools`、`tool_choice`、`max_tokens` の変換
- Ollama の非ストリーミング応答から `chat.completion` への変換
- Ollama の解析済みストリームから `chat.completion.chunk` ペイロードを持つ内部イベントへの変換
- Chat Completions 固有の tool call の組み立て

HTTP ソケットへの直接書き込みは避け、非ストリーミングでは JSON 化可能な辞書、ストリーミングでは後述の内部イベント列を返す。

### `agents/codex.py`

Codex CLI が利用する OpenAI Responses API 互換処理を担当する。

- `/v1/responses` の `instructions` と `input` のメッセージ変換
- Responses API のフラットな function tool 定義から Ollama 形式への変換
- `function_call` と `function_call_output` の履歴変換
- `max_output_tokens` と `tool_choice` の変換
- Ollama の非ストリーミング応答から Responses オブジェクトへの変換
- Ollama の解析済みストリームから `response.*` ペイロードを持つ内部イベント列への変換
- Responses API 固有の ID、sequence number、usage、終了・失敗イベントの組み立て

### `agents/claudecode.py`

初回は将来の Claude Code 対応位置を示す空の雛形だけを作成する。

- import しても副作用がないこと
- 未対応の Claude Code リクエストをこのモジュールへルーティングしないこと
- 実装済みと誤認させるダミー変換や暗黙のフォールバックを置かないこと
- 最低限のモジュール docstring と TODO のみとし、公開 API は要件確定後に定めること

### `ollama.py`

Ollama API との共通通信を担当する。

- `/api/chat` への POST
- `/api/tags` への GET とモデル一覧取得
- URL、HTTP ヘッダー、接続・読み取りタイムアウトの取り扱い
- JSON 応答と NDJSON ストリームを上位層へ渡す処理
- `HTTPError`、`URLError`、タイムアウトなどを一貫した内部例外へ変換
- upstream response の close を保証

Ollama リクエストボディのうちクライアント API 固有部分は `agents/` で作成し、`ollama.py` は送受信に集中する。`think`、`keep_alive` など全クライアント共通の値は `proxy.py` が読み込んだ設定から `ollama.py` へ明示的に渡し、`ollama.py` が POST 直前に一箇所で付与する。

ストリーミング通信はコンテキストマネージャーとして公開し、`with` ブロック内で NDJSON を解析した辞書の iterator を供給する。正常終了、upstream 例外、クライアント切断、generator の途中破棄のいずれでも、`__exit__` または `finally` で upstream response を close する。

### `common.py`

副作用を持たない、プロトコル間で再利用可能な小さな処理を担当する。

現行コードからの候補は次のとおり。

- JSON のコンパクトなシリアライズ
- OpenAI 互換エラーオブジェクトの生成
- content parts のテキスト正規化
- tool arguments の JSON 文字列化
- OpenAI メッセージから Ollama メッセージへの共通変換

時刻取得、UUID 生成、環境変数参照、ログ出力、ネットワーク通信、HTTP レスポンスへの書き込みは純粋処理ではないため置かない。ID や時刻を含むオブジェクト生成では、それらの値を呼び出し側から渡せる設計にする。

## 4. 現行コードの移動対応

| 現行の処理 | 移動先 | 補足 |
|---|---|---|
| 設定定数、`main()` | `proxy.py` | 将来 `Settings` へまとめる余地はあるが初回は必須としない |
| `ProxyHandler` のルーティング、ヘッダー、body 読み込み | `proxy.py` | HTTP 境界として維持 |
| `make_id()`、`now_unix()` | 呼び出し側または注入可能な生成処理 | `common.py` の純粋性を維持 |
| `json_bytes()`、`openai_error()` | `common.py` | HTTP 非依存のデータ処理として分離 |
| `normalize_content()`、`normalize_tool_arguments()` | `common.py` | 両エージェントで共用 |
| `convert_message_to_ollama()` | `common.py` | API 固有差分が増えた場合は各 agent から薄くラップする |
| `responses_tools_to_ollama()` | `agents/codex.py` | Responses API 固有 |
| `handle_chat_completion()` | `proxy.py` + `agents/opencode.py` | HTTP 処理と変換処理に分割 |
| `handle_non_stream()`、`handle_stream()` | `agents/opencode.py` | socket 書き込みは `proxy.py` に残す |
| `handle_responses()` | `proxy.py` + `agents/codex.py` | HTTP 処理と変換処理に分割 |
| `_responses_input_to_messages()`、`_responses_content_to_text()` | `agents/codex.py` | 単体テスト可能な純粋関数へ変更 |
| `handle_responses_non_stream()`、Responses の stream 処理 | `agents/codex.py` | SSE イベントを生成する処理として分離 |
| `urlopen()`、`Request`、Ollama エラー処理 | `ollama.py` | Chat / Responses 間の重複を統合 |
| `/v1/models` の Ollama 通信 | `ollama.py` | `/api/tags` の取得と Ollama JSON の解析を担当 |
| `/v1/models` の OpenAI 互換形式への整形 | `agents/__init__.py` から公開する純粋関数 | `created: 0` を含む現行形式を保持し、`proxy.py` は HTTP 応答だけを担当 |

## 5. 実施手順

### Phase 0: 現行仕様の固定

1. 現在公開されているエンドポイントと環境変数を一覧化する。
2. Chat Completions と Responses API の代表的な入出力を fixture として保存し、JSON の意味だけでなく HTTP ヘッダーと SSE の生バイト列も互換性基準とする。
3. 非ストリーミング、ストリーミング、tool calling、異常系の回帰テストを追加する。
4. Ollama 通信を差し替えられるよう、テストではローカルの偽 upstream または mock を使用する。
5. 環境変数の未設定時、各既定値、真偽値の表記、数値の不正値に対する起動時の振る舞いを固定する。
6. 現行テストが成功することを基準点として記録する。

### Phase 1: `common.py` の抽出

1. 副作用のない helper を `common.py` へ移す。
2. 呼び出し元の import のみを切り替える。
3. helper の単体テストと既存の回帰テストを実行する。
4. 互換性を保つため、シリアライズ結果の空白、Unicode、引数不正時のフォールバックまで比較する。

### Phase 2: `ollama.py` の抽出

1. Ollama の URL 構築、`/api/chat`、`/api/tags` 通信をまとめる。
2. 接続エラー、upstream HTTP エラー、無効な JSON、timeout を表す内部例外を定義する。
3. 非ストリーミング応答とストリーミング応答のライフサイクルを整理し、コンテキストマネージャーによる close 保証を実装する。
4. Chat / Responses の重複した接続処理を新しいクライアントへ置き換える。
5. upstream の切断とクライアント切断の回帰テストを行う。

### Phase 3: `agents/opencode.py` の抽出

1. `agents/` と最小の `agents/__init__.py` を作成し、Chat Completions のリクエスト変換を純粋関数として移す。
2. 非ストリーミングのレスポンス変換を移す。
3. ストリーム処理を「Ollama の解析済み辞書から内部イベントへの変換」と「SSE の送信」に分け、前者を移す。
4. tool call の ID、index、arguments、finish reason が現行と一致することを確認する。
5. `/v1/chat/completions` の回帰テストをすべて実行する。

### Phase 4: `agents/codex.py` の抽出

1. Responses の入力と tool 定義の変換を移す。
2. 非ストリーミングの Responses オブジェクト生成を移す。
3. ストリームの状態管理と `response.*` イベント生成を移す。
4. sequence number、response ID、call ID、usage、terminal event、`[DONE]` の順序を確認する。
5. 現行コードに複数の Responses stream 実装があるため、実際に呼ばれている `handle_responses_stream_v2()` を互換性の基準とする。旧実装の削除は同等性確認後に行う。
6. `/v1/responses` の回帰テストをすべて実行する。

### Phase 5: パッケージ境界と雛形の作成

1. `agents/__init__.py` の公開 API を確定し、`proxy.py` が内部モジュールを直接 import していないことを確認する。
2. `agents/claudecode.py` を空の雛形として追加する。
3. import graph を確認し、循環依存と import 時の副作用がないことを確認する。
4. `proxy.py` に変換ロジックや直接の `urlopen()` が残っていないことを確認する。

### Phase 6: 配布・ドキュメントの更新

1. `install.sh` を複数ファイルと `agents/` ディレクトリの配置に対応させる。
2. GitHub Raw から複数ファイルを個別取得せず、タグまたはコミット SHA で固定した単一のリポジトリアーカイブを一括取得する。リリースごとに期待 SHA-256 を提供し、配置前の一致検証を必須とする。
3. 同一ファイルシステム上の一時ディレクトリで、必要ファイルの存在、管理対象マニフェスト、Python の構文と import を検証してから、ディレクトリの rename で切り替える。
4. 更新前のディレクトリ一式をバックアップとして保持し、配置、systemd 再起動、起動確認のいずれかが失敗した場合は旧ディレクトリへ戻す。
5. `uninstall.sh` は管理対象マニフェストに記載されたファイルだけを削除する。`agents/` やインストールディレクトリに管理外ファイルが残る場合は、ディレクトリを再帰削除しない。
6. `README.md`、`INSTALL.md`、`UNINSTALL.md` の単一ファイル前提を更新する。
7. 直接実行と systemd の双方で起動確認する。

対象構成には記載されていないが、現行リポジトリには `uninstall.sh`、`INSTALL.md`、`UNINSTALL.md` が存在する。初回リファクタリングでこれらを削除せず、複数ファイル構成との整合性を保つための更新対象として扱う。最終的な構成から除外する場合は、別途明示的に決定する。

### Phase 7: 最終整理

1. 未使用 import、到達不能になった旧メソッド、重複ロジックを削除する。
2. ファイルごとの公開 API と型・docstring を整える。
3. 全テスト、構文チェック、起動確認、実 Ollama による smoke test を実行する。
4. 差分を「構造変更」と「挙動変更」に分類し、意図しない挙動変更がないことをレビューする。

## 6. 主な課題と対策

### ストリーミング状態の分離

現在のストリーミング処理は、Ollama の NDJSON 読み取り、tool call の増分組み立て、SSE イベント生成、socket 書き込み、切断処理を同じメソッド内で行う。単純なコード移動では結合が残る。

対策として、次の三段階に分ける。

1. `ollama.py` がコンテキストマネージャーの内側で upstream NDJSON を解析し、JSON 化可能な辞書の iterator を供給する。
2. `agents/opencode.py` または `agents/codex.py` が、SSE のペイロードにする辞書か terminal marker のいずれかを表す transport-neutral な内部イベントを生成する。エージェント層は `data:`、改行、UTF-8 エンコードを扱わない。
3. `proxy.py` が内部イベントを現行と同一の SSE 生バイト列へシリアライズし、flush する。`[DONE]` は agent が生成する terminal marker を受けて `proxy.py` が SSE として書き込む。

upstream response の所有権は `ollama.py` のコンテキストマネージャーにある。`proxy.py` はクライアント切断時にイベント消費を停止して `with` ブロックを抜け、agent の generator も `close()` する。これにより、正常終了と中途終了の両方で upstream を閉じる。

### エラー形式と送信開始後の失敗

SSE ヘッダー送信後は通常の JSON エラーへ切り替えられない。Chat Completions と Responses API ではストリーム失敗時のイベント形式も異なる。

対策として、Ollama 層は内部例外を返し、ヘッダー送信前後の状態とクライアント向けエラー表現は `proxy.py` と各 agent が判断する。HTTP ステータスとイベント順序を回帰テストで固定する。

### 設定の所有場所

環境変数を複数モジュールが import 時に直接読むと、テスト時の差し替えが難しくなり、設定値が分散する。

対策として、環境変数は入口で一度読み、Ollama 通信に必要な値を明示的に渡す。初回に設定クラスを導入しない場合でも、各ファイルで重複して環境変数を読まない。

### ID・時刻とテスト再現性

Responses API と Chat Completions はランダム ID と現在時刻を含む。変換関数内部で直接生成すると、完全一致テストが難しい。

対策として、ID と時刻を引数または生成関数として注入できるようにし、テストでは固定値を使う。

### スレッド安全性

`ThreadingHTTPServer` で同時処理されるため、モジュールレベルの可変状態や共有中間バッファを追加しない。現行の `ProxyHandler.request_counter` は加算が厳密な同期処理ではないため、ログ用途に限定するか lock／別の採番方法を検討する。

### インストール方式

現行 `install.sh` は GitHub Raw から `proxy.py` のみを取得し、`uninstall.sh` も主に単一ファイルを前提としている。このままでは分割後に import error で起動できない。

対策として、タグまたはコミット SHA で固定した単一アーカイブを一括取得し、リリースごとに提供する期待 SHA-256 と一致することを必須とする。同一ファイルシステム上の一時ディレクトリで必要ファイル、Python の import／構文を検証後、rename で一式を切り替える。個別ファイルを順番に直接上書きする方式は避ける。

配置前のバージョン一式は起動確認完了まで保持する。新バージョンの配置または起動に失敗したら旧一式へ戻す。インストール時に管理対象マニフェストを配置し、アンインストールではその記載ファイルだけを削除する。管理外ファイルを含むディレクトリは再帰削除しない。

### モジュール名 `ollama.py`

将来、同名の外部 Python パッケージを利用すると import が衝突する可能性がある。今回の指定構成では `ollama.py` を採用するが、import はプロジェクト内モジュールであることが明確になる形にし、外部の `ollama` パッケージへ依存する場合は名称変更を再検討する。

### エージェント名と API の対応

初回は現行仕様に基づき、Chat Completions を `opencode.py`、Responses API を `codex.py` へ配置する。ただし将来 OpenCode が Responses API を使うなど、クライアント名と API 形式が一対一でなくなる可能性がある。

その場合は、エージェント別モジュールの下でプロトコル変換を共用するか、将来 `protocols/` のような層を追加する。初回から未確定の抽象化は導入しない。

## 7. テスト計画

最低限、次を自動テストの対象とする。

テストは `tests/` に配置し、大きな入出力、NDJSON、SSE の生バイト列は `tests/fixtures/` で管理する。少なくとも `test_common.py`、`test_ollama.py`、`test_opencode.py`、`test_codex.py`、`test_http.py`、`test_settings.py`、`test_distribution.py` を作成する。

### 共通処理

- 文字列、content parts、`None`、不明な型の content 正規化
- tool arguments が文字列、辞書、`None`、JSON 化不能な値の場合
- Unicode を含む JSON とエラーオブジェクト
- tool call ID、name、index の保持

### Chat Completions / OpenCode

- 非ストリーミングの通常応答
- 空 content と tool call の応答
- 複数 tool calls と arguments の変換
- ストリーミングの role、content delta、tool call delta、終了 chunk、`[DONE]`
- `stream`、`tools`、`tool_choice`、`max_tokens` の各組み合わせ

### Responses API / Codex

- 文字列 input と message input
- `instructions`、system、developer message の扱い
- `function_call` と `function_call_output` の往復
- 非ストリーミングの output、status、usage
- ストリーミングの created、in_progress、output item、content delta、completed／failed、`[DONE]`
- 複数 tool calls と断片化された arguments
- `max_output_tokens` と Responses 形式の tools／tool choice

### HTTP・Ollama 通信

- `/v1/models`、`/health`、`/v1/health`、404
- 不正 JSON、空または不正な model、リクエストサイズ超過
- `Content-Length` の欠落、非数値、負値、上限の直前・一致・直後
- HTTP ステータス、`Content-Type` など現行ヘッダー、JSON と SSE の UTF-8 生バイト列
- SSE の `data:`、改行、空行、イベント順、`[DONE]`、チャンクごとの flush
- Ollama の接続拒否、HTTP エラー、不正 JSON、途中切断、timeout
- クライアントの Broken pipe／Connection reset
- クライアント切断と generator 途中終了時の upstream close
- 複数リクエストの同時実行

### 設定・起動

- すべての環境変数の未設定時と既定値
- `OLLAMA_THINK`、`DEBUG` の受理する真偽値表記
- port、timeout、request size に非数値、負値、0を指定したときの現行挙動
- import 時の副作用がないことと、直接起動時の設定反映

### 配布

- 新規インストール
- 旧単一ファイル版からの更新
- タグまたはコミット SHA に固定したアーカイブの一括取得とハッシュ不一致
- マニフェストの欠落、必要ファイルの欠落、import 失敗
- ダウンロード途中の失敗とロールバック
- systemd 起動と再起動
- アンインストール時に管理外ファイルを削除しないこと

## 8. 完了条件

- 指定された責務に従うファイル構成になっている。
- `claudecode.py` は空の雛形であり、未実装機能を公開していない。
- `proxy.py` は入口・HTTP 境界・オーケストレーションに限定されている。
- `agents/` はエージェント固有の形式変換を担当し、ネットワーク通信を行わない。
- `ollama.py` に Ollama との共通通信が集約されている。
- `common.py` は副作用のない共通処理だけを含む。
- モジュール間に循環 import がない。
- `proxy.py` は `agents/__init__.py` の公開 API だけを import し、内部の agent モジュールへ直接依存していない。
- `/v1/models` の Ollama 通信と OpenAI 互換形式への整形が、定めた責務境界に従って分離されている。
- 現行の公開 API、SSE イベント順、エラー形式、環境変数の互換性が保たれている。
- ストリーミングの正常終了、upstream 例外、クライアント切断のすべてで upstream response が close される。
- 回帰テスト、構文チェック、直接起動、systemd 起動、実 Ollama smoke test が成功する。
- バージョン固定された一括配布、マニフェストによる管理、更新失敗時の一式復元、安全なアンインストールが複数ファイル構成に対応している。
- README とインストール関連ドキュメントが実装と一致している。

## 9. 初回リファクタリングの対象外

- Claude Code 対応の実装
- 新しい API エンドポイントや機能の追加
- OpenAI／Ollama API の仕様改善を目的とする挙動変更
- HTTP サーバーフレームワークや外部 HTTP クライアントライブラリへの移行
- 設定方式、ログ形式、デフォルト値の変更
- ファイル分割に必須でない大規模な抽象化やクラス階層の導入

対象外の変更は、構造分割と互換性確認が完了した後に別の変更として実施する。
