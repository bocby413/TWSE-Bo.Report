# -*- coding: utf-8 -*-
"""每個交易日盤後把上市＋上櫃「全部股票」的收盤價與成交量累積進 history.json，
給 index.html（股市情勢）算均線、RSI、MACD 跟判斷趨勢用。

證交所與櫃買的 API 沒開跨網域，網頁抓不到，所以在 GitHub Actions 先抓好放進 repo。
這支抓的是「某一天」的整個市場，所以可以往回補：history.json 不存在或有缺的日子，就一天一天補回來，
第一次跑大概要十幾分鐘（證交所有限速，每次請求之間要停一下）。

檔案格式（全部陣列都對齊 dates，那天沒成交就是 null）：
  {"dates":["2026-03-20",…], "updated":"…", "keep":120,
   "skip":["2026-04-03",…],                     ← 查過確定不是交易日的日子，下次不再問
   "idx":{"TAIEX":[…]},                          ← 發行量加權股價指數
   "q":{"2330":{"n":"台積電","m":"twse","c":[收盤…],"v":[成交張數…]}, …}}
"""
import json, re, ssl, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
KEEP = 120              # 每檔保留幾個交易日。季線（MA60）要 60 天，再多留一倍畫圖用
LOOKBACK = 200          # 往回補最多幾個「日曆日」，120 個交易日大約是 170 個日曆日
MAX_DATES_PER_RUN = 150 # 一次最多補幾天，免得哪天卡住跑不完
TWSE_GAP = 3.5          # 證交所限速大約 5 秒 3 次，保守一點
TPEX_GAP = 1.5

UA = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.twse.com.tw/',
}

# 櫃買中心 2026-09 換的新憑證少送中間那張「TWCA SSL Certification Authority」，
# 瀏覽器會自己補抓、Python 不會，這裡把它放進信任清單讓鏈接得起來。驗證照做，不是關掉驗證。
# 來源：http://sslserver.twca.com.tw/cacert/Cyber_SSL_2023.crt，有效到 2033-02-23
TWCA_SSL_CA = """
-----BEGIN CERTIFICATE-----
MIIG1DCCBLygAwIBAgIQQAE0sE8AAAAAAAAAA+MkrDANBgkqhkiG9w0BAQwFADBQ
MQswCQYDVQQGEwJUVzESMBAGA1UEChMJVEFJV0FOLUNBMRAwDgYDVQQLEwdSb290
IENBMRswGQYDVQQDExJUV0NBIENZQkVSIFJvb3QgQ0EwHhcNMjMwMjIzMDcyMjI0
WhcNMzMwMjIzMTU1OTU5WjBhMQswCQYDVQQGEwJUVzESMBAGA1UEChMJVEFJV0FO
LUNBMRMwEQYDVQQLEwpTU0wgU3ViLUNBMSkwJwYDVQQDEyBUV0NBIFNTTCBDZXJ0
aWZpY2F0aW9uIEF1dGhvcml0eTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoC
ggIBAMquxiSlMrxfOO29yqxCo/BIYBswnE7snZnuZDPcx8N9WhOdNGDsF024VjXK
nXoVaZBcv56eFsU+w9Mcq+uIVYzjVrBoe5u8ZLE0hPSkluH8URhcxtSQJ+gXcB0L
JHsseAeXVcgqoxTSJ6/n0xTCeXEnGwSRAzrqTvjS2gbd3TILxsfIHwRgwwPjBDgm
tjzbHHOFTJB3GCtH65T9A0viM2B/IW9Wz73jkz02AVMrZBHQ67IJ2W9CoIjd5mdG
eIV36U9NXl+wZa/D90pLRsFVbItKgLXgF71CQ92vS/biTx8fA6UUCU2ToNczP5Ur
A/mDXCBCLakwa1I3ylRkgFwluJw9DqiYh56MRgsEABa+ZPrm1Qb9njQZK4Y4V+ML
IvGM3xVoHIlvaSN29ubTueLpTeuAwN2VTiRzfOyCRKTcMBCtlMw1WCJNAMiNWDWS
BMnY9SlKv1oujmjS/ti0ptcipMymIoeWpVuQt3Mj8lYlKRpd6Zg8MbljMwQRSClK
6O6MSwpM3Xy5uJGh2cY5oYmKtxfyHSuKtKsk+daAPV1lpWYp9bNrbsLUPwmSY+zk
VgkZdiWBF//RP/72/esANONINy5hkWjkVd0NLjA5TAgk+DVVmnPtQIj1vBgtk8ak
Y9CczIbEgKBonkHWn+GX2ycR6jadg2P+xrBFg4MGjomkb2gtAgMBAAGjggGXMIIB
kzAfBgNVHSMEGDAWgBSdhWEUfMFib5do5E83QOGt4A1WNzAdBgNVHQ4EFgQU8ijU
+dQcfhprFoLl75Mpae3KFSAwDgYDVR0PAQH/BAQDAgEGMBMGA1UdJQQMMAoGCCsG
AQUFBwMBMEoGA1UdIARDMEEwNQYLKwYBBAGCvyUBARUwJjAkBggrBgEFBQcCARYY
aHR0cHM6Ly93d3cudHdjYS5jb20udHcvMAgGBmeBDAECAjBNBgNVHR8ERjBEMEKg
QKA+hjxodHRwOi8vUm9vdENBLnR3Y2EuY29tLnR3L1RXQ0FSQ0EvY3liZXJfcm9v
dF9yZXZva2VfMjAyMi5jcmwwEgYDVR0TAQH/BAgwBgEB/wIBADB9BggrBgEFBQcB
AQRxMG8wQwYIKwYBBQUHMAKGN2h0dHA6Ly9zc2xzZXJ2ZXIudHdjYS5jb20udHcv
Y2FjZXJ0L2N5YmVyX3Jvb3RfMjAyMi5jcnQwKAYIKwYBBQUHMAGGHGh0dHA6Ly9y
b290b2NzcC50d2NhLmNvbS50dy8wDQYJKoZIhvcNAQEMBQADggIBAIFF/6Gnvu8L
3xQDIampB8QVgoKS2bcjte0uJBbCrQHpzcGTuVTkZaiA86LwVz6SAU7TVgVYRXmt
x8l29WzfKI6wOAzmvlGZxSYAdN0I6YBkJK1nmDs0+TSw5lCzb+UOpajNOaMdJ5SN
YTN87yRwl82AFrwUmSLaMV4tN7W49N0SsELWs/d4uNHSMM0mBjd0hLDIWJFwOkuD
yOWahnCVfPlCwSVWpUntOGgOHOA02IUE+JNX+spIV1SwAMYaEVyHe316YUgiGA5y
k3liTa3vuv06eE1J2yiWrs9booW2VTHD+amzucFFNN1KvSLjSbYxG1t/FclHEN/y
6hGM3bkjRC31A0jzpv93D3MUQTdJascicPa0H4i8hviRriyetaC6HC4q8FQUTo2A
cEpxicNGgyHhDV+YdbnS6GZL+f3bsmMM8ZFYZ77mDTS9mRO1VnIwkjiN4vpzh67a
KTpoD9TQzZcGQiJy6Pi+PCSFiqjK7UD/63L/Pt0hpoNKvZLrz4ngrlpyzpx8KjeS
A5cjKcc6vlHm0Kk07k5djhJsaqQELso5r+UXi9qC+nwqPuR/w5kJZv4fz0ND4UhY
5y3qd+iCikkF3WzOzey7jUH9URKb3iZnRAHZvmyLK57UI0FwP+5xZEByvwXDtxbe
914Hj3cSUrmKT3g/ZlOQQ1THeu48MA79
-----END CERTIFICATE-----
"""
CTX = ssl.create_default_context()
try:
    import certifi
    CTX.load_verify_locations(cafile=certifi.where())
except Exception:
    pass
CTX.load_verify_locations(cadata=TWCA_SSL_CA)


class NetError(Exception):
    """連不上或一直回非 JSON。跟「那天沒資料」是兩回事，所以分開"""


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                raw = r.read().decode('utf-8', 'replace')
            try:
                return json.loads(raw)
            except Exception:
                last = '不是 JSON：' + raw[:120].replace('\n', ' ')
                print('  第 %d 次%s' % (i + 1, last), flush=True)
        except Exception as e:
            last = str(e)
            print('  第 %d 次失敗：%s' % (i + 1, e), flush=True)
        time.sleep(5 * (i + 1))
    raise NetError(last or url)


def num(x):
    if x is None:
        return None
    s = str(x).replace(',', '').replace('+', '').strip()
    if s in ('', '--', '---', '-', 'X', '除權', '除息', '除權息'):
        return None
    try:
        return float(s)
    except Exception:
        return None


def clean(v, nd=2):
    """1085.0 寫成 1085，省一點空間；小數留兩位"""
    if v is None:
        return None
    v = round(v, nd)
    return int(v) if v == int(v) else v


CODE_RE = re.compile(r'^[0-9A-Z]{4,7}$')


def tables_of(d):
    """證交所新版回 tables=[{fields,data}]，舊版是 fields1/data1、fields2/data2…；
    櫃買新版也是 tables。都轉成 [(fields, data)] 一種形狀"""
    out = []
    if not isinstance(d, dict):
        return out
    if isinstance(d.get('tables'), list):
        for t in d['tables']:
            if isinstance(t, dict) and isinstance(t.get('data'), list):
                out.append((t.get('fields') or [], t['data']))
    for i in range(1, 15):
        if isinstance(d.get('data%d' % i), list):
            out.append((d.get('fields%d' % i) or [], d['data%d' % i]))
    if isinstance(d.get('fields'), list) and isinstance(d.get('data'), list):
        out.append((d['fields'], d['data']))
    return out


def col(fields, *names):
    """找欄位在第幾格。名稱有時會多個空白或括號，用「包含」比對"""
    for i, f in enumerate(fields):
        f = str(f).replace(' ', '')
        for n in names:
            if f == n:
                return i
    for i, f in enumerate(fields):
        f = str(f).replace(' ', '')
        for n in names:
            if f.startswith(n):
                return i
    return -1


# ── 上市：MI_INDEX 一次回那天全部股票（type=ALLBUT0999 是「全部不含權證」）──
TWSE_URLS = [
    'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=%s&type=ALLBUT0999&response=json',
    'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=%s&type=ALLBUT0999',
]


def parse_twse(d):
    """回 (stocks, taiex, has_data)。stocks = {code: (name, close, vol_shares)}"""
    if not isinstance(d, dict):
        return {}, None, False
    stat = str(d.get('stat') or '')
    if stat and stat.upper() != 'OK':
        return {}, None, False               # 「很抱歉, 沒有符合條件的資料!」＝ 不是交易日
    stocks, taiex = {}, None
    for fields, data in tables_of(d):
        ic, inm, icl, iv = (col(fields, '證券代號'), col(fields, '證券名稱'),
                            col(fields, '收盤價'), col(fields, '成交股數'))
        if ic >= 0 and icl >= 0:
            for row in data:
                if not isinstance(row, list) or len(row) <= max(ic, icl):
                    continue
                code = str(row[ic]).strip()
                if not CODE_RE.match(code):
                    continue
                close = num(row[icl])
                name = str(row[inm]).strip() if inm >= 0 and inm < len(row) else ''
                vol = num(row[iv]) if 0 <= iv < len(row) else None
                stocks[code] = (name, close, vol)
            continue
        ii, ix = col(fields, '指數'), col(fields, '收盤指數')
        if ii >= 0 and ix >= 0 and taiex is None:
            for row in data:
                if isinstance(row, list) and len(row) > max(ii, ix) and \
                        str(row[ii]).replace(' ', '') == '發行量加權股價指數':
                    taiex = num(row[ix])
                    break
    return stocks, taiex, bool(stocks)


# ── 上櫃：新版網站的日收盤行情，舊版兩個當備援 ──
def tpex_urls(day):
    roc = '%d/%s/%s' % (day.year - 1911, day.strftime('%m'), day.strftime('%d'))
    ad = day.strftime('%Y/%m/%d')
    return [
        'https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=%s&type=EW&id=&response=json' % ad,
        'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=%s&type=EW&response=json' % ad,
        'https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php'
        '?l=zh-tw&d=%s&se=EW&o=json' % roc,
        'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php'
        '?l=zh-tw&d=%s&se=EW&o=json' % roc,
    ]


def parse_tpex(d):
    """回 {code: (name, close, vol_shares)}。新版有 fields 就照名字找，
    舊版 aaData 沒欄名，固定是 代號,名稱,收盤,漲跌,開盤,最高,最低,成交股數…"""
    stocks = {}
    if not isinstance(d, dict):
        return stocks
    groups = tables_of(d)
    if isinstance(d.get('aaData'), list):
        groups.append(([], d['aaData']))
    for fields, data in groups:
        if fields:
            ic, inm, icl, iv = (col(fields, '代號', '證券代號'), col(fields, '名稱', '證券名稱'),
                                col(fields, '收盤', '收盤價'), col(fields, '成交股數', '成交量'))
            if ic < 0 or icl < 0:
                continue
        else:
            ic, inm, icl, iv = 0, 1, 2, 7
        for row in data:
            if not isinstance(row, list) or len(row) <= max(ic, icl):
                continue
            code = str(row[ic]).strip()
            if not CODE_RE.match(code):
                continue
            name = str(row[inm]).strip() if 0 <= inm < len(row) else ''
            vol = num(row[iv]) if 0 <= iv < len(row) else None
            stocks[code] = (name, num(row[icl]), vol)
    return stocks


def fetch_day(day):
    """抓某一天。回 None 表示那天沒資料（假日），否則回 (stocks, taiex)。
    連線問題丟 NetError，交給外面決定要不要放棄"""
    ymd = day.strftime('%Y%m%d')
    twse, taiex, has = {}, None, False
    err = None
    for u in TWSE_URLS:
        try:
            twse, taiex, has = parse_twse(get(u % ymd, tries=2))
            err = None
            break
        except NetError as e:
            err = e
            time.sleep(TWSE_GAP)
    if err:
        raise err
    if not has:
        return None
    time.sleep(TPEX_GAP)
    tpex, tpex_ok = {}, False
    for u in tpex_urls(day):
        try:
            tpex = parse_tpex(get(u, tries=1))
            tpex_ok = True
            if len(tpex) >= 300:
                break
        except NetError:
            pass
        time.sleep(TPEX_GAP)
    if not tpex_ok:
        raise NetError('上櫃 %s 每個來源都連不上' % ymd)
    stocks = {}
    for c, (n, close, vol) in twse.items():
        stocks[c] = {'n': n, 'm': 'twse', 'c': close, 'v': vol}
    for c, (n, close, vol) in tpex.items():
        stocks[c] = {'n': n, 'm': 'tpex', 'c': close, 'v': vol}
    print('  %s 上市 %d、上櫃 %d，加權指數 %s' % (day, len(twse), len(tpex), taiex), flush=True)
    return stocks, taiex, len(tpex) > 0


def main():
    old = {}
    try:
        with open('history.json', encoding='utf-8') as f:
            old = json.load(f)
    except Exception:
        pass
    dates = list(old.get('dates') or [])
    skip = set(old.get('skip') or [])
    q = old.get('q') or {}
    idx = (old.get('idx') or {}).get('TAIEX') or []
    idx = list(idx) + [None] * (len(dates) - len(idx))

    # 先把舊檔攤回「每天一份」，補完再重新排版
    days = {}                                    # date -> {code: (close, vol)}
    taiex = {}                                   # date -> close
    meta = {}                                    # code -> (name, market)
    for j, d in enumerate(dates):
        days[d] = {}
        taiex[d] = idx[j]
    for code, s in q.items():
        meta[code] = (s.get('n', ''), s.get('m', ''))
        cs, vs = s.get('c') or [], s.get('v') or []
        for j, d in enumerate(dates):
            c = cs[j] if j < len(cs) else None
            if c is not None:
                days[d][code] = (c, vs[j] if j < len(vs) else None)

    now = datetime.now(TPE)
    today = now.date()
    # 盤後大概 14:30 才有資料。太早問到的是「沒資料」，那不能當成假日記起來
    end = today if now.hour * 60 + now.minute >= 14 * 60 + 30 else today - timedelta(days=1)
    start = today - timedelta(days=LOOKBACK)
    todo = []
    day = start
    while day <= end:
        s = day.isoformat()
        if day.weekday() < 5 and s not in days and s not in skip:
            todo.append(day)
        day += timedelta(days=1)
    todo = todo[-MAX_DATES_PER_RUN:]
    print('已有 %d 個交易日，要補 %d 天' % (len(days), len(todo)), flush=True)

    got, stopped = 0, None
    for day in todo:
        s = day.isoformat()
        try:
            r = fetch_day(day)
        except NetError as e:
            stopped = '%s：%s' % (s, e)
            break
        if r is None:
            if day < today:
                skip.add(s)                      # 過去的日子沒資料就是假日，記起來
            else:
                print('  %s 還沒有資料，下次再抓' % s, flush=True)
            time.sleep(TWSE_GAP)
            continue
        stocks, tx, tpex_has = r
        if day >= today and not tpex_has:
            print('  上櫃還沒出來，今天先不記', flush=True)
            time.sleep(TWSE_GAP)
            continue
        days[s] = {c: (v['c'], v['v']) for c, v in stocks.items() if v['c'] is not None}
        taiex[s] = tx
        for c, v in stocks.items():
            if v['n'] or c not in meta:
                meta[c] = (v['n'], v['m'])
        got += 1
        time.sleep(TWSE_GAP)

    if stopped:
        print('::warning::補到一半連不上，先寫入已經拿到的：%s' % stopped, flush=True)
    if not got and not stopped and not todo:
        print('沒有新的交易日', flush=True)

    # 重新排版：只留最後 KEEP 個交易日，每檔對齊 dates
    dates = sorted(days)[-KEEP:]
    skip = sorted(d for d in skip if d >= (today - timedelta(days=LOOKBACK + 30)).isoformat())
    out_q = {}
    for code, (name, market) in meta.items():
        cs, vs, seen = [], [], False
        for d in dates:
            v = days[d].get(code)
            if v is None:
                cs.append(None)
                vs.append(None)
            else:
                seen = True
                cs.append(clean(v[0]))
                vs.append(int(v[1] // 1000) if v[1] is not None else None)   # 股 → 張
        if seen:
            out_q[code] = {'n': name, 'm': market, 'c': cs, 'v': vs}

    out = {'dates': dates, 'updated': now.strftime('%Y-%m-%d %H:%M'), 'keep': KEEP,
           'count': len(out_q), 'skip': skip,
           'idx': {'TAIEX': [clean(taiex.get(d)) for d in dates]},
           'q': out_q}
    if len(out_q) < 800 and old.get('q'):
        print('::warning::這次只有 %d 檔，怪怪的，不覆蓋舊檔' % len(out_q), flush=True)
        return
    with open('history.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('寫入 history.json：%d 個交易日（%s ～ %s），%d 檔，這次新增 %d 天'
          % (len(dates), dates[0] if dates else '-', dates[-1] if dates else '-', len(out_q), got))


if __name__ == '__main__':
    main()
