# Neat SDK ネットワーク設定

Neat SDK、別名Neat開発環境は、Dockerコンテナ内で実行されます。`sima-cli sdk setup`は、コンテナを作成し、ホストのワークスペースのマウントを準備し、有効になっている場合はInsightを起動し、ホストのブラウザとDevKitが開発中に使用するコンテナのポートを公開します。

このガイドでは、Modalix DevKitとSDKを組み合わせて使用​​する際に最も重要なネットワーク関連の設定について説明します。

## ネットワークモデル

一般的な SDK のセットアップには、以下の 3 つの要素が含まれます。

- ホストコンピューター：Docker、`sima-cli`、SDKコンテナー、およびブラウザを実行します。
- SDKコンテナ：Neat 開発環境とオプションのInsight サービスを実行します。
- DevKit：ローカルネットワークまたはダイレクトなイーサネット/共有ネットワーク接続を介してホストに接続します。

サポートされている SDK コンテナネットワークは、`simasdkbridge` です。DevKit と Insight ネットワークが必要な場合は、Docker から直接 SDK コンテナを起動したり、VS Code Dev Containers 拡張機能を使用したりしないでください。代わりに、`sima-cli sdk setup` を使用して、コンテナ、ポートマッピング、ワークスペースの共有、および Insight の構成をまとめて作成してください。

## どのような設定で構成されていますか？

DevKit と連携してセットアップを実行すると、`sima-cli` は以下の設定を行います。

- Docker ネットワーク：`simasdkbridge` を作成または再利用します。
- SDKコンテナポート：Insight UI、ビデオ、RTSP、メタデータ、WebRTC、およびWeb SSHポートをホストに公開します。
- Insight ポートマップ：生成されたポートマッピングをSDKワークスペースの構成に書き込みます。
- ワークスペースの共有：`--devkit`を使用する場合、ホストからDevKitワークスペースへのアクセスを構成します。
- DevKit インターネットアクセス：DevKit をホストの共有ネットワークリンク経由でルーティングし、必要に応じて DevKit がパッケージリポジトリにアクセスし、依存関係をダウンロードできるようにします。
- Linux共有ネットワークルーティング：UbuntuのLinux共有ネットワークリンクで、必要に応じてスコープ付きの転送/NATルールを適用します。

使用方法：

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

## 永続的な共有ネットワークの修復（Linux）

一部の Ubuntu ホストでは、NetworkManager の共有ネットワーク機能が、ケーブルの再接続時やホストの再起動時にファイアウォールチェーンを再作成する場合があります。その場合、設定によって現在のセッションを修復できますが、その修復が永続的ではないことを警告します。

インタラクティブな設定では、`sima-cli` は、永続的な NetworkManager ディスパッチャープロファイルをインストールする前に確認を求めます。

自動化を行う場合は、明示的に設定してください。

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```

永続プロファイルは、検出された共有ネットワークのパスにのみ適用されます。これは、`-y` のみではインストールされません。

## DevKit インターネットへのアクセス

DevKit を推奨される Linux / macOS の共有ネットワークリンクを通じて接続した場合、インターネットアクセスにはホストコンピュータに依存します。これは、DevKit がセットアップおよび開発中にパッケージをダウンロードしたり、依存関係をインストールしたり、外部サービスにアクセスしたりする必要がある場合に重要です。

依存関係のダウンロード中に DevKit コマンドが失敗した場合は、ホストが正常にインターネットにアクセスできること、および共有ネットワークパスがまだ有効であることを確認してください。Linux では、ネットワーク診断ツールを使用して、共有ネットワークの転送に関する問題を特定できます。

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

## Insightと公開されているポート

Insight は、生成されたホストポートを使用します。利用可能な場合はデフォルト値が使用されますが、ポートがすでに使用されている場合、`sima-cli` はデフォルト以外のポートを割り当てることがあります。

SDKシェル内でアクティブなマッピングを確認するには、次の手順を実行します。

```bash
neat --json
```

`exposedPorts`と`insight.webUiUrl`のエントリを探してください。DevKitストリーム、RTSPソース、ブラウザアクセス、またはアプリケーション出力先を設定する際に、これらの値を使用してください。

## 関連ページ

- [SDKのネットワーク接続に関するトラブルシューティング](troubleshooting.md)
- [SDKのネットワーク変更をロールバックします](rollback.md)
- [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk doctor network`](../commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../commands/sima-cli-sdk-network-rollback.md)
