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
Codex と Claude のスキルディレクトリに同期します。初回はカードへの
パスワードなし SSH 接続を設定してください。

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

同じコマンドを再実行すると、ネイティブパッケージ一式を修復またはアップグレードします。
対応する Python パッケージが正常に動作していれば、検証後にそのまま使用します。
ネイティブパッケージや pip の処理が失敗した場合は、表示された問題を修正して
再実行してください。失敗前に完了したネイティブパッケージは報告されます。
Windows Installer のエラー `1625` は、ホストのアプリケーション制御ポリシーが
パッケージを拒否したことを示します。承認済みの署名付きリリースを使うか、Windows
管理者にポリシーの確認を依頼し、セキュリティ制御を無効にしないでください。
Windows Installer を起動できない場合、`sima-cli` は対象パッケージ、権限に関する
案内、および予定されていたインストーラーログの保存先を表示します。

Scheduler と Core は Windows の「インストールされているアプリ」または `msiexec`
で削除します。Python パッケージは管理対象環境から別途削除してください。

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall pyneatpcie
```
