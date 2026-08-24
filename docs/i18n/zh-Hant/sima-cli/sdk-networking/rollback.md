# 將 SDK 網路變更還原。

當您想要檢查或撤銷 SDK 設定或網路修復所做的 Linux 主機網路變更時，請使用「還原」功能。

「還原」功能會盡力執行。它會移除 `sima-cli` 可以識別的、針對偵測到的 DevKit/shared-network 路徑所設定的規則。它不會重設與之無關的主機網路、VPN 設定、Docker 安裝狀態，或使用者管理的防火牆規則。

## 預覽回滾動作

除非提供 `--apply`，否則回滾作業將以模擬模式執行：

```bash
sima-cli sdk network rollback --devkit <devkit-ip>
```

在套用變更之前，請先檢視表格。

## 套用回滾

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

這會移除 `sima-cli` 為 SDK 橋接和 DevKit 共享網路路徑所新增的相符的執行階段轉發/NAT 規則。

## 移除永久設定檔。

如果已安裝一個持續存在的 NetworkManager 派遣器設定檔，請在移除它之前先確認是否要回滾。當 NetworkManager 重新建立一個共用連線時，該設定檔會重新應用 SDK 橋接轉送設定。

若要以互動方式移除它：

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

若要以非互動方式移除它：

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply --remove-persistent-profile
```

如果您想完全撤銷 SDK 網路修復，刪除永久設定檔是安全的。如果您繼續使用相同的 Ubuntu 共用網路 DevKit 連線，您可能需要在稍後再次執行修復。

## 回滾之後

執行診斷程式以確認目前狀態：

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

如果您想從頭開始重新建立 SDK 的網路設定，請執行：

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

對於需要持續進行共用網路修復的自動化作業，請使用：

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```
