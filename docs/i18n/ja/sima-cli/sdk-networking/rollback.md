# SDKネットワークの変更をロールバックする

SDK のセットアップまたはネットワークの修復によって行われた Linux ホストのネットワーク変更を検査または元に戻したい場合は、ロールバックを使用してください。

ロールバックは、可能な限り実行されます。検出された DevKit/shared-network パスに対して、`sima-cli` が識別できる範囲指定されたルールを削除します。ただし、関連性のないホストのネットワーク、VPN の構成、Docker のインストール状態、またはユーザーが管理するファイアウォールルールはリセットされません。

## ロールバック操作のプレビュー

`--apply` が指定されない限り、ロールバックはドライランモードで実行されます。

```bash
sima-cli sdk network rollback --devkit <devkit-ip>
```

変更を適用する前に、表の内容を確認してください。

## ロールバックを適用する

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

これにより、SDKブリッジとDevKit共有ネットワークパスのために`sima-cli`が追加した、対応するランタイム転送/NATルールが削除されます。

## 永続プロファイルを削除します。

永続的な NetworkManager ディスパッチャープロファイルがインストールされている場合、削除する前にロールバックを促すメッセージを表示します。NetworkManager が共有接続を再作成すると、そのプロファイルが再度適用され、SDK ブリッジの転送が有効になります。

対話的に削除するには、次の手順を実行します。

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

非対話的に削除するには：

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply --remove-persistent-profile
```

SDKネットワーク修復を完全にやり直したい場合は、永続プロファイルを削除しても問題ありません。同じUbuntu共有ネットワークDevKit接続を使い続けると、後で再度修復が必要になる場合があります。

## ロールバック後

現在の状態を確認するために、医師に検査を依頼してください。

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

SDKのネットワーク構成を最初から再作成する場合は、次のコマンドを実行してください。

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

継続的な共有ネットワークの修復が必要な自動化には、以下を使用してください。

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```
