# Windows に Neat PCIe Host をインストールする

Windows PCIe ホストのリリースは、1 つの `sima-cli` コマンドでインストールできます。
リリースマニフェストには、対応する Scheduler と Core の MSI パッケージ、
インストール順序、バージョン、チェックサムが記録されています。`sima-cli` は
一式をダウンロードして検証してから、各ネイティブパッケージを Windows Installer に渡します。

対応する Platform PCIe パッケージと x64 GStreamer MSVC ランタイムをインストールした後、
PowerShell から実行してください。
マシン全体にインストールする Scheduler と Core の MSI パッケージについて、標準ユーザーは
Windows の管理者承認プロンプトを 1 回だけ承認します。1 つの昇格された手順で Scheduler、
続いて Core をインストールし、Scheduler が失敗した場合は Core を開始しません。昇格される
のはこの手順だけです。CLI、管理対象 Python 環境、カスタマースキルは現在のユーザーのセッションとプロファイルに
残ります。標準ユーザーは、Windows が承認プロンプトを表示できるよう、対話型 PowerShell
ウィンドウからコマンドを実行してください。

```powershell
sima-cli neat install core/pciehost/windows/amd64@v<Neat version>
```

MSI の実行前に、`sima-cli` は Windows OpenSSH ツール、稼働中の Platform サービス、
GStreamer、互換性のある Python のバージョンとアーキテクチャ、pip を確認します。
前提条件を満たさない場合、ネイティブのインストール状態を変更せず停止します。
デフォルトでは `%USERPROFILE%\pyneatpcie` を作成または再利用し、対応する
`pyneatpcie` wheel をインストールして、バージョンとインポートを確認します。
別のアプリケーション環境を使う場合は `--python-executable <path>`、
C++ のみの場合は `--native-only` を指定してください。

Windows Installer の完了後、CLI はインストール済みホスト診断ツールを実行し、
パッケージに対応する `neat-pcie-application-builder` スキルを現在のユーザーの
Codex と Claude のスキルディレクトリに同期します。いずれかの MSI を実行する前に、
CLI は、このスキル ID が未使用であるか、同じインストール済みパッケージのパスに
登録済みであることも確認します。別のソースからのスキル、または同じ ID を持つ
未登録のスキルディレクトリには手を加えず、インストールを中止します。パッケージを
再インストールする場合は、同じパッケージのパスに登録されたスキルだけを置き換えます。

同じコマンドを再実行すると、同じチェックを行い、各 MSI を同じ順序で再び
Windows Installer に渡します。Python パッケージは、このリリースの wheel から
インストールされ、引き続きインポートできる場合にのみそのまま使用します。それ以外の
場合、`sima-cli` は wheel を再インストールします。
ネイティブパッケージや pip の処理が失敗した場合は、表示された問題を修正して
再実行してください。失敗前に完了したネイティブパッケージは報告されます。
Windows Installer のエラー `1625` は、ホストのアプリケーション制御ポリシーが
パッケージを拒否したことを示します。初回リリースの Neat MSI パッケージ、DLL、
Python 拡張モジュールはコード署名されていません。`sima-cli` はこれらをリリース
チャネルから HTTPS でダウンロードし、各パッケージの SHA-256 チェックサムを
ダウンロード後と、Windows Installer または pip を起動する前に再度検証し（標準ユーザーの
場合は、承認プロンプトの前と昇格された手順の中の両方で検証します）、一致しなくなった
パッケージがあれば停止します。企業のアプリケーション制御ポリシーにより、未署名のバイナリーが
ブロックされる場合があります。Windows 管理者にポリシーの確認を依頼してください。
`sima-cli` は Windows のセキュリティ制御を無効にしません。
Windows Installer を起動できない場合、`sima-cli` は対象パッケージ、権限に関する
案内、および予定されていたインストーラーログの保存先を表示します。

`sima-cli` はリリースのインストール時に、各 MSI のプロダクトコードをレジストリの
記録に保存します。1 つのコマンドがそのコードを使い、1 回の管理者承認プロンプトのもと、
1 つの昇格された手順で Core、続いて Scheduler をアンインストールし、その後記録を削除します。

```powershell
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

`INSTALLED_VERSION` は `packages list` に表示される正確な値に置き換えてください。
すでに存在しないパッケージは削除済みとして扱います。Windows Installer が失敗した場合、
Core が残っている間は Scheduler を削除せず、記録を `remove-failed` 状態で保持するため、
同じコマンドを再実行できます。終了コード `3010` は、両方のパッケージが削除され、
完了するには Windows の再起動が必要であることを示します。

このコマンドは、現在のユーザーの Python パッケージ、管理対象 Python 環境、同期済みの
カスタマースキルを削除しません。これらを削除するコマンドを表示します。

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
```
