# sima-cli 指令參考

為 sima-cli 命令列介面所產生的 Markdown 參考文件。

## 安裝

對於大多數使用者來說，請從您作業系統的官方公開安裝程式網址下載並安裝最新版本。

### Linux、macOS 和 DevKit

從終端機執行安裝程式：

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

安裝完成後，開啟一個新的終端機或重新載入您的 shell 設定檔，然後驗證安裝是否成功：

```bash
sima-cli --version
```

### Windows PowerShell

從 PowerShell 下載並執行 Windows 安裝程式：

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

安裝完成後，開啟新的命令提示字元或 PowerShell 視窗，然後驗證安裝是否成功：

```powershell
sima-cli --version
```

### 進階：選擇一個分支或版本。

僅在您需要選擇特定的經過測試的分支版本或發布版本，而不是安裝最新的官方 PyPI 發布版本時，才使用 `install.py`。

在 Linux、macOS 或 DevKit 上：

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/install.py -o sima-cli-install.py
python3 sima-cli-install.py
```

安裝特定分支或版本：

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

在 Windows 的 PowerShell 中：

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py
```

若要安裝特定分支或版本：

```powershell
python .\sima-cli-install.py feature/my-branch latest
python .\sima-cli-install.py v2.1.6 latest
```

發布標籤，例如 `v2.1.6`，可從公用 PyPI 安裝。分支名稱可安裝來自 `artifacts.neat.sima.ai/sima-cli` 的經過測試的成品。

也可以直接安裝公用 PyPI 的發布版本：

```bash
pip install sima-cli
```

## 指南

| 指南 | 描述 |
| --- | --- |
| [Neat SDK 網路設定](sdk-networking/index.md) | 了解 SDK、Docker、Insight 和 DevKit 的網路設定方式。 |
| [疑難排解：SDK 網路問題](sdk-networking/troubleshooting.md) | 執行網路診斷，修復 Linux 共用網路的路由設定，並收集支援資料包。 |
| [回滾 SDK 網路變更](sdk-networking/rollback.md) | 預覽並撤銷 Linux SDK 網路設定或修復變更。 |

## 頂層指令

| 指令 | 描述 |
| --- | --- |
| [`sima-cli appzoo`](../../../sima-cli/commands/sima-cli-appzoo.md) | 從 App Zoo 取得範例應用程式。 |
| [`sima-cli bootimg`](../../../sima-cli/commands/sima-cli-bootimg.md) | 為 SiMa DevKit 準備一個可開機的映像檔。 |
| [`sima-cli device`](../../../sima-cli/commands/sima-cli-device.md) | 在區域網路中搜尋附近的 SiMa.ai 裝置。 |
| [`sima-cli download`](../../../sima-cli/commands/sima-cli-download.md) | 從指定的網址下載檔案或整個資料夾。 |
| [`sima-cli install`](../../../sima-cli/commands/sima-cli-install.md) | 安裝 SiMa 套件。 |
| [`sima-cli login`](../../../sima-cli/commands/sima-cli-login.md) | 透過 SiMa 開發者入口網站進行驗證。 |
| [`sima-cli logout`](../../../sima-cli/commands/sima-cli-logout.md) | 登出時，請刪除快取中的憑證和設定檔。 |
| [`sima-cli mla`](../../../sima-cli/commands/sima-cli-mla.md) | 機器學習加速器工具。 |
| [`sima-cli modelzoo`](../../../sima-cli/commands/sima-cli-modelzoo.md) | 從 Model Zoo 存取模型。 |
| [`sima-cli neat`](../../../sima-cli/commands/sima-cli-neat.md) | 尋找、下載並安裝 Neat 建置產生的成品。 |
| [`sima-cli network`](../../../sima-cli/commands/sima-cli-network.md) | 在 DevKit 上設定網路 IP 位址。 |
| [`sima-cli nvme`](../../../sima-cli/commands/sima-cli-nvme.md) | 在 Modalix DevKit 上執行 NVMe 作業。 |
| [`sima-cli packages`](../../../sima-cli/commands/sima-cli-packages.md) | 管理 sima-cli 套件登錄檔（列出、檢查、清理等）。 |
| [`sima-cli playbooks`](../../../sima-cli/commands/sima-cli-playbooks.md) | 安裝並管理劇本（Codex/Claude）。 |
| [`sima-cli sdcard`](../../../sima-cli/commands/sima-cli-sdcard.md) | 為 MLSoc DevKit 或 Modalix 的早期測試版本，準備好 SD 卡作為資料儲存裝置。 |
| [`sima-cli sdk`](../../../sima-cli/commands/sima-cli-sdk.md) | 管理並啟動 SiMa SDK 2.0 容器環境（測試版）。 |
| [`sima-cli selfupdate`](../../../sima-cli/commands/sima-cli-selfupdate.md) | 手動從 PyPI 或直接的 wheel URL 更新 sima-cli。 |
| [`sima-cli serial`](../../../sima-cli/commands/sima-cli-serial.md) | 連接到 DevKit 的 UART 序列埠控制台。 |
| [`sima-cli update`](../../../sima-cli/commands/sima-cli-update.md) | 更新 SiMa DevKit 或遠端 SiMa 裝置上的軟體。 |

## 完整指令清單

- [`sima-cli`](../../../sima-cli/commands/sima-cli.md)
- [`sima-cli appzoo`](../../../sima-cli/commands/sima-cli-appzoo.md)
- [`sima-cli bootimg`](../../../sima-cli/commands/sima-cli-bootimg.md)
- [`sima-cli device`](../../../sima-cli/commands/sima-cli-device.md)
- [`sima-cli download`](../../../sima-cli/commands/sima-cli-download.md)
- [`sima-cli install`](../../../sima-cli/commands/sima-cli-install.md)
- [`sima-cli login`](../../../sima-cli/commands/sima-cli-login.md)
- [`sima-cli logout`](../../../sima-cli/commands/sima-cli-logout.md)
- [`sima-cli mla`](../../../sima-cli/commands/sima-cli-mla.md)
- [`sima-cli modelzoo`](../../../sima-cli/commands/sima-cli-modelzoo.md)
- [`sima-cli neat`](../../../sima-cli/commands/sima-cli-neat.md)
- [`sima-cli network`](../../../sima-cli/commands/sima-cli-network.md)
- [`sima-cli nvme`](../../../sima-cli/commands/sima-cli-nvme.md)
- [`sima-cli packages`](../../../sima-cli/commands/sima-cli-packages.md)
- [`sima-cli playbooks`](../../../sima-cli/commands/sima-cli-playbooks.md)
- [`sima-cli sdcard`](../../../sima-cli/commands/sima-cli-sdcard.md)
- [`sima-cli sdk`](../../../sima-cli/commands/sima-cli-sdk.md)
- [`sima-cli selfupdate`](../../../sima-cli/commands/sima-cli-selfupdate.md)
- [`sima-cli serial`](../../../sima-cli/commands/sima-cli-serial.md)
- [`sima-cli update`](../../../sima-cli/commands/sima-cli-update.md)
- [`sima-cli appzoo clone`](../../../sima-cli/commands/sima-cli-appzoo-clone.md)
- [`sima-cli appzoo describe`](../../../sima-cli/commands/sima-cli-appzoo-describe.md)
- [`sima-cli appzoo get`](../../../sima-cli/commands/sima-cli-appzoo-get.md)
- [`sima-cli appzoo list`](../../../sima-cli/commands/sima-cli-appzoo-list.md)
- [`sima-cli device discover`](../../../sima-cli/commands/sima-cli-device-discover.md)
- [`sima-cli mla meminfo`](../../../sima-cli/commands/sima-cli-mla-meminfo.md)
- [`sima-cli modelzoo describe`](../../../sima-cli/commands/sima-cli-modelzoo-describe.md)
- [`sima-cli modelzoo get`](../../../sima-cli/commands/sima-cli-modelzoo-get.md)
- [`sima-cli modelzoo list`](../../../sima-cli/commands/sima-cli-modelzoo-list.md)
- [`sima-cli neat artifacts`](../../../sima-cli/commands/sima-cli-neat-artifacts.md)
- [`sima-cli neat download`](../../../sima-cli/commands/sima-cli-neat-download.md)
- [`sima-cli neat install`](../../../sima-cli/commands/sima-cli-neat-install.md)
- [`sima-cli neat sdk`](../../../sima-cli/commands/sima-cli-neat-sdk.md)
- [`sima-cli packages build`](../../../sima-cli/commands/sima-cli-packages-build.md)
- [`sima-cli packages list`](../../../sima-cli/commands/sima-cli-packages-list.md)
- [`sima-cli packages show`](../../../sima-cli/commands/sima-cli-packages-show.md)
- [`sima-cli playbooks apply`](../../../sima-cli/commands/sima-cli-playbooks-apply.md)
- [`sima-cli playbooks delete`](../../../sima-cli/commands/sima-cli-playbooks-delete.md)
- [`sima-cli playbooks describe`](../../../sima-cli/commands/sima-cli-playbooks-describe.md)
- [`sima-cli playbooks install`](../../../sima-cli/commands/sima-cli-playbooks-install.md)
- [`sima-cli playbooks list`](../../../sima-cli/commands/sima-cli-playbooks-list.md)
- [`sima-cli playbooks remove`](../../../sima-cli/commands/sima-cli-playbooks-remove.md)
- [`sima-cli playbooks update`](../../../sima-cli/commands/sima-cli-playbooks-update.md)
- [`sima-cli sdk doctor`](../../../sima-cli/commands/sima-cli-sdk-doctor.md)
- [`sima-cli sdk elxr`](../../../sima-cli/commands/sima-cli-sdk-elxr.md)
- [`sima-cli sdk ls`](../../../sima-cli/commands/sima-cli-sdk-ls.md)
- [`sima-cli sdk model`](../../../sima-cli/commands/sima-cli-sdk-model.md)
- [`sima-cli sdk mpk`](../../../sima-cli/commands/sima-cli-sdk-mpk.md)
- [`sima-cli sdk neat`](../../../sima-cli/commands/sima-cli-sdk-neat.md)
- [`sima-cli sdk network`](../../../sima-cli/commands/sima-cli-sdk-network.md)
- [`sima-cli sdk remove`](../../../sima-cli/commands/sima-cli-sdk-remove.md)
- [`sima-cli sdk ros2`](../../../sima-cli/commands/sima-cli-sdk-ros2.md)
- [`sima-cli sdk run`](../../../sima-cli/commands/sima-cli-sdk-run.md)
- [`sima-cli sdk setup`](../../../sima-cli/commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk start`](../../../sima-cli/commands/sima-cli-sdk-start.md)
- [`sima-cli sdk stop`](../../../sima-cli/commands/sima-cli-sdk-stop.md)
- [`sima-cli sdk yocto`](../../../sima-cli/commands/sima-cli-sdk-yocto.md)
- [`sima-cli sdk doctor network`](../../../sima-cli/commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../../../sima-cli/commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../../../sima-cli/commands/sima-cli-sdk-network-rollback.md)
