# 股市情勢

台股大盤情勢燈號、個股趨勢判斷、強弱勢排行。純靜態網頁，資料由 GitHub Actions 每個交易日盤後從證交所與櫃買中心累積。

## 第一次設定

1. **開啟 GitHub Pages**：Settings → Pages → Build and deployment → Source 選「Deploy from a branch」，Branch 選 `main`、資料夾 `/ (root)`，Save。
2. **跑第一次資料**：Actions → 左邊選「收盤價歷史」→ Run workflow。第一次會往回補 120 個交易日，大約十幾分鐘；跑完會自動把 `history.json` 提交進 repo。
3. 之後每個交易日台北 15:05 與 17:40 會自動更新，不用再管。

網址是 `https://<帳號>.github.io/<repo 名稱>/`。

## 檔案

- `index.html`：整個網站，沒有外部函式庫。
- `scripts/fetch_history.py`：抓上市（證交所 MI_INDEX）與上櫃（櫃買日收盤行情）每日全市場收盤價與成交量，保留 120 個交易日寫進 `history.json`。
- `.github/workflows/history.yml`：排程。

## 判斷怎麼算

大盤與個股用同一套機械式計分（-7 ～ +7），逐條列在頁面上：均線排列（±2）、月線方向、近 20 日動能、RSI（14）、MACD 柱、60 日區間位置各 ±1。大盤再加上站上月線／季線的個股比例與新高新低家數。只是幫你看清楚位置，不是投資建議。
