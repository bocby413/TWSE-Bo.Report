# -*- coding: utf-8 -*-
"""每個交易日盤後把上市＋上櫃「全部股票」的資料累積進 repo，給 index.html 判斷趨勢用。

證交所與櫃買的 API 沒開跨網域，網頁抓不到，所以在 GitHub Actions 先抓好放進 repo。
每個來源抓的都是「某一天的整個市場」，所以可以往回補：缺哪天就補哪天。
第一次跑要好幾十分鐘（證交所有限速），跑不完的下次接著補，寫檔是增量的。

抓的東西：
  每日行情      開高低收、成交張數            證交所 MI_INDEX／櫃買 日收盤行情
  三大法人      外資、投信、自營商買賣超（張）  證交所 T86／櫃買 三大法人買賣明細
  融資融券      融資餘額、融券餘額（張）        證交所 MI_MARGN／櫃買 融資融券餘額
  基本面快照    本益比、殖利率、淨值比、月營收、每股盈餘   兩邊的 OpenAPI，一次一整份，每次跑累積
  大盤新聞      Google 新聞 RSS

寫出來的檔：
  history.json   全市場精簡版：每檔 120 天收盤與成交量、最近 20 天法人與融資融券、本益比殖利率、
                 用五年歷史算好的相似情境統計（ai），加權指數的開高低收。廣度、排行都算這個
  d/XXX.json     依代號前三碼分片的詳細版：開高低收、成交量、法人、融資融券都五年，基本面，
                 點到那檔才載入。開高低與法人另外用 from 標起始位置，前面不塞 null
  meta.json      給這支腳本自己看的：全部日期、哪天做過什麼、假日、加權指數全長
  news.json      大盤新聞
"""
import json, os, re, ssl, time, urllib.request, urllib.parse, html
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

TPE = timezone(timedelta(hours=8))
KEEP = 1250             # 每檔收盤與成交量保留幾個交易日，約五年，個股頁找相似情境要用
KEEP_K = 1250           # 開高低也留五年（一片分片幾百 KB，可以接受）
HIST_KEEP = 120         # 首頁用的精簡版 history.json 只留這麼多，檔案才不會太大
MIN_CODES = 800         # 少於這個檔數就當抓壞了，不覆蓋舊檔
CHIP_BACK = 1250        # 法人與融資也補五年（每天要多抓四支，第一次要好幾個小時，從最近的往回補）
LOOKBACK = 1900         # 往回補最多幾個「日曆日」，1250 個交易日大約是 1830 個日曆日
TIME_BUDGET = 40 * 60      # 抓資料最多幾秒，之後還要算回測、抓個股新聞、寫檔，留 20 分鐘給它們
                        # 回補五年要跑好幾次接力，一次不要太長，這樣每次的結果都能早點提交出去
TWSE_GAP = 3.5          # 證交所限速大約 5 秒 3 次，保守一點
TPEX_GAP = 1.5
T0 = time.time()

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


def fetch_raw(url, tries=3, timeout=60):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            last = str(e)
            print('  第 %d 次失敗：%s' % (i + 1, e), flush=True)
            if getattr(e, 'code', None) == 404:
                break                                  # 路徑不存在，再試也一樣
            if i + 1 < tries:
                time.sleep(5 * (i + 1))
    raise NetError(last or url)


def get(url, tries=3):
    last = None
    for i in range(tries):
        raw = fetch_raw(url, tries=1)
        try:
            return json.loads(raw)
        except Exception:
            last = '不是 JSON：' + raw[:120].replace('\n', ' ')
            print('  第 %d 次%s' % (i + 1, last), flush=True)
            time.sleep(5 * (i + 1))
    raise NetError(last or url)


def num(x):
    if x is None:
        return None
    s = str(x).replace(',', '').replace('+', '').strip()
    if s in ('', '--', '---', '-', 'X', '除權', '除息', '除權息', 'N/A', 'NA'):
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


def lots(v):
    """股 → 張，取整數（負的也照樣往零取）"""
    if v is None:
        return None
    return int(v // 1000) if v >= 0 else -int((-v) // 1000)


def tick():
    return time.time() - T0 > TIME_BUDGET


CODE_RE = re.compile(r'^[0-9A-Z]{4,7}$')
# 權證：證交所 0[3-8] 開頭六碼、櫃買 7[0-3] 開頭六碼（最後一碼可能是字母）。
# 櫃買的日行情就算選「全部」也會把權證一起給，七千多檔、每檔活幾個月，不擋掉檔案會爆
WARRANT_RE = re.compile(r'^(0[3-8]|7[0-3])\d{3}[0-9A-Z]$')


def is_code(code):
    return bool(CODE_RE.match(code)) and not WARRANT_RE.match(code)


def tables_of(d):
    """證交所新版回 tables=[{fields,data}]，舊版是 fields1/data1、fields2/data2…、
    單表的是 fields/data；櫃買新版也是 tables、舊版是 aaData。都轉成 [(fields, data)]"""
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
    if isinstance(d.get('aaData'), list):
        out.append(([], d['aaData']))
    return out


def norm(f):
    return str(f).replace(' ', '').replace('　', '')


def col(fields, *names):
    """找欄位在第幾格：先找完全一樣的，再找開頭一樣的"""
    fs = [norm(f) for f in fields]
    for n in names:
        if n in fs:
            return fs.index(n)
    for i, f in enumerate(fs):
        for n in names:
            if f.startswith(n):
                return i
    return -1


def col_has(fields, must, must_not=(), which=0):
    """找名稱「包含」這些字的欄位（which=-1 是最後一個）。法人那張表欄名很長又會改，用包含比對比較穩"""
    hits = []
    for i, f in enumerate(fields):
        f = norm(f)
        if all(m in f for m in must) and not any(m in f for m in must_not):
            hits.append(i)
    if not hits:
        return -1
    return hits[which]


def no_data(d):
    """證交所回 {"stat":"很抱歉, 沒有符合條件的資料!"} 就是那天不是交易日／還沒出來"""
    if not isinstance(d, dict):
        return True
    stat = str(d.get('stat') or '')
    return bool(stat) and stat.lower() != 'ok'


def roc(day):
    return '%d/%s/%s' % (day.year - 1911, day.strftime('%m'), day.strftime('%d'))


def row_get(row, i):
    return num(row[i]) if 0 <= i < len(row) else None


# ── 每日行情 ──
TWSE_Q = [
    'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=%s&type=ALLBUT0999&response=json',
    'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=%s&type=ALLBUT0999',
]


def parse_twse_quotes(d, extra=None):
    """回 (stocks, taiex)。stocks = {code: {n,o,h,l,c,v}}；v 是股數"""
    stocks, taiex = {}, None
    if no_data(d):
        return stocks, taiex
    for fields, data in tables_of(d):
        ic, icl = col(fields, '證券代號'), col(fields, '收盤價')
        if ic >= 0 and icl >= 0:
            inm, io, ih, il, iv = (col(fields, '證券名稱'), col(fields, '開盤價'), col(fields, '最高價'),
                                   col(fields, '最低價'), col(fields, '成交股數'))
            for row in data:
                if not isinstance(row, list) or len(row) <= max(ic, icl):
                    continue
                code = str(row[ic]).strip()
                if not is_code(code):
                    continue
                stocks[code] = {'n': str(row[inm]).strip() if 0 <= inm < len(row) else '',
                                'o': row_get(row, io), 'h': row_get(row, ih), 'l': row_get(row, il),
                                'c': row_get(row, icl), 'v': row_get(row, iv)}
            continue
        ii, ix = col(fields, '指數'), col(fields, '收盤指數')
        if ii >= 0 and ix >= 0 and taiex is None:
            for row in data:
                if isinstance(row, list) and len(row) > max(ii, ix) and norm(row[ii]) == '發行量加權股價指數':
                    taiex = num(row[ix])
                    break
    return stocks, taiex


def tpex_q_urls(day):
    ad = day.strftime('%Y/%m/%d')
    return [
        'https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=%s&type=EW&id=&response=json' % ad,
        'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=%s&type=EW&response=json' % ad,
        'https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php'
        '?l=zh-tw&d=%s&se=EW&o=json' % roc(day),
        'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php'
        '?l=zh-tw&d=%s&se=EW&o=json' % roc(day),
    ]


def parse_tpex_quotes(d, extra=None):
    """新版有 fields 就照名字找；舊版 aaData 固定是 代號,名稱,收盤,漲跌,開盤,最高,最低,成交股數…"""
    stocks = {}
    for fields, data in tables_of(d):
        if fields:
            ic, icl = col(fields, '代號', '證券代號'), col(fields, '收盤', '收盤價')
            if ic < 0 or icl < 0:
                continue
            inm, io, ih, il, iv = (col(fields, '名稱', '證券名稱'), col(fields, '開盤', '開盤價'),
                                   col(fields, '最高', '最高價'), col(fields, '最低', '最低價'),
                                   col(fields, '成交股數', '成交量'))
        else:
            ic, inm, icl, io, ih, il, iv = 0, 1, 2, 4, 5, 6, 7
        for row in data:
            if not isinstance(row, list) or len(row) <= max(ic, icl):
                continue
            code = str(row[ic]).strip()
            if not is_code(code):
                continue
            stocks[code] = {'n': str(row[inm]).strip() if 0 <= inm < len(row) else '',
                            'o': row_get(row, io), 'h': row_get(row, ih), 'l': row_get(row, il),
                            'c': row_get(row, icl), 'v': row_get(row, iv)}
    return stocks


# ── 加權指數的開高低收：一次一整個月 ──
TWSE_X = 'https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=%s&response=json'


def parse_taiex_hist(d):
    """回 {iso日期: (開, 高, 低, 收)}。日期是民國「115/09/01」"""
    out = {}
    if no_data(d):
        return out
    for fields, data in tables_of(d):
        i_d, i_o, i_h, i_l, i_c = (col(fields, '日期'), col(fields, '開盤指數'), col(fields, '最高指數'),
                                   col(fields, '最低指數'), col(fields, '收盤指數'))
        if min(i_d, i_o, i_h, i_l, i_c) < 0:
            continue
        for row in data:
            if not isinstance(row, list) or len(row) <= max(i_d, i_o, i_h, i_l, i_c):
                continue
            m = re.match(r'(\d{2,3})/(\d{2})/(\d{2})', str(row[i_d]).strip())
            if not m:
                continue
            iso = '%04d-%s-%s' % (int(m.group(1)) + 1911, m.group(2), m.group(3))
            out[iso] = (num(row[i_o]), num(row[i_h]), num(row[i_l]), num(row[i_c]))
    return out


# ── 三大法人 ──
TWSE_I = [
    'https://www.twse.com.tw/rwd/zh/fund/T86?date=%s&selectType=ALLBUT0999&response=json',
    'https://www.twse.com.tw/fund/T86?response=json&date=%s&selectType=ALLBUT0999',
]


def parse_insti(d, positional=None):
    """回 {code: (外資淨, 投信淨, 自營商淨)}，單位股。
    欄名兩邊都很長（「外陸資買賣超股數(不含外資自營商)」之類），用包含比對；
    舊版櫃買 aaData 沒欄名就用 positional 給的位置"""
    out = {}
    if no_data(d):
        return out
    for fields, data in tables_of(d):
        if fields:
            ic = col(fields, '證券代號', '代號')
            ifi = col_has(fields, ['外', '買賣超'], ['自營商'], 0)        # 外資及陸資(不含外資自營商)
            if ifi < 0:
                ifi = col_has(fields, ['外', '買賣超'], [], 0)
            iit = col_has(fields, ['投信', '買賣超'])
            # 自營商「合計」那欄：證交所排在自行／避險前面、櫃買排在後面，所以不能看順序，
            # 要挑名字裡沒有「自行」「避險」的那個
            idl = col_has(fields, ['自營商', '買賣超'], ['外', '自行', '避險'], 0)
            if ifi < 0:
                # 櫃買新版的欄名沒有群組名稱，只有「買進股數、賣出股數、買賣超股數」一組一組重複，
                # 群組順序是：外資及陸資(不含自營商)、外資自營商、外資及陸資合計、投信、
                # 自營商(自行)、自營商(避險)、自營商合計、三大法人合計。照順序取第 1、4、7 組
                nets = [i for i, f in enumerate(fields) if '買賣超' in norm(f)]
                if len(nets) >= 7:
                    ifi, iit, idl = nets[0], nets[3], nets[6]
                elif positional:
                    ic, ifi, iit, idl = positional
            if ic < 0 or ifi < 0:
                continue
        elif positional:
            ic, ifi, iit, idl = positional
        else:
            continue
        for row in data:
            if not isinstance(row, list) or len(row) <= max(ic, ifi):
                continue
            code = str(row[ic]).strip()
            if not is_code(code):
                continue
            out[code] = (row_get(row, ifi), row_get(row, iit), row_get(row, idl))
    return out


def tpex_i_urls(day):
    """櫃買新版網站的路徑我沒辦法在這裡實測，多列幾種寫法逐一試；
    全部都沒資料的時候 try_urls 會把回應的長相印進 log，看了再修"""
    ad = day.strftime('%Y/%m/%d')
    base = 'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade'
    return [
        (base + '?type=Daily&sect=EW&date=%s&response=json' % ad, (0, 4, 13, 22)),
        (base + '?type=Daily&sect=EW&date=%s&id=&response=json' % ad, (0, 4, 13, 22)),
        ('https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php'
         '?l=zh-tw&se=EW&t=D&d=%s&o=json' % roc(day), (0, 4, 13, 22)),
    ]


# ── 融資融券 ──
TWSE_M = [
    'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=%s&selectType=ALL&response=json',
    'https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=%s&selectType=ALL',
]


def parse_margin(d, positional=None):
    """回 {code: (融資餘額, 融券餘額)}，單位張。
    證交所那張表欄名是「今日餘額」出現兩次（前面融資、後面融券），用第幾次出現來分"""
    out = {}
    if no_data(d):
        return out
    for fields, data in tables_of(d):
        if fields:
            ic = col(fields, '股票代號', '代號', '證券代號')
            if ic < 0:
                continue
            fs = [norm(f) for f in fields]
            bal = [i for i, f in enumerate(fs) if '餘額' in f and '前' not in f]
            fin = [i for i in bal if '資' in fs[i] and '券' not in fs[i]]
            sht = [i for i in bal if '券' in fs[i] and '資' not in fs[i]]
            if fin and sht:
                img, ims = fin[0], sht[0]
            elif len(bal) >= 2:
                img, ims = bal[0], bal[1]
            else:
                continue
        elif positional:
            ic, img, ims = positional
        else:
            continue
        for row in data:
            if not isinstance(row, list) or len(row) <= max(ic, img, ims):
                continue
            code = str(row[ic]).strip()
            if not is_code(code):
                continue
            out[code] = (num(row[img]), num(row[ims]))
    return out


def tpex_m_urls(day):
    ad = day.strftime('%Y/%m/%d')
    return [
        ('https://www.tpex.org.tw/www/zh-tw/margin/balance?date=%s&response=json' % ad, None),
        ('https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php'
         '?l=zh-tw&d=%s&o=json' % roc(day), (0, 6, 14)),
    ]


def shape(d, n=400):
    """把回應的長相濃縮成一行：頂層鍵、每張表的欄名跟第一列。給 log 看的"""
    try:
        if isinstance(d, dict):
            parts = ['keys=%s' % list(d.keys())[:8]]
            for k in ('stat', 'date', 'message', 'msg'):
                if k in d:
                    parts.append('%s=%s' % (k, str(d[k])[:60]))
            for fields, data in tables_of(d)[:3]:
                parts.append('fields=%s first=%s n=%d' % (fields[:12], (data[0] if data else None), len(data)))
            return ' | '.join(parts)[:n]
        return ('%s %s' % (type(d).__name__, str(d)[:n]))
    except Exception as e:
        return 'shape 失敗 %s' % e


DIAG = {}                                          # 每種來源最多印幾次診斷


def try_urls(urls, parse, gap, min_rows=1, diag=None):
    """逐一試來源，回 (result, reached)。reached=False 表示每個都連不上。
    result 是 parse 的回傳（dict，或 (dict, 其他) 的 tuple）。
    diag 給個名字的話，全部來源都連得上卻沒資料時，把每個回應的長相印出來（每個名字最多兩次）"""
    reached = False
    best = None
    seen = []
    for item in urls:
        url, extra = item if isinstance(item, tuple) else (item, None)
        try:
            d = get(url, tries=1)
        except NetError:
            time.sleep(gap)
            continue
        reached = True
        r = parse(d, extra)
        rows = r[0] if isinstance(r, tuple) else r
        if len(rows) >= min_rows:
            return r, True
        seen.append((url, shape(d)))
        if best is None:
            best = r
        time.sleep(gap)
    if best is None:
        best = parse({}, None)
    if diag and reached and DIAG.get(diag, 0) < 2:
        DIAG[diag] = DIAG.get(diag, 0) + 1
        for url, sh in seen:
            print('  [診斷 %s] %s\n      → %s' % (diag, url, sh), flush=True)
    return best, reached


# ── 基本面快照：一次一整份，抓到什麼就更新什麼，抓不到就沿用舊的 ──
def fetch_fundamentals():
    f = {}                                     # code -> dict

    def put(code, **kw):
        if not code:
            return
        f.setdefault(code, {}).update({k: v for k, v in kw.items() if v is not None})

    # 本益比、殖利率、淨值比
    for url, kc, kpe, ky, kpb in [
        ('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL', 'Code', 'PEratio', 'DividendYield', 'PBratio'),
        ('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis', 'SecuritiesCompanyCode',
         'PriceEarningRatio', 'YieldRatio', 'PriceBookRatio'),
    ]:
        try:
            rows = get(url, tries=2)
            n = 0
            for r in rows if isinstance(rows, list) else []:
                put(str(r.get(kc) or '').strip(), pe=clean(num(r.get(kpe))), yld=clean(num(r.get(ky))),
                    pb=clean(num(r.get(kpb))))
                n += 1
            print('  本益比 %s：%d 檔' % (url.split('/')[2], n), flush=True)
        except NetError as e:
            print('  本益比抓不到 %s：%s' % (url, e), flush=True)

    # 月營收。累積成 {yyyymm: [當月營收(千元), 年增%, 月增%]}，跑久了就有一整年
    for url in ['https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O',
                'https://www.tpex.org.tw/openapi/v1/t187ap05_O']:
        try:
            rows = get(url, tries=2)
        except NetError as e:
            print('  月營收抓不到 %s：%s' % (url, e), flush=True)
            continue
        n = 0
        for r in rows if isinstance(rows, list) else []:
            code = str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or '').strip()
            ym = str(r.get('資料年月') or r.get('DataYearMonth') or '').strip().replace('/', '')
            rev = num(r.get('營業收入-當月營收') or r.get('CurrentMonthRevenue'))
            yoy = num(r.get('營業收入-去年同月增減(%)') or r.get('LastYearMonthRevenueChangePercent'))
            mom = num(r.get('營業收入-上月比較增減(%)') or r.get('LastMonthRevenueChangePercent'))
            if code and ym and rev is not None:
                put(code, rev={ym: [clean(rev, 0), clean(yoy, 1), clean(mom, 1)]})
                n += 1
        print('  月營收 %s：%d 檔' % (url.split('/')[-1], n), flush=True)
        if n and 'twse' not in url:
            break

    # 每股盈餘（累計）。綜合損益表依行業分好幾份，有基本每股盈餘欄的都收
    for url in ['https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci',
                'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi',
                'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd',
                'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_fh',
                'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ins',
                'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_basi',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_bd',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_fh',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ins',
                'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_mim']:
        try:
            rows = get(url, tries=1)
        except NetError:
            continue
        n = 0
        for r in rows if isinstance(rows, list) else []:
            code = str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or '').strip()
            y = str(r.get('年度') or r.get('Year') or '').strip()
            q = str(r.get('季別') or r.get('Season') or '').strip()
            eps = None
            for k, v in r.items():
                if '每股盈餘' in str(k) and '基本' in str(k):
                    eps = num(v)
                    break
            if code and y and q and eps is not None:
                put(code, eps={'%sQ%s' % (y, q): clean(eps, 2)})
                n += 1
        print('  EPS %s：%d 檔' % (url.split('/')[-1], n), flush=True)
    return f


def merge_fund(old, new):
    """新的蓋舊的，但 rev／eps 是字典要合併，才累積得起來"""
    out = dict(old or {})
    for k, v in (new or {}).items():
        if k in ('rev', 'eps'):
            d = dict(out.get(k) or {})
            d.update(v)
            keys = sorted(d)[-(13 if k == 'rev' else 8):]        # 只留最近 13 個月／8 季
            out[k] = {kk: d[kk] for kk in keys}
        else:
            out[k] = v
    return out


# ── 大盤新聞 ──
def fetch_news():
    items = []
    for q in ['台股 大盤', '台積電 外資', '聯準會 美股']:
        url = ('https://news.google.com/rss/search?q=%s&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
               % urllib.parse.quote(q))
        try:
            raw = fetch_raw(url, tries=2, timeout=30)
            root = ElementTree.fromstring(raw.encode('utf-8'))
            for it in list(root.iter('item'))[:12]:
                t = html.unescape(it.findtext('title') or '')
                link = it.findtext('link') or ''
                pub = it.findtext('pubDate') or ''
                src = it.findtext('source') or ''
                if t and link:
                    items.append({'t': t, 'u': link, 'd': pub, 's': src, 'q': q})
        except Exception as e:
            print('  新聞抓不到 %s：%s' % (q, e), flush=True)
    seen, out = set(), []
    for it in items:
        if it['t'] in seen:
            continue
        seen.add(it['t'])
        out.append(it)
    print('  新聞 %d 則' % len(out), flush=True)
    return out


# ── 個股相似情境（跟 index.html 的 ownAnalogs 同一套，改了要兩邊一起改） ──
# 把過去每一天的技術狀態算成一組數字，標準化後找跟今天最像的幾天，看它們之後 5／10／20 天怎麼走。
# 排行要對兩千多檔查，瀏覽器載不動每檔五年的資料，所以在這裡算好放進 history.json
HORIZ = (5, 10, 20)


def _ffill(a):
    out, last = [], None
    for v in a:
        if v is not None:
            last = v
        out.append(last)
    i = 0
    while i < len(out) and out[i] is None:
        i += 1
    return out[i:]


def _sma(a, n):
    out, s = [None] * len(a), 0.0
    for i, v in enumerate(a):
        s += v
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema(a, n):
    k, out, e = 2.0 / (n + 1), [], None
    for v in a:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _rsi_series(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    g /= n
    l /= n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def _macd_hist(c):
    e12, e26 = _ema(c, 12), _ema(c, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = _ema(dif, 9)
    return [a - b for a, b in zip(dif, dea)]


def _stats(arr):
    if len(arr) < 5:
        return None
    s = sorted(arr)
    n = len(s)
    q = lambda p: s[min(n - 1, int(p * n))]
    return [n, round(sum(s) / n * 100, 2), round(q(.5) * 100, 2), round(q(.25) * 100, 2), round(q(.75) * 100, 2),
            round(sum(1 for v in s if v > 0) / n * 100, 1)]


def _feature_rows(closes, vols):
    """回 (補值後的收盤 c, [(i, 特徵向量)…])。特徵：乖離、均線間距、月線斜率、RSI、MACD、區間位置、5／20 日動能、量能"""
    c = _ffill(closes)
    L = len(c) - 1
    if L < 60:
        return c, []
    ma5, ma20, ma60 = _sma(c, 5), _sma(c, 20), _sma(c, 60)
    rs, mh = _rsi_series(c, 14), _macd_hist(c)
    vr = None
    if vols and any(v for v in vols):
        off = len(vols) - len(c)
        v = [(vols[i + off] or 0) if 0 <= i + off < len(vols) else 0 for i in range(len(c))]
        v5, v20 = _sma(v, 5), _sma(v, 20)
        vr = [(v5[i] / v20[i] - 1) if v20[i] else None for i in range(len(c))]

    def feat(i):
        if i < 60 or ma60[i] is None or rs[i] is None or ma20[i - 5] is None:
            return None
        p = c[i]
        w = c[i - 59:i + 1]
        hi, lo = max(w), min(w)
        f = [(p - ma20[i]) / ma20[i], (ma5[i] - ma20[i]) / ma20[i], (ma20[i] - ma60[i]) / ma60[i],
             ma20[i] / ma20[i - 5] - 1, rs[i] / 100, mh[i] / p, (p - lo) / (hi - lo) if hi > lo else .5,
             p / c[i - 20] - 1, p / c[i - 5] - 1]
        if vr is not None:
            f.append(0 if vr[i] is None else vr[i])
        return f

    rows = [(i, feat(i)) for i in range(60, L + 1)]
    return c, [(i, f) for i, f in rows if f]


def _pick(cand, K):
    """cand 是 (i, 距離) 由近到遠；相鄰 3 天內只挑一個，避免同一段行情重複算"""
    picked = []
    for i, _ in cand:
        if all(abs(j - i) >= 3 for j in picked):
            picked.append(i)
        if len(picked) >= K:
            break
    return picked


def own_analogs(closes, vols):
    """回 {'n': 候選天數, 'k': 取幾天, '5': [n, 平均%, 中位%, p25%, p75%, 勝率%], '10': …, '20': …}，資料不夠回 None"""
    c, rows = _feature_rows(closes, vols)
    L = len(c) - 1
    cur = next((f for i, f in rows if i == L), None)
    if cur is None or len(rows) < 25:
        return None
    dim = len(cur)
    mean = [sum(f[k] for _, f in rows) / len(rows) for k in range(dim)]
    sd = [(sum((f[k] - mean[k]) ** 2 for _, f in rows) / len(rows)) ** .5 or 1 for k in range(dim)]
    dist = lambda f: sum(((f[k] - cur[k]) / sd[k]) ** 2 for k in range(dim)) ** .5
    cand = sorted(((i, dist(f)) for i, f in rows if i <= L - 5), key=lambda x: x[1])
    picked = _pick(cand, min(40, max(10, len(cand) // 4)))
    out = {'n': len(cand), 'k': len(picked)}
    for h in HORIZ:
        st = _stats([c[i + h] / c[i] - 1 for i in picked if i + h <= L])
        if st:
            out[str(h)] = st
    return out


BT_DAYS = 120            # 回測最近幾個交易日的預測


def backtest(closes, vols, highs, lows, dates):
    """把同一套「找相似日子」的方法放回過去每一天，只用那天以前的資料做預測，再跟實際走勢對。
    回 {'5': [次數, 方向命中%, 目標價達成%, 平均誤差%], '10': …, '20': …, 'last': [[日期, 預估10日%, 實際%, 有沒有到], …]}"""
    try:
        import numpy as np
    except ImportError:
        return None
    c, rows = _feature_rows(closes, vols)
    if len(rows) < 60:
        return None
    off = len(closes) - len(c)                         # c 的第 i 天是 dates 的第 i+off 天
    L = len(c) - 1
    I = np.array([i for i, _ in rows])
    F = np.array([f for _, f in rows], dtype=float)
    sd = F.std(axis=0)
    sd[sd == 0] = 1
    Z = F / sd
    pos = {int(i): k for k, i in enumerate(I)}
    hi = [(highs[i + off] if highs[i + off] is not None else c[i]) for i in range(len(c))]
    lo = [(lows[i + off] if lows[i + off] is not None else c[i]) for i in range(len(c))]
    res = {h: [] for h in HORIZ}
    last = []
    for t in range(max(60, L - BT_DAYS - 4), L - 4):    # t+5 ≤ L 才有答案可對
        k = pos.get(t)
        if k is None:
            continue
        m = int(np.searchsorted(I, t - 5, side='right'))    # 只用 t-5 以前的日子當候選（那天當下看得到的）
        if m < 25:
            continue
        d = np.sqrt(((Z[:m] - Z[k]) ** 2).sum(axis=1))
        order = np.argsort(d, kind='stable')
        picked = _pick([(int(I[j]), 0) for j in order], min(40, max(10, m // 4)))
        for h in HORIZ:
            if t + h > L:
                continue
            st = _stats([c[i + h] / c[i] - 1 for i in picked if i + h <= t])
            if not st:
                continue
            med = st[2] / 100
            act = c[t + h] / c[t] - 1
            tgt = c[t] * (1 + med)
            reached = (max(hi[t + 1:t + h + 1]) >= tgt) if med >= 0 else (min(lo[t + 1:t + h + 1]) <= tgt)
            res[h].append((med, act, reached))
            if h == 10:
                last.append([dates[t + off], round(med * 100, 2), round(act * 100, 2), 1 if reached else 0])
    out = {}
    for h in HORIZ:
        r = res[h]
        if len(r) < 10:
            continue
        n = len(r)
        out[str(h)] = [n, round(sum(1 for m_, a, _ in r if (m_ >= 0) == (a >= 0)) / n * 100, 1),
                       round(sum(1 for _, _, x in r if x) / n * 100, 1),
                       round(sum(abs(a - m_) for m_, a, _ in r) / n * 100, 2)]
    if '10' not in out:
        return None
    out['last'] = last[-10:]
    return out


# ── 個股新聞：Google News 近 7 天，用關鍵字粗分利多／利空 ──
POS_KW = ('創新高', '創高', '新高', '漲停', '大漲', '買超', '加碼', '調升', '上修', '上調', '成長', '轉盈', '獲利', '接單',
          '訂單', '擴產', '利多', '看好', '強勁', '大單', '庫藏股', '填息', '年增', '季增', '轉強', '突破', '衝高', '走高',
          '攻頂', '飆', '亮眼', '報喜', '搶進', '布局', '受惠')
NEG_KW = ('跌停', '大跌', '賣超', '減碼', '調降', '下修', '下調', '衰退', '虧損', '虧', '利空', '看壞', '疲弱', '裁員', '訴訟',
          '違約', '罰款', '處分', '警示', '注意股', '重挫', '走弱', '跌破', '下滑', '年減', '季減', '停工', '召回', '摔',
          '重摔', '爆量下跌', '崩', '砍', '減資', '掏空', '停牌', '打入全額交割')
NEWS_TOP = 200           # 成交值前幾名的股票抓個股新聞
NEWS_BUDGET = 240        # 個股新聞最多花幾秒
NWH_KEEP = 500           # 每檔留幾天的新聞分數歷史


def news_score(title):
    p = any(k in title for k in POS_KW)
    n = any(k in title for k in NEG_KW)
    return (1 if p else 0) - (1 if n else 0)


def fetch_stock_news(cands):
    """cands: [(代號, 名稱)…]。回 {代號: {'n': 則數, 'p': 利多, 'q': 利空, 'i': [[標題, 連結, 日期, 來源, 分數]…]}}"""
    t0 = time.time()
    out = {}
    for code, name in cands:
        if time.time() - t0 > NEWS_BUDGET:
            print('  個股新聞時間到，抓了 %d 檔' % len(out), flush=True)
            break
        q = '"%s" when:7d' % name
        url = ('https://news.google.com/rss/search?q=%s&hl=zh-TW&gl=TW&ceid=TW:zh-Hant' % urllib.parse.quote(q))
        try:
            raw = fetch_raw(url, tries=1, timeout=20)
            root = ElementTree.fromstring(raw.encode('utf-8'))
        except Exception as e:
            print('  個股新聞 %s 抓不到：%s' % (code, str(e)[:60]), flush=True)
            time.sleep(1)
            continue
        items = []
        for it in list(root.iter('item'))[:15]:
            t = html.unescape(it.findtext('title') or '')
            if not t or name not in t:                 # 標題裡要有這檔的名字，不然常常是別檔的新聞
                continue
            items.append([t, it.findtext('link') or '', it.findtext('pubDate') or '', it.findtext('source') or '', news_score(t)])
        if items:
            out[code] = {'n': len(items), 'p': sum(1 for x in items if x[4] > 0), 'q': sum(1 for x in items if x[4] < 0), 'i': items[:6]}
        time.sleep(.3)
    print('  個股新聞：%d 檔有新聞（%.0f 秒）' % (len(out), time.time() - t0), flush=True)
    return out


# ── 主流程 ──
KEYS = ('o', 'h', 'l', 'c', 'v', 'fi', 'it', 'dl', 'mg', 'ms')


def shard_of(code):
    """依代號前三碼分片（一片十幾檔）；00 開頭的 ETF 有三百多檔，切到四碼"""
    return code[:4] if code.startswith('00') else code[:3]


def load_store():
    """把 d/*.json 攤回 days[date][code] = {...}，dates／skip／done 從 history.json 拿"""
    meta = {}
    for fn in ('meta.json', 'history.json'):          # 新版的進度在 meta.json，舊版只有 history.json
        try:
            with open(fn, encoding='utf-8') as f:
                meta = json.load(f)
            break
        except Exception:
            pass
    dates = list(meta.get('dates') or [])
    days = {d: {} for d in dates}
    info = {}                                          # code -> {'n','m','f'}
    idx = meta.get('idx') or {}
    taiex = dict(zip(dates, idx.get('TAIEX') or []))
    taiex_ohl = {}                                     # date -> (o, h, l)
    for j, d in enumerate(dates):
        o = (idx.get('TAIEXo') or [None] * len(dates))[j]
        if o is not None:
            taiex_ohl[d] = (o, (idx.get('TAIEXh') or [])[j], (idx.get('TAIEXl') or [])[j])
    if os.path.isdir('d'):
        for fn in sorted(os.listdir('d')):
            if not fn.endswith('.json'):
                continue
            try:
                with open(os.path.join('d', fn), encoding='utf-8') as f:
                    sh = json.load(f)
            except Exception:
                continue
            sd = sh.get('dates') or []
            for code, s in (sh.get('q') or {}).items():
                info[code] = {'n': s.get('n', ''), 'm': s.get('m', ''), 'f': s.get('f') or {}, 'nwh': s.get('nwh') or []}
                # 新格式：o/h/l 放在 k 區塊、法人融資放在 x 區塊，各自有 from（在 dates 裡的起始位置）
                cols = {}
                for k in ('c', 'v'):
                    cols[k] = (0, s.get(k) or [])
                for blk, keys in (('k', ('o', 'h', 'l')), ('x', ('fi', 'it', 'dl', 'mg', 'ms'))):
                    b = s.get(blk)
                    for k in keys:
                        if isinstance(b, dict) and b.get(k):
                            cols[k] = (b.get('from', 0), b[k])
                        elif s.get(k):                        # 舊格式：跟 dates 一樣長
                            cols[k] = (0, s[k])
                for j, d in enumerate(sd):
                    if d not in days:
                        continue
                    row = {}
                    for k, (frm, arr) in cols.items():
                        jj = j - frm
                        if 0 <= jj < len(arr) and arr[jj] is not None:
                            row[k] = arr[jj]
                    if row:
                        days[d][code] = row
    # 舊版 history.json（沒有 d/ 的時候）只有收盤跟成交量，也接得起來
    if not info and meta.get('q'):
        for code, s in meta['q'].items():
            if not is_code(code):
                continue
            info[code] = {'n': s.get('n', ''), 'm': s.get('m', ''), 'f': {}}
            for j, d in enumerate(dates):
                c = (s.get('c') or [None] * len(dates))[j]
                if c is not None:
                    days[d][code] = {'c': c, 'v': (s.get('v') or [None] * len(dates))[j]}
    done = {d: set(v) for d, v in (meta.get('done') or {}).items()}
    for d in dates:
        done.setdefault(d, {'q'})
    # 櫃買的法人／融資各自有記號（iO、mO）。舊檔沒有這兩個記號，就看資料裡有沒有來補
    # 不能信檔案裡的記號：之前解析錯的時候，把「連上了但 0 檔」的日子也記成做過了
    for d in dates:
        tp = [r for c, r in days[d].items() if info.get(c, {}).get('m') == 'tpex']
        done[d].discard('iO'); done[d].discard('mO')
        # 要有一定數量才算做過：有少數幾檔會同時出現在證交所那份（例如剛轉上市的），
        # 只看「有沒有任何一檔」會被它們騙過去
        if sum(1 for r in tp if r.get('fi') is not None) >= 100:
            done[d].add('iO')
        if sum(1 for r in tp if r.get('mg') is not None) >= 100:
            done[d].add('mO')
    return days, info, taiex, taiex_ohl, set(meta.get('skip') or []), done


def kind_name(kind):
    return '法人' if kind == 'i' else '融資'


def main():
    days, info, taiex, taiex_ohl, skip, done = load_store()
    now = datetime.now(TPE)
    today = now.date()
    # 盤後大概 14:30 才有行情；法人 16:00 後、融資 17:30 後才出來。太早問到的「沒資料」不能記成假日
    end = today if now.hour * 60 + now.minute >= 14 * 60 + 30 else today - timedelta(days=1)
    chip_ok_today = now.hour * 60 + now.minute >= 17 * 60 + 35
    start = today - timedelta(days=LOOKBACK)
    if len(days) >= KEEP:
        # 已經滿 120 個交易日了，比最舊那天還早的不用再抓，抓了也會被切掉
        start = max(start, datetime.strptime(min(days), '%Y-%m-%d').date())

    # 1. 加權指數的開高低：哪個月有交易日還沒有開盤指數就抓那個月（一個月一支請求）。
    #    只有幾十支請求，放最前面，首頁的大盤 K 線最先補齊；行情補完後再跑一次，把新加的日子也補上
    stopped = None

    def fetch_index():
        nonlocal stopped
        trading = sorted(days)[-KEEP:]
        months = sorted({d[:7] for d in trading[-KEEP_K:] if d not in taiex_ohl})
        for ym in months:
            if stopped:
                break
            if tick():
                stopped = '時間到了，先寫檔，下次接著補'
                break
            try:
                got_x = parse_taiex_hist(get(TWSE_X % (ym.replace('-', '') + '01'), tries=2))
            except NetError as e:
                print('  加權指數 %s 抓不到：%s' % (ym, e), flush=True)
                time.sleep(TWSE_GAP)
                continue
            n = 0
            for d, (o, h, l, c) in got_x.items():
                if d in days and o is not None:
                    taiex_ohl[d] = (o, h, l)
                    if taiex.get(d) is None and c is not None:
                        taiex[d] = c
                    n += 1
            print('  加權指數 %s：%d 天開高低' % (ym, n), flush=True)
            time.sleep(TWSE_GAP)

    fetch_index()

    # 2. 行情：缺哪天補哪天（同時決定那天是不是交易日）。
    #    沒有開高低的日子（舊版檔案留下的）也重抓一次
    todo = []
    recent = set(sorted(days)[-KEEP_K:])               # 只有這些天需要開高低，更早的沒有也不用重抓
    day = start
    while day <= end:
        s = day.isoformat()
        if day.weekday() < 5 and s not in skip:
            have = days.get(s)
            if have is None or (s in recent and not any('o' in r for r in list(have.values())[:50])):
                todo.append(day)
        day += timedelta(days=1)
    print('已有 %d 個交易日，行情要補 %d 天' % (len(days), len(todo)), flush=True)
    got = 0
    for day in todo:
        if stopped:
            break
        if tick():
            stopped = '時間到了，先寫檔，下次接著補'
            break
        s = day.isoformat()
        (twse, tx), reached = try_urls([u % day.strftime('%Y%m%d') for u in TWSE_Q], parse_twse_quotes, TWSE_GAP, 300)
        if not reached:
            stopped = '%s 證交所連不上' % s
            break
        if not twse:
            if day < today and s not in days:
                skip.add(s)
            elif day >= today:
                print('  %s 行情還沒出來，下次再抓' % s, flush=True)
            time.sleep(TWSE_GAP)
            continue
        time.sleep(TPEX_GAP)
        tpex, reached2 = try_urls(tpex_q_urls(day), parse_tpex_quotes, TPEX_GAP, 300)
        if not reached2:
            stopped = '%s 櫃買連不上' % s
            break
        if day >= today and not tpex:
            print('  %s 上櫃還沒出來，今天先不記' % s, flush=True)
            time.sleep(TWSE_GAP)
            continue
        rows = days.get(s) or {}
        for mk, src in (('twse', twse), ('tpex', tpex)):
            for code, q in src.items():
                if q['c'] is None:
                    continue
                r = rows.setdefault(code, {})
                r.update({'o': q['o'], 'h': q['h'], 'l': q['l'], 'c': q['c'], 'v': lots(q['v'])})
                m = info.setdefault(code, {'f': {}})
                m['n'] = q['n'] or m.get('n', '')
                m['m'] = mk
        days[s] = rows
        taiex[s] = tx
        done.setdefault(s, set()).add('q')
        got += 1
        print('  %s 上市 %d、上櫃 %d，加權指數 %s' % (s, len(twse), len(tpex), tx), flush=True)
        time.sleep(TWSE_GAP)

    fetch_index()                                      # 剛補進來的交易日也要有指數開高低

    # 3. 法人與融資：最近 CHIP_BACK 個交易日裡還沒抓到的。
    trading = sorted(days)[-KEEP:]
    #    證交所、櫃買各自記錄（i／iO、m／mO），一邊掛了不影響另一邊
    tasks = [
        ('i',  '證交所法人', lambda day: [u % day.strftime('%Y%m%d') for u in TWSE_I], parse_insti, TWSE_GAP),
        ('iO', '櫃買法人',   tpex_i_urls, parse_insti, TPEX_GAP),
        ('m',  '證交所融資', lambda day: [u % day.strftime('%Y%m%d') for u in TWSE_M], parse_margin, TWSE_GAP),
        ('mO', '櫃買融資',   tpex_m_urls, parse_margin, TPEX_GAP),
    ]
    for s in reversed(trading[-CHIP_BACK:]):          # 從最近的往回補，近期的先有
        if stopped:
            break
        if s == today.isoformat() and not chip_ok_today:
            continue
        day = datetime.strptime(s, '%Y-%m-%d').date()
        for key, name, urls, parse, gap in tasks:
            if key in done.setdefault(s, {'q'}):
                continue
            if tick():
                stopped = '時間到了，先寫檔，下次接著補'
                break
            rows, reached = try_urls(urls(day), parse, gap, diag=name)
            for wait in (60, 120, 180):                 # 被擋（回 HTML）多半是問太密，歇一下再試，不要整段放棄
                if reached or tick():
                    break
                print('  %s %s回的不是資料，休息 %d 秒再試' % (s, name, wait), flush=True)
                time.sleep(wait)
                rows, reached = try_urls(urls(day), parse, gap, diag=name)
            if not reached:
                stopped = '%s %s連不上' % (s, name)
                break
            if not rows:
                if day < today:
                    done[s].add(key)                   # 過去的日子沒這份資料，就當沒有，不再問
                else:
                    print('  %s 今天的%s還沒出來' % (s, name), flush=True)
                time.sleep(gap)
                continue
            n = 0
            for code, vals in rows.items():
                row = days[s].get(code)
                if row is None:
                    continue
                if key[0] == 'i':
                    row['fi'], row['it'], row['dl'] = lots(vals[0]), lots(vals[1]), lots(vals[2])
                else:
                    row['mg'], row['ms'] = clean(vals[0], 0), clean(vals[1], 0)
                n += 1
            done[s].add(key)
            print('  %s %s：%d 檔，對上 %d 檔' % (s, name, len(rows), n), flush=True)
            time.sleep(gap)

    if stopped:
        print('::warning::%s' % stopped, flush=True)

    # 4. 基本面與新聞：每次都抓，抓不到沿用舊的
    print('抓基本面…', flush=True)
    try:
        fund = fetch_fundamentals()
    except Exception as e:
        print('  基本面整個失敗：%s' % e, flush=True)
        fund = {}
    for code, f in fund.items():
        if code in info:
            info[code]['f'] = merge_fund(info[code].get('f'), f)
    print('抓新聞…', flush=True)
    news = []
    try:
        news = fetch_news()
    except Exception as e:
        print('  新聞整個失敗：%s' % e, flush=True)
    # 成交值前 NEWS_TOP 名抓個股新聞
    snews = {}
    try:
        lastd = sorted(days)[-1]
        turn = []
        for code, row in days[lastd].items():
            if code in info and row.get('c') and row.get('v') and info[code].get('n'):
                turn.append((row['c'] * row['v'], code))
        turn.sort(reverse=True)
        snews = fetch_stock_news([(code, info[code]['n']) for _, code in turn[:NEWS_TOP]])
    except Exception as e:
        print('  個股新聞整個失敗：%s' % e, flush=True)

    # 5. 寫檔
    dates = sorted(days)[-KEEP:]
    skip = sorted(d for d in skip if d >= (today - timedelta(days=LOOKBACK + 30)).isoformat())
    codes = [c for c in info if any(c in days[d] for d in dates)]
    if len(codes) < MIN_CODES and os.path.exists('history.json'):
        print('::warning::這次只有 %d 檔，怪怪的，不覆蓋舊檔' % len(codes), flush=True)
        return
    hist_q, shards = {}, {}
    acc = {str(h): [0, 0.0, 0.0, 0.0] for h in HORIZ}     # 全市場回測合計：[次數, 方向命中, 目標達成, 誤差]（後三個先加權）
    BT_ERR = [0]
    n20 = min(20, len(dates))
    off = len(dates) - n20
    hoff = max(0, len(dates) - HIST_KEEP)              # history.json 只留最後 HIST_KEEP 天
    kfrom = max(0, len(dates) - KEEP_K)                # 開高低從這裡開始存
    xfrom = max(0, len(dates) - CHIP_BACK)             # 法人融資從這裡開始存
    for code in codes:
        m = info[code]
        series = {k: [] for k in KEYS}
        for d in dates:
            row = days[d].get(code) or {}
            for k in KEYS:
                v = row.get(k)
                series[k].append(clean(v) if k in ('o', 'h', 'l', 'c') else v)
        s = {'n': m.get('n', ''), 'm': m.get('m', ''), 'c': series['c'], 'v': series['v'],
             'k': {'from': kfrom, 'o': series['o'][kfrom:], 'h': series['h'][kfrom:], 'l': series['l'][kfrom:]},
             'x': {'from': xfrom}}
        for k in ('fi', 'it', 'dl', 'mg', 'ms'):
            tail = series[k][xfrom:]
            if any(v is not None for v in tail):
                s['x'][k] = tail
        if m.get('f'):
            s['f'] = m['f']
        nwh = list(m.get('nwh') or [])                  # 每天的新聞分數留下來（[日期, 則數, 利多, 利空]），以後回測消息面要用
        if code in snews:
            s['nw'] = snews[code]
            if not nwh or nwh[-1][0] != dates[-1]:
                nwh.append([dates[-1], snews[code]['n'], snews[code]['p'], snews[code]['q']])
            else:
                nwh[-1] = [dates[-1], snews[code]['n'], snews[code]['p'], snews[code]['q']]
        if nwh:
            s['nwh'] = nwh[-NWH_KEEP:]
        shards.setdefault(shard_of(code), {})[code] = s
        h = {'n': s['n'], 'm': s['m'], 'c': series['c'][hoff:], 'v': series['v'][hoff:]}
        try:
            ai = own_analogs(series['c'], series['v'])
        except Exception:
            ai = None
        if ai and ai.get('10'):
            h['ai'] = ai
        try:
            bt = backtest(series['c'], series['v'], series['h'], series['l'], dates)
        except Exception as e:
            bt = None
            if BT_ERR[0] < 3:
                BT_ERR[0] += 1
                print('  回測 %s 失敗：%s' % (code, e), flush=True)
        if bt:
            s['bt'] = bt
            h['ac'] = bt['10'][:3]
            for hz, st in bt.items():
                if hz in acc:
                    acc[hz][0] += st[0]
                    for j in (1, 2, 3):
                        acc[hz][j] += st[j] * st[0]
        if code in snews:
            h['nw'] = [snews[code]['n'], snews[code]['p'], snews[code]['q']]
        for k in ('fi', 'it', 'dl', 'mg', 'ms'):
            tail = series[k][off:]
            if any(v is not None for v in tail):
                h[k] = tail
        f = m.get('f') or {}
        for k in ('pe', 'yld'):
            if f.get(k) is not None:
                h[k] = f[k]
        hist_q[code] = h

    os.makedirs('d', exist_ok=True)
    for sh, q in shards.items():
        with open(os.path.join('d', sh + '.json'), 'w', encoding='utf-8') as fp:
            json.dump({'dates': dates, 'q': q}, fp, ensure_ascii=False, separators=(',', ':'))
    for fn in os.listdir('d'):
        if fn.endswith('.json') and fn[:-5] not in shards:
            os.remove(os.path.join('d', fn))
    idx = {'TAIEX': [clean(taiex.get(d)) for d in dates],
           'TAIEXo': [clean(taiex_ohl[d][0]) if d in taiex_ohl else None for d in dates],
           'TAIEXh': [clean(taiex_ohl[d][1]) if d in taiex_ohl else None for d in dates],
           'TAIEXl': [clean(taiex_ohl[d][2]) if d in taiex_ohl else None for d in dates]}
    with open('idx.json', 'w', encoding='utf-8') as fp:            # 首頁大盤 K 線用：五年的加權指數開高低收
        json.dump({'dates': dates, 'updated': now.strftime('%Y-%m-%d %H:%M'), 'c': idx['TAIEX'],
                   'o': idx['TAIEXo'], 'h': idx['TAIEXh'], 'l': idx['TAIEXl']},
                  fp, ensure_ascii=False, separators=(',', ':'))
    with open('meta.json', 'w', encoding='utf-8') as fp:
        json.dump({'dates': dates, 'updated': now.strftime('%Y-%m-%d %H:%M'), 'keep': KEEP, 'skip': skip,
                   'done': {d: sorted(done.get(d, {'q'})) for d in dates}, 'idx': idx},
                  fp, ensure_ascii=False, separators=(',', ':'))
    acc_out = {hz: [v[0], round(v[1] / v[0], 1), round(v[2] / v[0], 1), round(v[3] / v[0], 2)] for hz, v in acc.items() if v[0]}
    out = {'dates': dates[hoff:], 'updated': now.strftime('%Y-%m-%d %H:%M'), 'keep': HIST_KEEP, 'count': len(hist_q),
           'idx': {k: v[hoff:] for k, v in idx.items()}, 'chipDays': n20, 'q': hist_q, 'acc': acc_out, 'btDays': BT_DAYS}
    print('  回測：%s' % acc_out, flush=True)
    with open('history.json', 'w', encoding='utf-8') as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(',', ':'))
    if news or not os.path.exists('news.json'):
        with open('news.json', 'w', encoding='utf-8') as fp:
            json.dump({'updated': now.strftime('%Y-%m-%d %H:%M'), 'items': news}, fp,
                      ensure_ascii=False, separators=(',', ':'))
    chip_have = sum(1 for d in dates if 'i' in done.get(d, set()) and 'iO' in done.get(d, set()))
    print('寫入 history.json（%d 個交易日 %s ～ %s，%d 檔，這次新增 %d 天，法人有 %d 天）與 d/ %d 個分片'
          % (len(dates), dates[0] if dates else '-', dates[-1] if dates else '-', len(hist_q), got, chip_have, len(shards)))


if __name__ == '__main__':
    main()
