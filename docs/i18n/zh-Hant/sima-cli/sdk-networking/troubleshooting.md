# 疑難排解：SDK 網路問題

當 SDK 容器、Insight UI、DevKit SSH、RTSP、WebRTC 視訊或工作區同步未如預期運作時，請使用網路診斷工具。

診斷指令為唯讀模式。它會檢查主機路由、Docker 容器狀態、已發布的連接埠、Insight 連接埠對應、防火牆狀態以及選取的 Linux 網路詳細資訊。

## 快速檢查一下。

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

如果存在多個 SDK 容器，請提供容器名稱：

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --container <container-name>
```

## 收集一份支援資料包。

當您需要 SiMa 支援時，請收集以下資料：

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect
```

選擇輸出位置：

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect --output ~/sima-sdk-network-doctor.tar.gz
```

這個套件包含經過清理的主機、Docker、路由、防火牆、NetworkManager，以及 Insight 埠對應診斷工具。它的用途是提供網路支援。請勿手動將 SSH 金鑰、Docker 憑證檔案、瀏覽器 Cookie 或其他機密資訊新增到封存檔中。

## 常見發現。

| 尋找 | 意義 | 建議的行動方案 |
| --- | --- | --- |
| `vpn-route` | 前往 DevKit 的路徑會經過 VPN 或隧道介面。 | 斷開 VPN 連線，或調整路由設定，讓 DevKit 使用面對實際 DevKit 的介面。 |
| `missing-simasdkbridge` | SDK 容器正在執行，但尚未連接到 `simasdkbridge`。 | 重新建立或重新啟動 SDK，使用 `sima-cli sdk setup`。避免直接啟動 VS Code 開發容器，以用於 DevKit 工作流程。 |
| `host-network-mode` | SDK 容器已使用 Docker 主機網路啟動。 | 重新建立 SDK，使用 `sima-cli sdk setup`；主機網路不是支援的 Insight 埠模型。 |
| `port-map-mismatch` | Docker 公布的端口與產生的 Insight 端口映射不符。 | 重新建立 SDK 容器，以便 `sima-cli` 可以重新產生連接埠，並與 Insight 的設定一起使用。 |
| `stale-port-bindings` | 已停止的 SDK 容器已儲存 Docker 埠繫結，但這些繫結現在已無法使用。 | 透過 `sima-cli sdk setup` 移除或重新建立容器。 |
| `nm-shared-iptables-blocking` | NetworkManager 的共享網路功能會在達到 Docker 規則之前，拒絕 SDK 橋接流量。 | 執行 `sima-cli sdk network repair --devkit <devkit-ip>`；如果修復程式必須在重新連線或重新啟動後仍然有效，請新增 `--persist`。 |
| `container-devkit-reachability` | SDK 容器無法確認是否能透過 SSH 或 ping 命令連線至 DevKit。 | 確認 DevKit 的 IP 位址、線纜/網路路徑、DevKit 的 SSH 服務，以及主機防火牆。 |

## DevKit 依賴套件下載失敗。

當 DevKit 使用建議的共用網路連線時，它會依賴主機來取得網際網路連線。如果 DevKit 上發生套件安裝、相依性下載或外部服務存取失敗的情況，請檢查：

- 主機電腦可以連接到網際網路。
- 主機的共用網路介面仍然處於啟用狀態。
- VPN 路由未正確擷取 DevKit 路由。
- Linux 如果 NetworkManager 重新建立共用連線，轉發／NAT 規則仍然存在。

執行：

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

如果醫生回報了 NetworkManager 共享網路出現封鎖問題，請執行醫生回報中顯示的修復指令。

## 修復 Linux 共用網路的路由設定。

在採用 NetworkManager 共享網路的 Ubuntu/Linux 主機上，執行：

```bash
sima-cli sdk network repair --devkit <devkit-ip>
```

在套用執行階段修復後，安裝持續性分派器掛鉤：

```bash
sima-cli sdk network repair --devkit <devkit-ip> --persist
```

本次修復的範圍僅限於 SDK 橋接和偵測到的面向 DevKit 的共享網路路徑。它不會將 Docker 切換到主機網路，也不會將全域 `FORWARD` 原則設定為 `ACCEPT`。

## 維修後請進行確認。

再次執行診斷程式：

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

然後開啟以下由系統提供的 Insight URL：

```bash
neat --json
```

如果瀏覽器無法開啟 Insight，請確認 `mainUI` 主機的連接埠是否可從主機瀏覽器存取，以及 SDK 容器是否由 `sima-cli sdk setup` 啟動。
