# 在 Windows 安裝 Neat PCIe Host

使用一個 `sima-cli` 命令即可安裝 Windows PCIe 主機版本。發行清單列出
相互配對的 Scheduler 和 Core MSI 套件、安裝順序、版本與檢查碼。
`sima-cli` 會先下載並驗證整組套件，再將各個原生套件交給 Windows Installer。

請先安裝支援的 Platform PCIe 套件與 x64 GStreamer MSVC 執行階段，
再從 PowerShell 執行命令。已簽署的驅動程式、主機程式庫和
`SiMaMlaDaemon` 服務由 Platform 套件管理；Neat 不會替換或更新它們。
Windows 可能會要求管理員核准安裝於整台電腦的 Scheduler 與 Core MSI 套件。
只有這些原生安裝程式會提升權限；CLI、受管理的 Python 環境與客戶技能仍留在
目前使用者的工作階段與設定檔中。標準使用者必須從互動式 PowerShell 視窗執行
命令，Windows 才能顯示核准提示。

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0
```

啟動任一 MSI 前，`sima-cli` 會檢查 Windows OpenSSH 工具、執行中的 Platform
服務、GStreamer，以及相容的 Python 版本、架構與 pip。若缺少必要條件，
便會停止，不更動原生安裝。預設會建立或重複使用 `%USERPROFILE%\pyneatpcie`，
將配對的 `pyneatpcie` wheel 安裝至該環境，再檢查版本與匯入是否正常。
使用 `--python-executable <path>` 可指定其他應用程式環境；
使用 `--native-only` 則只安裝 C++ 所需套件。

Windows Installer 完成後，CLI 會執行已安裝的主機診斷工具，並將與套件配對的
`neat-pcie-application-builder` 技能同步到目前使用者的 Codex 與 Claude
技能目錄。在執行任一 MSI 前，CLI 也會檢查此技能 ID 尚未使用，或已登錄至相同的
已安裝套件路徑。若技能來自其他來源，或存在具有相同 ID 但未登錄的技能目錄，CLI
不會變更該技能或目錄，並會停止安裝。再次安裝套件時，只能取代登錄至相同套件
路徑的技能。首次安裝時，請設定免密碼 SSH 卡片連線：

```powershell
& "C:\Program Files\SimaPCIeHost\bin\pcie-setup.cmd"
```

設定命令會從本機的 `10.0.N.1` 管理連線探索卡片位址。無法探索時，請使用
`-Hosts 10.0.0.2`。只有在公開金鑰尚未註冊時，OpenSSH 才會提示輸入卡片密碼，
命令不會儲存密碼。包裝程式僅對該已安裝指令碼的處理程序套用
`ExecutionPolicy Bypass`，讓首次發行的未簽署工具能在 Windows PowerShell
預設本機原則下執行。這不會變更使用者或電腦的執行原則；管理員強制套用的
原則仍有優先權。

再次執行相同命令會重新進行相同的檢查，並依相同順序再次將各個 MSI 交給
Windows Installer。只有當 Python 套件是從此版本的 wheel 安裝且仍可匯入時才會保留；
否則 `sima-cli` 會重新安裝 wheel。若原生套件或 pip 步驟失敗，請修正顯示的問題後
再執行相同命令；安裝程式會列出失敗前已完成的原生套件。Windows Installer 錯誤 `1625`
表示主機的應用程式控制原則拒絕了套件。此版本的 Neat MSI 套件、DLL 與 Python
擴充模組皆未經程式碼簽署。`sima-cli` 透過 HTTPS 從發行通道下載這些檔案，在下載後
驗證每個套件的 SHA-256 檢查碼，並在即將安裝前再次驗證；若套件不再相符便會停止。
企業的應用程式控制原則可能會封鎖未簽署的二進位檔；請 Windows 管理員檢視該原則。
`sima-cli` 不會停用任何 Windows 安全性控制。若無法啟動 Windows Installer，
`sima-cli` 會顯示嘗試安裝的套件、權限指引，以及預定的安裝記錄位置。

透過 Windows「已安裝的應用程式」或 `msiexec` 移除 Scheduler 和 Core。
這些 MSI 不負責目前使用者的 Python 套件、已同步的客戶技能或 CLI 登錄記錄；
請從 PowerShell 分別移除：

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

請將 `INSTALLED_VERSION` 替換為 `packages list` 顯示的精確值。最後一個命令只會
移除本機 CLI 記錄，不會解除安裝檔案，因此應在上述各安裝程式完成移除後執行。
