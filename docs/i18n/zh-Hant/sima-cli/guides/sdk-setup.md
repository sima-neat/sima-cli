# SDK 設定與擴充功能管理

執行 `sima-cli sdk setup` 時，選擇選用 SDK 服務、瀏覽器版 VS Code 擴充功能，以及 Model Compiler 安裝來源。

命令參考：[`sima-cli sdk setup`](../../../../sima-cli/commands/sima-cli-sdk-setup.md)

## 建置與分享容器映像檔

使用 DevKit 設定 SDK 3.0 或更新版本時，sima-cli 會提供建立小型本機容器登錄庫的選項。登錄庫會將映像檔儲存在主機上，因此即使 SDK 容器被替換，映像檔仍可繼續使用。

登錄庫從連接埠 5050 啟動。若該連接埠已被使用，sima-cli 會自動選擇下一個可用的連接埠，並顯示所選的位址。

設定也會在 SDK shell 中提供兩個位址：

- `SIMA_CONTAINER_REGISTRY` 是 SDK 用來推送映像檔的位址。
- `SIMA_DEVKIT_CONTAINER_REGISTRY` 是 DevKit 用來拉取相同映像檔的位址。

在 Dockerfile 中定義應用程式，然後從 SDK 內建置並推送 ARM64 映像檔：

```bash
docker buildx build \
  --platform linux/arm64 \
  --tag "${SIMA_CONTAINER_REGISTRY}/hello-neat:develop" \
  --push \
  .
```

使用 `dk container deploy` 從同一登錄庫下載映像檔，並在已連接的 DevKit 上執行：

```bash
dk container deploy hello-neat:develop \
  --detach \
  --name hello-neat \
  --network host
```

使用簡短的映像檔名稱即可。`dk` 會使用 sima-cli 設定的 DevKit 登錄庫位址。若要先不明確更新映像檔就直接啟動，請使用 `dk container run hello-neat:develop ...`。

登錄庫會在本機開發網路上使用 HTTP。sima-cli 會更新 DevKit 的 Docker 設定，並在此設定變更時重新啟動 Docker 一次。登錄庫只會公開在 SDK 使用的迴路介面和面向 DevKit 的網路路徑上，不會公開在主機的所有介面上。

SDK 設定不會在 DevKit 上安裝 Docker。若未安裝 Docker，設定仍會正常完成，並說明第一個 `dk container` 命令會提示安裝及設定。如此一來，對不使用容器部署的使用者而言，Docker 仍完全是選用項目。在 DevKit 上安裝需要明確核准、免密碼的 `sudo`，以及可透過網際網路存取 Docker 的 Debian 軟體庫。

使用 `--no-container-registry` 可略過此步驟。這不會停止或移除現有的登錄庫。若要選擇其他連接埠，請使用 `--container-registry-port <port>` 重新執行設定。變更連接埠時，已儲存的映像檔會保留。

## 選擇啟用 Edgematic Studio

預設設定不會顯示 Edgematic Studio 提示、不會安裝 Studio，也不會公開其連接埠。這也適用於 `-y` 與 `--noninteractive`。

使用 `--edgematic-studio` 安裝 Studio 並公開其連接埠。若只要公開連接埠以供之後手動安裝，請使用 `--edgematic-studio-port`。

## 選擇瀏覽器版 VS Code 擴充功能

互動式設定提供 Neat、Codex 與 Claude 擴充功能。以 Space 選取，按 Enter 確認。初始皆未勾選；不選任何項目就會略過安裝，既有但未選取的擴充功能仍會保留。

自動化時，可不經檢查清單直接安裝三項擴充功能：

```bash
sima-cli sdk setup --noninteractive --all-extensions
```

`--all-extensions` 只略過擴充功能選取。使用 `--noninteractive` 略過其他問題。重複使用既有 SDK 容器時也會安裝擴充功能。未指定此選項時，除非舊版 `SIMA_CLI_INSTALL_CODEX_EXTENSION` 設定要求安裝，否則 `-y` 與 `--noninteractive` 會略過選用擴充功能。`--all-extensions` 不能與 `--minimal` 合併使用，也不會啟用 Edgematic Studio 或改變 Model Compiler 選擇。

## 瀏覽器版 VS Code 擴充功能版本

sima-cli 安裝 Codex 與 Claude 的確切版本，並固定版本以防自動更新。重新執行設定時會替換不同版本；相符版本則保留並固定，不會重新下載。SDK 映像可在 `/etc/sima-neat/vscode-extensions.json` 宣告已驗證版本：

```json
{
  "schema_version": 1,
  "extensions": {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266"
  }
}
```

資訊清單本身不代表使用者選擇安裝。結構描述版本 1 要求兩個 ID 都指定確切版本。沒有資訊清單的舊映像會使用 Codex `26.5825.51511` 與 Claude `2.1.266` 作為相容性備援版本。無法讀取或無效的資訊清單會回報錯誤並略過擴充功能安裝，但其餘設定會繼續。Codex 或 Claude 安裝命令失敗時，會使用相同確切版本與正常 TLS 驗證重試最多三次。

環境變數覆寫設定優先：

| 變數 | 行為 |
| --- | --- |
| `SIMA_CLI_CODEX_EXTENSION_ID` | 以 `publisher.extension@exact-version` 覆寫 Codex；空值會停用，除非使用 `--all-extensions`。 |
| `SIMA_CLI_CLAUDE_EXTENSION_ID` | 以 `publisher.extension@exact-version` 覆寫 Claude；空值會停用，除非使用 `--all-extensions`。 |
| `SIMA_CLI_INSTALL_CODEX_EXTENSION` | 不經檢查清單，自動選取擴充功能安裝。 |

未附版本的內建擴充功能 ID 使用 SDK／預設固定版本。其他 ID 必須指定版本。`--minimal` 會略過選用擴充功能安裝。設定會驗證已安裝版本、寫入每個選定擴充功能的 `metadata.pinned` 旗標，並重新啟動瀏覽器版 VS Code；之後請重新載入已開啟的瀏覽器分頁。

## Model Compiler 安裝來源

設定只會依 SDK 容器架構，在目前工作目錄尋找 `model-compiler-arm64.zip` 或 `model-compiler-amd64.zip`。不會搜尋解壓縮後的資料夾、父目錄、工作區 ZIP 或另一種架構。

找到有效本機 ZIP 時，設定提供本機、線上或略過選項。沒有有效 ZIP 時，提供線上或略過，按 Enter 的預設選擇為略過。

| 旗標 | 有本機 ZIP | 無有效本機 ZIP |
| --- | --- | --- |
| `--noninteractive` | 本機安裝 | 略過 |
| `--noninteractive -y` | 本機安裝 | 線上安裝 |
| `-y` | 本機安裝 | 線上安裝 |

無人操作的線上安裝必須使用 `-y`。舊版 SDK 來源請在主機上使用 `sima-cli login` 驗證。`--minimal`、`--no-model-compiler` 與 `--no-model-sdk` 一律略過安裝。

官方 ZIP 必須在根目錄包含 `install_modelsdk_wheels.sh`、`source.json`、`manifest.txt` 與套件內容，且必須符合 SDK 選定的編譯器版本。封存檔會解壓縮至主機暫存空間，再複製到 Linux 容器暫存空間，因此請為兩份內容預留空間。成功或失敗後會移除暫存資料，保留原始 ZIP。本機安裝失敗絕不會改用線上來源。系統套件與 Python 必要元件仍可能需要網路存取。
