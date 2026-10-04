# Neat SDK 網路設定

Neat SDK，也稱為 Neat 開發環境，在 Docker 容器中執行。`sima-cli sdk setup` 會建立容器、準備主機工作區掛載點、在啟用時啟動 Insight，並發布主機瀏覽器和 DevKit 在開發期間使用的容器連接埠。

本指南說明在使用 SDK 與 Modalix DevKit 時，最重要的網路組件。

## 網路模型

典型的 SDK 設定包含三個參與者：

- 主機電腦：執行 Docker、`sima-cli`、SDK 容器以及您的瀏覽器。
- SDK 容器：執行 Neat 開發環境，以及可選的 Insight 服務。
- DevKit：透過本機網路或直接的乙太網路／共用網路連線來連接主機。

支援的 SDK 容器網路為 `simasdkbridge`。當您需要 DevKit 和 Insight 網路時，請勿直接從 Docker 或 VS Code Dev Containers 擴充功能啟動 SDK 容器。請使用 `sima-cli sdk setup`，以便將容器、連接埠對應、工作區共用和 Insight 設定一同建立。

## 什麼設定會生效？

當您使用 DevKit 整合功能執行設定時，`sima-cli` 會進行以下設定：

- Docker 網路：建立或重新使用 `simasdkbridge`。
- SDK 容器埠：將 Insight UI、視訊、RTSP、中繼資料、WebRTC 和網頁 SSH 埠發佈到主機。
- Insight 埠對應：將產生的埠對應寫入 SDK 工作區的設定中。
- 工作區共用：設定當使用 `--devkit` 時，主機對 DevKit 工作區的存取權限。
- DevKit 網路存取：透過主機的共用網路連線，將 DevKit 導向正確的網路路徑，以便在需要時，DevKit 可以存取套件儲存庫並下載相依性。
- Linux 共享網路路由：在 Ubuntu/Linux 共享網路連線中，當需要時，會套用範圍型轉送/NAT 規則。

使用方式：

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

## 持續的 Linux 共用網路修復

在某些 Ubuntu 系統上，NetworkManager 的網路共享功能可能會在網路線重新連接或系統重新啟動時重新建立防火牆規則。在這種情況下，設定程序可能會修復目前的會話，但會提示使用者，此修復並非永久性的。

對於互動式設定，`sima-cli` 會在安裝永久性的 NetworkManager 觸發器設定檔之前進行確認。

對於自動化設定，請明確選擇啟用：

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```

持續設定檔僅適用於偵測到的共用網路路徑。它並非由 `-y` 單獨安裝。

## DevKit 網路連線

當 DevKit 透過建議的 Linux/macOS 共用網路連線連接時，它會依賴主機電腦來取得網路連線。這在 DevKit 需要在設定和開發期間下載套件、安裝相依性或存取外部服務時非常重要。

如果 DevKit 命令在嘗試下載相依性時失敗，請驗證主機是否具有可運作的網路連線，以及共用網路路徑是否仍然處於作用狀態。在 Linux 上，網路診斷工具可以識別共用網路轉送問題：

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

## Insight，以及已發布的連接埠

Insight 會使用產生的主機連接埠。如果有的話，會使用預設值，但如果某個連接埠已經在使用中，`sima-cli` 則可能會配置非預設的連接埠。

若要從 SDK 介面內檢查目前使用的對應關係：

```bash
neat --json
```

請尋找 `exposedPorts` 和 `insight.webUiUrl` 項目。在設定 DevKit 串流、RTSP 來源、瀏覽器存取或應用程式輸出目標時，請使用這些值。

## 相關頁面

- [疑難排解 SDK 網路問題](troubleshooting.md)
- [回滾 SDK 網路變更](rollback.md)
- [`sima-cli sdk setup`](../../../../sima-cli/commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk doctor network`](../../../../sima-cli/commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../../../../sima-cli/commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../../../../sima-cli/commands/sima-cli-sdk-network-rollback.md)
