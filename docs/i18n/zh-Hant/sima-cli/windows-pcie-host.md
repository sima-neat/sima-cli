# 在 Windows 安裝 Neat PCIe Host

使用一個 `sima-cli` 命令即可安裝 Windows PCIe 主機版本。發行清單列出
相互配對的 Scheduler 和 Core MSI 套件、安裝順序、版本與檢查碼。
`sima-cli` 會先下載並驗證整組套件，再將各個原生套件交給 Windows Installer。

本頁僅說明 `sima-cli` 命令。關於必要條件、卡片註冊、驗證與復原，請依照
[安裝 PCIe 主機](/getting-started/neat-library/pcie-host)操作。

請先安裝支援的 Platform PCIe 套件、x64 GStreamer MSVC 執行階段，以及（未使用
`--native-only` 時）64 位元 Python 3.14，再從 PowerShell 執行命令。
在未提升權限的 PowerShell 視窗中，對於安裝於整台電腦的 Scheduler 與 Core MSI 套件，
只需核准一次 Windows 管理員提示：一個提升權限的步驟會先安裝 Scheduler，再安裝 Core；若 Scheduler 失敗，
便不會啟動 Core。只有這個步驟會提升權限；CLI、受管理的 Python 環境與客戶技能仍留在
目前使用者的工作階段與設定檔中。請從互動式 PowerShell 視窗執行
命令，Windows 才能顯示核准提示；以「以系統管理員身分執行」開啟的視窗則不需要提示。

```powershell
sima-cli neat install core/pciehost/windows/amd64@v<Neat version>
```

請將 `<Neat version>` 替換為有提供 Windows 主機套件的 Neat 發行版本；它與 Ubuntu
主機套件使用的發行標籤相同。

啟動任一 MSI 前，`sima-cli` 會檢查 Windows OpenSSH 工具、執行中的 Platform
服務、GStreamer，以及相容的 Python 版本、架構、pip 與 wheel 標籤（free-threaded
Python 不接受此 wheel）。若缺少必要條件，
便會停止，不更動原生安裝。預設會建立或重複使用 `%USERPROFILE%\pyneatpcie`，
將配對的 `pyneatpcie` wheel 安裝至該環境，再檢查版本與匯入是否正常。
使用 `--python-executable <path>` 可指定其他應用程式環境；
使用 `--native-only` 則只安裝 C++ 所需套件。

Windows Installer 完成後，CLI 會執行已安裝的主機診斷工具，並將與套件配對的
`neat-pcie-application-builder` 技能同步到目前使用者的 Codex 與 Claude
技能目錄。在執行任一 MSI 前，CLI 也會檢查此技能 ID 尚未使用，或已登錄至相同的
已安裝套件路徑。若技能來自其他來源，或存在具有相同 ID 但未登錄的技能目錄，CLI
不會變更該技能或目錄，並會停止安裝。再次安裝套件時，只能取代登錄至相同套件
路徑的技能。

最後，如同 Ubuntu 安裝程式會執行 `pcie-setup.sh`，CLI 會在同一個視窗中、以該視窗的權限
執行已安裝的 `pcie-setup.cmd`，使用 `--native-only` 時也一樣。此步驟會設定連到每張卡的
免密碼 SSH，並可能要求輸入卡的密碼。若要略過，請在命令前設定
`$env:SIMAPCIE_SKIP_SETUP = "1"`；由於首次註冊需要密碼，沒有互動式輸入的工作階段也會
略過此步驟。略過時會顯示稍後要執行的命令。若此步驟失敗，已安裝的套件會保留，登錄記錄的
狀態為 `setup-failed`，命令會以非零結束代碼結束，並顯示重新執行此步驟的命令。

再次執行相同命令會重新進行相同的檢查，並依相同順序修復每個已安裝的 MSI：`sima-cli`
會以 `/fomus` 將其交給 Windows Installer，重新安裝遺失或比此發行版本舊的檔案，並重新寫入
套件的登錄項目和捷徑；已存在且為最新的檔案則保持不變。若需要取代的檔案正在使用中，
Windows Installer 可能會要求重新啟動，因此請在重新執行命令前關閉執行中的 PCIe 應用程式。
較新的發行版本會就地升級
套件，並取代舊發行版本的登錄記錄。Windows Installer 不會就地降級套件；若要安裝較舊的
發行版本，請依下文移除已安裝的版本，再安裝較舊的版本。只有當 Python 套件是從此版本的 wheel 安裝且仍可匯入時才會保留；
否則 `sima-cli` 會重新安裝 wheel。若原生套件或 pip 步驟失敗，請修正顯示的問題；
若 Windows Installer 要求重新啟動，命令會以結束代碼 `3010` 結束，請重新啟動 Windows。
接著執行 `sima-cli` 顯示的重試命令：它指定本次執行所解析的確切發行版本，因此重試不會
選到其他版本。安裝程式會列出失敗前已完成的原生套件。Windows Installer 錯誤 `1625`
表示主機的應用程式控制原則拒絕了套件。首次發行的 Neat MSI 套件、DLL 與 Python
擴充模組皆未經程式碼簽署。`sima-cli` 透過 HTTPS 從發行通道下載這些檔案，在下載後
驗證每個套件的 SHA-256 檢查碼，並在啟動 Windows Installer 或 pip 前再次驗證（在未提升權限的
視窗中，會在核准提示之前與提升權限的步驟之中各驗證一次）；若套件不再相符便會停止。
企業的應用程式控制原則可能會封鎖未簽署的二進位檔；請 Windows 管理員檢視該原則。
`sima-cli` 不會停用任何 Windows 安全性控制。若無法啟動 Windows Installer，
`sima-cli` 會顯示嘗試安裝的套件、權限指引，以及預定的安裝記錄位置。

`sima-cli` 安裝發行版本時，會將每個 MSI 的產品代碼記錄在登錄記錄中。一個命令會使用
這些代碼，在一次管理員核准提示下，於一個提升權限的步驟中先解除安裝 Core，再解除安裝
Scheduler，然後移除該記錄：

```powershell
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

請將 `INSTALLED_VERSION` 替換為 `packages list` 顯示的精確值。已不存在的套件視為已移除。
若 Windows Installer 失敗，只要 Core 仍在，就不會移除 Scheduler；記錄會以
`remove-failed` 狀態保留，可再次執行同一命令。結束代碼 `3010` 表示兩個套件皆已移除，
且必須重新啟動 Windows 才能完成。

此命令不會刪除目前使用者的 Python 套件、受管理的 Python 環境或已同步的客戶技能；
它會顯示移除這些項目的命令：

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
```

`sima-cli` 會透過 `%USERPROFILE%\.sima-cli\registry.json` 中的記錄找到這些套件，
因此請在移除 `sima-cli` 或該資料夾之前先移除套件。若記錄已不存在，可以再次執行已安裝
發行版本的安裝命令以還原記錄，然後依上述方式移除套件；或在 設定 > 應用程式 >
已安裝的應用程式 中先解除安裝 `sima-pcie-host`，再解除安裝 `simaai-scheduler-pcie-host`。
若您刪除了整個 `%USERPROFILE%\.sima-cli` 資料夾，安裝命令會先停止，並顯示技能已
存在於 sima-cli 登錄之外的訊息；請刪除訊息中列出的技能資料夾，然後再次執行安裝命令。

記錄屬於執行安裝的 Windows 帳戶，而套件是為整台電腦安裝的。其他帳戶執行相同的安裝命令，
即可取得自己的記錄、Python 環境、技能與卡片 SSH 金鑰；此時套件會被修復，而不會重新安裝。
