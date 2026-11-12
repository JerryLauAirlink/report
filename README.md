# Airlink 表格（同一個網址）

入首頁先選擇地區（香港 / 台灣 / 新加坡），再選擇 Service Report 或 Cert of Job Completion，然後填寫表單並請客戶簽名。

同事只需要記一個網址。

## 同事快速開始

```powershell
git clone https://github.com/JerryLauAirlink/field-report-pwa.git
cd field-report-pwa
python server.py
```

- 電腦開啟：http://localhost:8765/index.html
- 手機（同一 Wi‑Fi）：`http://你的電腦IP:8765/index.html`
- Webhook 預設：`/upload`（PDF 存入本機 `REPORT/` 資料夾）

## 管理員

- 全公司預設：編輯 `deploy-config.json`（改完請增加 `configRevision`）
- Dropbox 直傳：複製 `server-config.example.json` → `server-config.json` 填入 Token
- **切勿** commit `server-config.json`（含密鑰）

## 需要

- Python 3.10+
- 瀏覽器 Chrome / Safari / Edge
