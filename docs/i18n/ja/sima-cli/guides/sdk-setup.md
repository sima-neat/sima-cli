# SDK のセットアップと拡張機能の管理

`sima-cli sdk setup` の実行時に、任意の SDK サービス、ブラウザー版 VS Code の拡張機能、Model Compiler のインストール元を選択します。

コマンドリファレンス: [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)

## Edgematic Studio の明示的な選択

既定のセットアップは、Edgematic Studio の確認を表示せず、Studio のインストールやポートの公開も行いません。`-y` と `--noninteractive` でも同様です。

Studio をインストールしてポートを公開するには `--edgematic-studio` を使います。後で手動インストールするためにポートだけを公開するには `--edgematic-studio-port` を使います。

## ブラウザー版 VS Code の拡張機能の選択

対話型セットアップでは Neat、Codex、Claude の拡張機能を選べます。Space で選択し、Enter で確定します。最初はすべて未選択です。何も選ばなければインストールをスキップし、選択しなかった既存の拡張機能はインストール済みのまま残ります。

自動化する場合は、チェックリストを表示せず 3 つすべてをインストールします:

```bash
sima-cli sdk setup --noninteractive --all-extensions
```

`--all-extensions` は拡張機能の選択だけを省略します。他の質問を省略するには `--noninteractive` を使います。既存の SDK コンテナーを再利用する場合も拡張機能をインストールします。このオプションがなければ、従来の `SIMA_CLI_INSTALL_CODEX_EXTENSION` 設定で要求されない限り、`-y` と `--noninteractive` は任意の拡張機能をスキップします。`--all-extensions` は `--minimal` と併用できず、Edgematic Studio を有効にしたり Model Compiler の選択を変えたりしません。

## ブラウザー版 VS Code の拡張機能のバージョン

sima-cli は Codex と Claude の厳密なバージョンをインストールし、自動更新されないよう固定します。セットアップを再実行すると異なるバージョンを置き換え、一致するバージョンは再ダウンロードせず保持して固定します。SDK イメージは `/etc/sima-neat/vscode-extensions.json` で検証済みバージョンを宣言できます:

```json
{
  "schema_version": 1,
  "extensions": {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266"
  }
}
```

マニフェスト自体はインストールへの同意を意味しません。スキーマバージョン 1 では両方の ID に厳密なバージョンが必要です。マニフェストのない古いイメージは互換用の代替として Codex `26.5825.51511` と Claude `2.1.266` を使います。読み取れない、または無効なマニフェストはエラーを報告して拡張機能のインストールをスキップしますが、残りのセットアップは続行します。Codex または Claude のインストールコマンドが失敗した場合、同じ厳密なバージョンと通常の TLS 検証で最大 3 回再試行します。

環境変数による上書きが優先されます:

| 変数 | 動作 |
| --- | --- |
| `SIMA_CLI_CODEX_EXTENSION_ID` | `publisher.extension@exact-version` で Codex を上書きします。空なら `--all-extensions` を使わない限り無効にします。 |
| `SIMA_CLI_CLAUDE_EXTENSION_ID` | `publisher.extension@exact-version` で Claude を上書きします。空なら `--all-extensions` を使わない限り無効にします。 |
| `SIMA_CLI_INSTALL_CODEX_EXTENSION` | チェックリストなしで拡張機能のインストールを自動選択します。 |

バージョンなしの組み込み拡張機能 ID は SDK/既定の固定バージョンを使います。他の ID には明示的なバージョンが必要です。`--minimal` は任意の拡張機能のインストールをスキップします。セットアップはインストール済みバージョンを検証し、選択した各拡張機能の `metadata.pinned` フラグを書き込み、ブラウザー版 VS Code を再起動します。その後、開いているブラウザータブを再読み込みしてください。

## Model Compiler のインストール元

セットアップは SDK コンテナーのアーキテクチャに応じて、現在の作業ディレクトリだけで `model-compiler-arm64.zip` または `model-compiler-amd64.zip` を探します。展開済みフォルダー、親ディレクトリ、ワークスペースの ZIP、別のアーキテクチャは検索しません。

有効なローカル ZIP があれば、ローカル、オンライン、スキップを選べます。なければオンラインまたはスキップを選べ、Enter の既定値はスキップです。

| フラグ | ローカル ZIP あり | 有効なローカル ZIP なし |
| --- | --- | --- |
| `--noninteractive` | ローカルからインストール | スキップ |
| `--noninteractive -y` | ローカルからインストール | オンラインでインストール |
| `-y` | ローカルからインストール | オンラインでインストール |

無人でのオンラインインストールには `-y` が必要です。従来の SDK ソースでは、ホストで `sima-cli login` を使って認証します。`--minimal`、`--no-model-compiler`、`--no-model-sdk` は常にインストールをスキップします。

公式 ZIP はルートに `install_modelsdk_wheels.sh`、`source.json`、`manifest.txt`、パッケージのペイロードを含み、SDK 用に選択されたコンパイラーバージョンと一致する必要があります。アーカイブをホストの一時ストレージに展開し、Linux コンテナーの一時ストレージへコピーするため、両方のコピーに必要な空き容量を確保してください。成功または失敗後に一時データを削除し、元の ZIP は保持します。ローカルインストールの失敗時にオンラインへフォールバックしません。システムパッケージや Python の前提条件にはネットワーク接続が必要な場合があります。
