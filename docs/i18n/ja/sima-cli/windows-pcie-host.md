# Windows に Neat PCIe Host をインストールする

Windows PCIe ホストのリリースは、1 つの `sima-cli` コマンドでインストールできます。
リリースマニフェストには、対応する Scheduler と Core の MSI パッケージ、
インストール順序、バージョン、チェックサムが記録されています。`sima-cli` は
一式をダウンロードして検証してから、各ネイティブパッケージを Windows Installer に渡します。

対応する Platform PCIe パッケージと x64 GStreamer MSVC ランタイムをインストールした後、
PowerShell から実行してください。署名済みドライバー、ホストライブラリ、
`SiMaMlaDaemon` サービスは Platform パッケージが提供します。Neat はこれらを置換・更新しません。
マシン全体にインストールする Scheduler と Core の MSI パッケージについて、Windows が
管理者の承認を求める場合があります。昇格されるのはこれらのネイティブインストーラーだけです。
CLI、管理対象 Python 環境、カスタマースキルは現在のユーザーのセッションとプロファイルに
残ります。標準ユーザーは、Windows が承認プロンプトを表示できるよう、対話型 PowerShell
ウィンドウからコマンドを実行してください。

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0
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
初回はカードへのパスワードなし SSH 接続を設定してください。

```powershell
& "C:\Program Files\SimaPCIeHost\bin\pcie-setup.cmd"
```

このコマンドはローカルの `10.0.N.1` 管理リンクからカードのアドレスを検出します。
自動検出できない場合は `-Hosts 10.0.0.2` を指定してください。公開鍵が未登録の
場合にのみ OpenSSH がカードのパスワードを要求し、コマンドはパスワードを保存しません。
ラッパーは、初回リリースの未署名ユーティリティを Windows PowerShell の標準ローカル
ポリシーで実行できるよう、そのインストール済みスクリプトのプロセスだけに
`ExecutionPolicy Bypass` を適用します。ユーザーやコンピューターの実行ポリシーは
変更せず、管理者が強制したポリシーが優先されます。

同じコマンドを再実行すると、同じチェックを行い、各 MSI を同じ順序で再び
Windows Installer に渡します。Python パッケージは、このリリースの wheel から
インストールされ、引き続きインポートできる場合にのみそのまま使用します。それ以外の
場合、`sima-cli` は wheel を再インストールします。
ネイティブパッケージや pip の処理が失敗した場合は、表示された問題を修正して
再実行してください。失敗前に完了したネイティブパッケージは報告されます。
Windows Installer のエラー `1625` は、ホストのアプリケーション制御ポリシーが
パッケージを拒否したことを示します。このリリースの Neat MSI パッケージ、DLL、
Python 拡張モジュールはコード署名されていません。`sima-cli` はこれらをリリース
チャネルから HTTPS でダウンロードし、各パッケージの SHA-256 チェックサムを
ダウンロード後とインストール直前の 2 回検証し、一致しなくなったパッケージがあれば
停止します。企業のアプリケーション制御ポリシーにより、未署名のバイナリーが
ブロックされる場合があります。Windows 管理者にポリシーの確認を依頼してください。
`sima-cli` は Windows のセキュリティ制御を無効にしません。
Windows Installer を起動できない場合、`sima-cli` は対象パッケージ、権限に関する
案内、および予定されていたインストーラーログの保存先を表示します。

Scheduler と Core は Windows の「インストールされているアプリ」または `msiexec`
で削除します。これらの MSI は、現在のユーザーの Python パッケージ、同期済みの
カスタマースキル、CLI レジストリの記録を管理しません。PowerShell から別途削除してください。

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

`INSTALLED_VERSION` は `packages list` に表示される正確な値に置き換えてください。
最後のコマンドはローカルの CLI 記録だけを削除し、ファイルはアンインストールしません。
そのため、上記の各インストーラーによる削除後に実行してください。
