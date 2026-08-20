# SDK ネットワークの問題解決

SDKコンテナ、Insight UI、DevKit SSH、RTSP、WebRTCビデオ、またはワークスペースの同期が期待どおりに動作しない場合は、ネットワーク診断ツールを使用してください。

診断コマンドは読み取り専用です。ホストのルーティング、Docker コンテナの状態、公開されているポート、Insight ポートマップ、ファイアウォールの状態、および選択されたLinux ネットワークの詳細を調べます。

## 簡単に確認する

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

複数の SDK コンテナーが存在する場合、コンテナー名を指定してください。

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --container <container-name>
```

## サポートに必要な情報をまとめてください。

SiMaサポートのサポートが必要な場合は、以下の情報をまとめてください。

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect
```

出力先を選択するには：

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect --output ~/sima-sdk-network-doctor.tar.gz
```

このパッケージには、安全に処理されたホスト、Docker、ルート、ファイアウォール、NetworkManager、およびInsightポートマップ診断が含まれます。これは、ネットワークサポートを目的としています。アーカイブにSSHキー、Dockerの認証情報ファイル、ブラウザのCookie、またはその他の機密情報を手動で追加しないでください。

## 一般的な所見

| 発見 | 意味 | 推奨される対応 |
| --- | --- | --- |
| `vpn-route` | DevKit への経路は、VPN またはトンネルインターフェースを経由します。 | VPNを切断するか、ルーティングを調整して、DevKitが物理的なDevKitに接続されたインターフェースを使用するようにします。 |
| `missing-simasdkbridge` | SDKコンテナは実行中ですが、`simasdkbridge` に接続されていません。 | `sima-cli sdk setup` を使用して、SDK を再作成または再起動してください。DevKit ワークフローでは、VS Code Dev Containers を直接起動しないようにしてください。 |
| `host-network-mode` | SDKコンテナは、Docker ホストネットワークを使用して起動されました。 | SDKを`sima-cli sdk setup`で再作成してください。ホストネットワークは、サポートされているInsightポートモデルではありません。 |
| `port-map-mismatch` | Docker で公開されているポートと、生成された Insight ポートマップが一致しません。 | SDKコンテナを再作成し、`sima-cli`によってポートを再生成し、Insightの構成をまとめて更新できるようにします。 |
| `stale-port-bindings` | 停止された SDK コンテナーには、現在利用できなくなった Docker ポートバインディングが保存されています。 | `sima-cli sdk setup` を使用して、コンテナーを削除または再作成します。 |
| `nm-shared-iptables-blocking` | NetworkManagerの共有ネットワーク機能は、Dockerのルールが適用される前に、SDKブリッジからのトラフィックを拒否します。 | `sima-cli sdk network repair --devkit <devkit-ip>` を実行してください。修正を維持する必要がある場合は、`--persist` を追加してください（再接続や再起動後も有効にする場合）。 |
| `container-devkit-reachability` | SDKコンテナは、DevKitへのSSH接続またはpingによる接続確認に失敗しました。 | DevKitのIPアドレス、ケーブル/ネットワークの経路、DevKitのSSHサービス、およびホストのファイアウォールを確認してください。 |

## DevKit の依存関係のダウンロードに失敗しました。

DevKit が推奨される共有ネットワークリンクを使用する場合、インターネットへのアクセスはホストに依存します。DevKit でパッケージのインストール、依存関係のダウンロード、または外部サービスへのアクセスが失敗した場合は、以下の点を確認してください。

- ホストコンピューターはインターネットに接続できます。
- ホストの共有ネットワークインターフェースは、現在もアクティブな状態です。
- VPNルーティングで、DevKitのルートが正しく認識されていません。
- Linux NetworkManager が共有接続を再作成した場合、転送/NAT ルールは依然として有効です。

実行：

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

医師が NetworkManager の共有ネットワークの接続に関する問題を発見した場合、医師が出力した内容に示されている修復コマンドを実行してください。

## Linux共有ネットワークのルーティングを修復します。

Ubuntu/Linux ホストで NetworkManager を使用した共有ネットワークを利用している場合は、次のコマンドを実行してください。

```bash
sima-cli sdk network repair --devkit <devkit-ip>
```

実行時の修正を適用した後、永続的なディスパッチャーフックをインストールするには、次の手順を実行します。

```bash
sima-cli sdk network repair --devkit <devkit-ip> --persist
```

今回の修正は、SDKブリッジと検出された箇所に限定されます。 DevKit-共有ネットワークパスに面している。切り替えは行われません。 Docker ネットワークをホストし、グローバル設定は行いません。 `FORWARD` ポリシーは `ACCEPT`.

## 修理後に確認してください。

もう一度、医師の診断を実行してください。

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

次に、以下のURLを開いてください：Insight

```bash
neat --json
```

ブラウザでInsightを開けない場合は、ホストブラウザから`mainUI`のホストポートにアクセスできること、およびSDKコンテナが`sima-cli sdk setup`によって起動されたことを確認してください。
