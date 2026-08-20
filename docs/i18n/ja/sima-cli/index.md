# sima-cli コマンドリファレンス

sima-cli コマンドラインインターフェースの Markdown 形式の参照ドキュメントを生成しました。

## インストール

ほとんどのユーザーは、お使いのオペレーティングシステムに対応する公開インストーラーのURLから、最新の公式リリース版をインストールしてください。

### Linux、macOS、およびDevKit

ターミナルからインストーラーを実行してください。

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

インストール後、新しいターミナルを開くか、シェルのプロファイルを再読み込みし、インストールが正しく行われたことを確認してください。

```bash
sima-cli --version
```

### Windows PowerShell

PowerShellから、Windowsのインストーラーをダウンロードして実行してください。

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

インストール後、新しいコマンドプロンプトまたはPowerShellウィンドウを開き、インストールが正常に完了したことを確認してください。

```powershell
sima-cli --version
```

### 詳細設定：ブランチまたはリリースを選択してください。

最新の公式 PyPI リリースをインストールする代わりに、特定のテスト済みのブランチビルドまたはリリースを選択する必要がある場合にのみ、`install.py` を使用してください。

Linux、macOS、または DevKit の場合：

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/install.py -o sima-cli-install.py
python3 sima-cli-install.py
```

特定のブランチまたはリリースをインストールします。

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

Windows の PowerShell で：

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py
```

特定のブランチまたはリリースをインストールするには：

```powershell
python .\sima-cli-install.py feature/my-branch latest
python .\sima-cli-install.py v2.1.6 latest
```

`v2.1.6`などのリリースタグは、公開されているPyPIからインストールされます。ブランチ名は、`artifacts.neat.sima.ai/sima-cli`からテスト済みのアーティファクトをインストールします。

公開されているPyPIのリリースも、直接インストールできます。

```bash
pip install sima-cli
```

## ガイド

| ガイド | 説明 |
| --- | --- |
| [Neat SDK ネットワーク設定](sdk-networking/index.md) | SDK、Docker、Insight、およびDevKitのネットワーク設定について理解しましょう。 |
| [SDK ネットワークの問題解決](sdk-networking/troubleshooting.md) | ネットワーク診断を実行し、Linux共有ネットワークのルーティングを修復し、サポートに必要な情報を収集します。 |
| [SDKのネットワーク変更をロールバック](sdk-networking/rollback.md) | プレビューを行い、Linux SDK のネットワーク設定または変更を元に戻します。 |

## 最上位レベルのコマンド

| コマンド | 説明 |
| --- | --- |
| [`sima-cli appzoo`](commands/sima-cli-appzoo.md) | App Zooからサンプルアプリにアクセスしてください。 |
| [`sima-cli bootimg`](commands/sima-cli-bootimg.md) | SiMa DevKit の起動可能なイメージを準備してください。 |
| [`sima-cli device`](commands/sima-cli-device.md) | ローカルネットワーク上で、近くにあるSiMa.aiデバイスを検出します。 |
| [`sima-cli download`](commands/sima-cli-download.md) | 指定されたURLから、ファイルまたはフォルダ全体をダウンロードします。 |
| [`sima-cli install`](commands/sima-cli-install.md) | SiMaパッケージをインストールしてください。 |
| [`sima-cli login`](commands/sima-cli-login.md) | SiMa開発者ポータルで認証を行ってください。 |
| [`sima-cli logout`](commands/sima-cli-logout.md) | キャッシュされた認証情報と設定ファイルを削除することでログアウトできます。 |
| [`sima-cli mla`](commands/sima-cli-mla.md) | 機械学習アクセラレータ用ユーティリティ。 |
| [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md) | Model Zooからモデルにアクセスしてください。 |
| [`sima-cli neat`](commands/sima-cli-neat.md) | Neatのビルドアーティファクトを検索、ダウンロード、インストールしてください。 |
| [`sima-cli network`](commands/sima-cli-network.md) | DevKit のネットワーク IP アドレスを設定します。 |
| [`sima-cli nvme`](commands/sima-cli-nvme.md) | NVMe 操作を、Modalix DevKit 上で実行します。 |
| [`sima-cli packages`](commands/sima-cli-packages.md) | sima-cli パッケージレジストリを管理します（リスト表示、詳細確認、クリーンアップなど）。 |
| [`sima-cli playbooks`](commands/sima-cli-playbooks.md) | プレイブックをインストールおよび管理します（Codex/Claude）。 |
| [`sima-cli sdcard`](commands/sima-cli-sdcard.md) | MLSoc DevKitまたはModalixの早期アクセスユニット用のデータストレージデバイスとして、SDカードを準備してください。 |
| [`sima-cli sdk`](commands/sima-cli-sdk.md) | SiMa SDK 2.0 のコンテナ環境を管理し、ベータ版として公開します。 |
| [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md) | sima-cli を手動で、PyPI または直接的な wheel URL から更新してください。 |
| [`sima-cli serial`](commands/sima-cli-serial.md) | DevKit の UART シリアルコンソールに接続します。 |
| [`sima-cli update`](commands/sima-cli-update.md) | SiMa DevKit またはリモートの SiMa デバイスでソフトウェアを更新します。 |

## コマンド一覧（完全版）

- [`sima-cli`](commands/sima-cli.md)
- [`sima-cli appzoo`](commands/sima-cli-appzoo.md)
- [`sima-cli bootimg`](commands/sima-cli-bootimg.md)
- [`sima-cli device`](commands/sima-cli-device.md)
- [`sima-cli download`](commands/sima-cli-download.md)
- [`sima-cli install`](commands/sima-cli-install.md)
- [`sima-cli login`](commands/sima-cli-login.md)
- [`sima-cli logout`](commands/sima-cli-logout.md)
- [`sima-cli mla`](commands/sima-cli-mla.md)
- [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md)
- [`sima-cli neat`](commands/sima-cli-neat.md)
- [`sima-cli network`](commands/sima-cli-network.md)
- [`sima-cli nvme`](commands/sima-cli-nvme.md)
- [`sima-cli packages`](commands/sima-cli-packages.md)
- [`sima-cli playbooks`](commands/sima-cli-playbooks.md)
- [`sima-cli sdcard`](commands/sima-cli-sdcard.md)
- [`sima-cli sdk`](commands/sima-cli-sdk.md)
- [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md)
- [`sima-cli serial`](commands/sima-cli-serial.md)
- [`sima-cli update`](commands/sima-cli-update.md)
- [`sima-cli appzoo clone`](commands/sima-cli-appzoo-clone.md)
- [`sima-cli appzoo describe`](commands/sima-cli-appzoo-describe.md)
- [`sima-cli appzoo get`](commands/sima-cli-appzoo-get.md)
- [`sima-cli appzoo list`](commands/sima-cli-appzoo-list.md)
- [`sima-cli device discover`](commands/sima-cli-device-discover.md)
- [`sima-cli mla meminfo`](commands/sima-cli-mla-meminfo.md)
- [`sima-cli modelzoo describe`](commands/sima-cli-modelzoo-describe.md)
- [`sima-cli modelzoo get`](commands/sima-cli-modelzoo-get.md)
- [`sima-cli modelzoo list`](commands/sima-cli-modelzoo-list.md)
- [`sima-cli neat artifacts`](commands/sima-cli-neat-artifacts.md)
- [`sima-cli neat download`](commands/sima-cli-neat-download.md)
- [`sima-cli neat install`](commands/sima-cli-neat-install.md)
- [`sima-cli neat sdk`](commands/sima-cli-neat-sdk.md)
- [`sima-cli packages build`](commands/sima-cli-packages-build.md)
- [`sima-cli packages list`](commands/sima-cli-packages-list.md)
- [`sima-cli packages show`](commands/sima-cli-packages-show.md)
- [`sima-cli playbooks apply`](commands/sima-cli-playbooks-apply.md)
- [`sima-cli playbooks delete`](commands/sima-cli-playbooks-delete.md)
- [`sima-cli playbooks describe`](commands/sima-cli-playbooks-describe.md)
- [`sima-cli playbooks install`](commands/sima-cli-playbooks-install.md)
- [`sima-cli playbooks list`](commands/sima-cli-playbooks-list.md)
- [`sima-cli playbooks remove`](commands/sima-cli-playbooks-remove.md)
- [`sima-cli playbooks update`](commands/sima-cli-playbooks-update.md)
- [`sima-cli sdk doctor`](commands/sima-cli-sdk-doctor.md)
- [`sima-cli sdk elxr`](commands/sima-cli-sdk-elxr.md)
- [`sima-cli sdk ls`](commands/sima-cli-sdk-ls.md)
- [`sima-cli sdk model`](commands/sima-cli-sdk-model.md)
- [`sima-cli sdk mpk`](commands/sima-cli-sdk-mpk.md)
- [`sima-cli sdk neat`](commands/sima-cli-sdk-neat.md)
- [`sima-cli sdk network`](commands/sima-cli-sdk-network.md)
- [`sima-cli sdk remove`](commands/sima-cli-sdk-remove.md)
- [`sima-cli sdk ros2`](commands/sima-cli-sdk-ros2.md)
- [`sima-cli sdk run`](commands/sima-cli-sdk-run.md)
- [`sima-cli sdk setup`](commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk start`](commands/sima-cli-sdk-start.md)
- [`sima-cli sdk stop`](commands/sima-cli-sdk-stop.md)
- [`sima-cli sdk yocto`](commands/sima-cli-sdk-yocto.md)
- [`sima-cli sdk doctor network`](commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](commands/sima-cli-sdk-network-rollback.md)
