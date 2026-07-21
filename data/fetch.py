"""数据抓取：支持多数据源 provider。

默认 provider = alphavantage（需环境变量 ALPHAVANTAGE_API_KEY），
可用 STOCK_PROVIDER=yfinance 切回 yfinance。

Alpha Vantage 免费档限制：每分钟5次、每天25次。每只票需 2 次调用
（日线 + 财报），所以一天约能分析 10~12 只。已内置缓存 + 调用间隔。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PROVIDER = os.environ.get("STOCK_PROVIDER", "tiingo").lower()
AV_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
AV_BASE = "https://www.alphavantage.co/query"
_AV_LAST_CALL = [0.0]      # 上次调用时间戳，用于限流间隔
AV_MIN_INTERVAL = 15.0     # 免费档每分钟5次 → 至少间隔12s，留余量15s

TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")
TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"
# tiingo 模式下是否用外部源补基本面(Tiingo 免费档无基本面)。
TIINGO_USE_AV_FUNDAMENTALS = os.environ.get("TIINGO_FUNDAMENTALS_VIA_AV", "1") == "1"

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"
# 基本面首选源：有 Finnhub key 用 finnhub(60次/分,宽松)，否则回退 alphavantage
FUNDAMENTALS_SOURCE = os.environ.get(
    "FUNDAMENTALS_SOURCE", "finnhub" if FINNHUB_KEY else "alphavantage").lower()


@dataclass
class StockData:
    ticker: str
    history: pd.DataFrame
    fundamentals: dict = field(default_factory=dict)
    ok: bool = True
    error: str = ""


def _cache_path(ticker: str) -> str:
    return os.path.join(CACHE_DIR, f"{ticker.upper()}_{PROVIDER}_{date.today().isoformat()}.pkl")


# ---------- Alpha Vantage ----------

def _av_get(params: dict) -> dict:
    """带限流间隔的 Alpha Vantage 请求。"""
    elapsed = time.time() - _AV_LAST_CALL[0]
    if elapsed < AV_MIN_INTERVAL:
        time.sleep(AV_MIN_INTERVAL - elapsed)
    params["apikey"] = AV_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{AV_BASE}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "stock-screener/1.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode()
    _AV_LAST_CALL[0] = time.time()
    data = json.loads(raw)
    # Alpha Vantage 用 Note/Information 字段返回限流或错误提示
    if "Note" in data or "Information" in data:
        raise RuntimeError(data.get("Note") or data.get("Information"))
    if "Error Message" in data:
        raise RuntimeError(data["Error Message"])
    return data


def _av_history(ticker: str) -> pd.DataFrame:
    # 免费档只支持 compact(最近~100交易日)；full 是付费功能
    data = _av_get({"function": "TIME_SERIES_DAILY", "symbol": ticker,
                    "outputsize": "compact"})
    ts = data.get("Time Series (Daily)")
    if not ts:
        return pd.DataFrame()
    rows = []
    for d, ohlcv in ts.items():
        rows.append({
            "Date": pd.Timestamp(d),
            "Open": float(ohlcv["1. open"]),
            "High": float(ohlcv["2. high"]),
            "Low": float(ohlcv["3. low"]),
            "Close": float(ohlcv["4. close"]),
            "Volume": float(ohlcv["5. volume"]),
        })
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    return df.iloc[-300:]  # 最近约300交易日足够算所有因子


def _av_fundamentals(ticker: str) -> dict:
    """用 EARNINGS(季度EPS) + INCOME_STATEMENT(季度营收) 算同比与加速。"""
    out = {"eps_yoy": None, "rev_yoy": None, "eps_accel": None, "eps_growth_positive": None}
    try:
        e = _av_get({"function": "EARNINGS", "symbol": ticker})
        q = e.get("quarterlyEarnings", [])
        # 时间降序，reportedEPS 字符串
        eps = [float(x["reportedEPS"]) for x in q[:8]
               if x.get("reportedEPS") not in (None, "None", "")]
        if len(eps) >= 5:
            def yoy(i):
                if i + 4 < len(eps) and eps[i + 4] != 0:
                    return (eps[i] - eps[i + 4]) / abs(eps[i + 4]) * 100
                return None
            g0, g1 = yoy(0), yoy(1)
            out["eps_yoy"] = g0
            if g0 is not None and g1 is not None:
                out["eps_accel"] = g0 > g1
            if g0 is not None:
                out["eps_growth_positive"] = g0 > 0
    except Exception:
        pass
    try:
        inc = _av_get({"function": "INCOME_STATEMENT", "symbol": ticker})
        q = inc.get("quarterlyReports", [])
        rev = [float(x["totalRevenue"]) for x in q[:8]
               if x.get("totalRevenue") not in (None, "None", "")]
        if len(rev) >= 5 and rev[4] != 0:
            out["rev_yoy"] = (rev[0] - rev[4]) / abs(rev[4]) * 100
    except Exception:
        pass
    return out


# ---------- Finnhub 基本面 (60次/分，宽松) ----------

def _finnhub_get(path: str, params: dict) -> dict:
    params["token"] = FINNHUB_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FINNHUB_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "stock-screener/1.0"})
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=30).read().decode()
            return json.loads(raw)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {}


def _finnhub_fundamentals(ticker: str) -> dict:
    """Finnhub 基本面。metric=all 直接给季度同比(已是百分数)，
    EPS 加速用 /stock/earnings 的连续两季 actual 判断。"""
    out = {"eps_yoy": None, "rev_yoy": None, "eps_accel": None, "eps_growth_positive": None}
    # 1) metric=all: 现成的季度同比(revenueGrowthQuarterlyYoy / epsGrowthQuarterlyYoy)
    try:
        m = _finnhub_get("stock/metric", {"symbol": ticker, "metric": "all"})
        metric = m.get("metric", {}) if isinstance(m, dict) else {}
        rev = metric.get("revenueGrowthQuarterlyYoy")
        if rev is None:
            rev = metric.get("revenueGrowthTTMYoy")
        if rev is not None:
            out["rev_yoy"] = float(rev)  # 已是百分数
        eps_q = metric.get("epsGrowthQuarterlyYoy")
        if eps_q is None:
            eps_q = metric.get("epsGrowthTTMYoy")
        if eps_q is not None:
            out["eps_yoy"] = float(eps_q)
            out["eps_growth_positive"] = float(eps_q) > 0
    except Exception:
        pass
    # 2) EPS 加速：连续两季 actual 的同比是否递增
    try:
        earn = _finnhub_get("stock/earnings", {"symbol": ticker})
        if isinstance(earn, list):
            eps = [float(x["actual"]) for x in earn[:8]
                   if x.get("actual") not in (None, "None", "")]
            if len(eps) >= 6:
                def yoy(i):
                    if i + 4 < len(eps) and eps[i + 4] != 0:
                        return (eps[i] - eps[i + 4]) / abs(eps[i + 4]) * 100
                    return None
                g0, g1 = yoy(0), yoy(1)
                if g0 is not None and g1 is not None:
                    out["eps_accel"] = g0 > g1
                # metric 没给 eps 同比时，用 earnings 自算兜底
                if out["eps_yoy"] is None and g0 is not None:
                    out["eps_yoy"] = g0
                    out["eps_growth_positive"] = g0 > 0
    except Exception:
        pass
    return out


def get_fundamentals(ticker: str) -> dict:
    """基本面统一入口：按 FUNDAMENTALS_SOURCE 选源，失败回退。"""
    empty = {"eps_yoy": None, "rev_yoy": None, "eps_accel": None, "eps_growth_positive": None}
    if FUNDAMENTALS_SOURCE == "finnhub" and FINNHUB_KEY:
        f = _finnhub_fundamentals(ticker)
        if f.get("eps_yoy") is not None or f.get("rev_yoy") is not None:
            return f
        # Finnhub 拿不到就回退 AV
        if AV_KEY:
            return _av_fundamentals(ticker)
        return f
    if AV_KEY:
        return _av_fundamentals(ticker)
    return empty


def _fetch_alphavantage(ticker: str) -> StockData:
    if not AV_KEY:
        return StockData(ticker, pd.DataFrame(), ok=False,
                         error="缺少 ALPHAVANTAGE_API_KEY 环境变量")
    try:
        hist = _av_history(ticker)
        if hist.empty or len(hist) < 60:
            return StockData(ticker, pd.DataFrame(), ok=False,
                             error=f"历史数据不足({len(hist)}<60)")
        fund = _av_fundamentals(ticker)
        return StockData(ticker, hist, fundamentals=fund, ok=True)
    except Exception as e:
        return StockData(ticker, pd.DataFrame(), ok=False, error=str(e))


# ---------- Tiingo (日线) + Alpha Vantage (基本面) 混合 ----------

def _tiingo_history(ticker: str, years: int = 2) -> pd.DataFrame:
    """Tiingo EOD 日线，用复权价(adj*)供技术分析。免费档给多年历史。"""
    start = (date.today().replace(year=date.today().year - years)).isoformat()
    url = (f"{TIINGO_BASE}/{ticker}/prices"
           f"?startDate={start}&format=json&token={TIINGO_KEY}")
    req = urllib.request.Request(url, headers={"Content-Type": "application/json",
                                               "User-Agent": "stock-screener/1.0"})
    # 网络抖动/限流重试: timeout=25s,3次,失败快速跳过不死等
    raw = None
    last_err = None
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=25).read().decode()
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5)  # 短等,撞限流也不拖太久
            time.sleep(2 * (attempt + 1))
    if raw is None:
        raise last_err
    arr = json.loads(raw)
    if not isinstance(arr, list) or not arr:
        return pd.DataFrame()
    rows = []
    for x in arr:
        rows.append({
            "Date": pd.Timestamp(x["date"]).tz_localize(None),
            "Open": float(x.get("adjOpen") or x["open"]),
            "High": float(x.get("adjHigh") or x["high"]),
            "Low": float(x.get("adjLow") or x["low"]),
            "Close": float(x.get("adjClose") or x["close"]),
            "Volume": float(x.get("adjVolume") or x["volume"]),
        })
    return pd.DataFrame(rows).set_index("Date").sort_index()


def _fetch_tiingo(ticker: str) -> StockData:
    if not TIINGO_KEY:
        return StockData(ticker, pd.DataFrame(), ok=False,
                         error="缺少 TIINGO_API_KEY 环境变量")
    try:
        hist = _tiingo_history(ticker)
        if hist.empty or len(hist) < 60:
            return StockData(ticker, pd.DataFrame(), ok=False,
                             error=f"历史数据不足({len(hist)}<60)")
        # 基本面：Tiingo 免费档没有，走统一入口(Finnhub 优先，回退 AV)
        fund = {"eps_yoy": None, "rev_yoy": None, "eps_accel": None, "eps_growth_positive": None}
        try:
            fund = get_fundamentals(ticker)
        except Exception:
            pass
        return StockData(ticker, hist, fundamentals=fund, ok=True)
    except Exception as e:
        return StockData(ticker, pd.DataFrame(), ok=False, error=str(e))


# ---------- yfinance (备选) ----------

def _fetch_yfinance(ticker: str, period: str) -> StockData:
    try:
        import yfinance as yf
    except ImportError:
        return StockData(ticker, pd.DataFrame(), ok=False, error="yfinance 未安装")
    try:
        tk = yf.Ticker(ticker)
        hist = None
        last_err = ""
        for attempt in range(4):
            try:
                hist = tk.history(period=period, interval="1d", auto_adjust=True)
                if hist is not None and not hist.empty:
                    break
            except Exception as e:
                last_err = str(e)
            time.sleep(2 * (2 ** attempt))
        if hist is None or hist.empty or len(hist) < 60:
            return StockData(ticker, pd.DataFrame(), ok=False,
                             error=last_err or f"历史数据不足")
        hist = hist.rename(columns=str.title)
        # yfinance 基本面(简化，复用旧逻辑)
        fund = {"eps_yoy": None, "rev_yoy": None, "eps_accel": None, "eps_growth_positive": None}
        try:
            qf = tk.quarterly_income_stmt
            if qf is not None and not qf.empty and len(qf.columns) >= 5:
                cols = list(qf.columns)
                def row(name):
                    for k in qf.index:
                        if str(k).strip().lower() == name.lower():
                            return qf.loc[k]
                    return None
                rev = row("Total Revenue")
                if rev is not None and pd.notna(rev[cols[0]]) and pd.notna(rev[cols[4]]) and rev[cols[4]]:
                    fund["rev_yoy"] = (rev[cols[0]] - rev[cols[4]]) / abs(rev[cols[4]]) * 100
                ni = row("Net Income")
                if ni is not None and pd.notna(ni[cols[0]]) and pd.notna(ni[cols[4]]) and ni[cols[4]]:
                    fund["eps_yoy"] = (ni[cols[0]] - ni[cols[4]]) / abs(ni[cols[4]]) * 100
        except Exception:
            pass
        return StockData(ticker, hist, fundamentals=fund, ok=True)
    except Exception as e:
        return StockData(ticker, pd.DataFrame(), ok=False, error=str(e))


# ---------- 统一入口 ----------

def fetch(ticker: str, period: str = "1y", use_cache: bool = True) -> StockData:
    ticker = ticker.upper().strip()
    cp = _cache_path(ticker)
    if use_cache and os.path.exists(cp):
        try:
            return pd.read_pickle(cp)
        except Exception:
            pass

    if PROVIDER == "yfinance":
        sd = _fetch_yfinance(ticker, period)
    elif PROVIDER == "tiingo":
        sd = _fetch_tiingo(ticker)
    else:
        sd = _fetch_alphavantage(ticker)

    if sd.ok and use_cache:
        try:
            pd.to_pickle(sd, cp)
        except Exception:
            pass
    return sd


def fetch_benchmark(period: str = "1y") -> pd.DataFrame:
    """大盘基准：Alpha Vantage 用 SPY。"""
    sd = fetch("SPY", period=period)
    return sd.history if sd.ok else pd.DataFrame()


def fetch_many(tickers: list[str], period: str = "1y") -> dict[str, StockData]:
    return {t.upper(): fetch(t, period=period) for t in tickers}
