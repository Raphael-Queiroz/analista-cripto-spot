#!/usr/bin/env python3
"""Cripto v3: dados públicos, sinais determinísticos e simulação. NÃO envia ordens.

Python 3.10+, somente biblioteca padrão. Leia GUIA.md e REGRAS.md antes de usar.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import copy
import csv
import hashlib
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "3.1.0"
DAY = 86_400_000
FIVE_MIN = 300_000
API = "https://data-api.binance.vision/api/v3/"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
           "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "LTCUSDT"]
DEFAULT = {
    "version": VERSION, "symbols": SYMBOLS, "history_start": "2019-01-01",
    "min_bars": 250, "pivot_sides": 2, "resistance_window": 60,
    "target_window": 250, "touch_separation": 5, "touch_tolerance_atr": 0.25,
    "rsi_low": 40, "rsi_high": 50, "breakout_rsi_max": 75,
    "breakout_volume": 1.5, "minimum_net_rr": 2.0,
    "signal_valid_minutes": 60, "execution_delay_minutes": 5,
    "timezone": "America/Sao_Paulo", "analysis_times": ["08:00", "17:30", "21:10"],
    "fee_per_side": 0.001, "slippage_per_side": 0.001,
    "synthetic_targets": True, "stop_variant": "steps",
    "paper_equity": 10000, "risk_fraction": 0.005,
    "max_open_risk": 0.02, "max_positions": 3,
    "max_position_fraction": 0.10, "max_exposure_fraction": 0.30,
    "max_drawdown": 0.10, "max_daily_loss": 0.02, "max_weekly_loss": 0.04,
    "cost_assumptions_confirmed": False, "risk_limits_confirmed": False,
    "allow_macro_context": False,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite_positive(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def epoch(date):
    value = datetime.fromisoformat(date.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def config(path=None):
    c = copy.deepcopy(DEFAULT)
    if path:
        values = read(path)
        require(not (set(values) - set(c)), "Configuração contém campo desconhecido")
        c.update(values)
    require(c["version"] == VERSION, "Versão de configuração incompatível")
    require(c["symbols"] and len(set(c["symbols"])) == len(c["symbols"]), "Lista de ativos inválida")
    require(all(s in SYMBOLS for s in c["symbols"]), "Ativo fora do universo desta versão")
    for k in ["fee_per_side", "slippage_per_side"]:
        require(isinstance(c[k], (int, float)) and 0 <= c[k] < 0.1, f"{k} inválido")
    for k in ["risk_fraction", "max_open_risk", "max_position_fraction",
              "max_exposure_fraction", "max_drawdown", "max_daily_loss", "max_weekly_loss"]:
        require(finite_positive(c[k]) and c[k] <= 1, f"{k} inválido")
    for k in ["min_bars", "pivot_sides", "resistance_window", "target_window",
              "touch_separation", "signal_valid_minutes", "execution_delay_minutes", "max_positions"]:
        require(isinstance(c[k], int) and c[k] > 0, f"{k} inválido")
    require(c["min_bars"] >= 250, "Histórico mínimo de 250 candles fechados")
    require(c["stop_variant"] in ("steps", "fixed"), "Variante de stop desconhecida")
    require(0 < c["execution_delay_minutes"] < c["signal_valid_minutes"] <= 60,
            "Execução deve ocorrer após análise e antes da expiração")
    require(c["timezone"] == "America/Sao_Paulo" and c["analysis_times"] == ["08:00", "17:30", "21:10"],
            "Esta versão usa 08:00, 17:30 e 21:10 em America/Sao_Paulo")
    require(c["execution_delay_minutes"] % 5 == 0, "Simulação exige atraso múltiplo de cinco minutos")
    require(c["risk_fraction"] <= c["max_open_risk"], "Risco unitário excede agregado")
    require(c["max_position_fraction"] <= c["max_exposure_fraction"], "Exposição unitária excede total")
    require(finite_positive(c["paper_equity"]), "Capital simulado inválido")
    require(c["minimum_net_rr"] >= 2 and 0 <= c["rsi_low"] < c["rsi_high"] <= 100,
            "Parâmetros de setup inválidos")
    for k in ("synthetic_targets", "cost_assumptions_confirmed", "risk_limits_confirmed", "allow_macro_context"):
        require(isinstance(c[k], bool), f"{k} deve ser true ou false, sem aspas")
    require(finite_positive(c["touch_tolerance_atr"]) and finite_positive(c["breakout_volume"])
            and finite_positive(c["minimum_net_rr"]) and 0 < c["breakout_rsi_max"] <= 100,
            "Parâmetros numéricos de rompimento/RR inválidos")
    return c


def round_context(now, c):
    """Uma rodada é identificada pelo horário agendado, inclusive em uma repetição."""
    zone = ZoneInfo(c["timezone"])
    local = datetime.fromtimestamp(now/1000, zone)
    starts = []
    for delta in (-1, 0, 1):
        date = (local + timedelta(days=delta)).date()
        for clock in c["analysis_times"]:
            hour, minute = map(int, clock.split(":"))
            starts.append(int(datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone).timestamp()*1000))
    start = max(t for t in starts if t <= now)
    next_start = min(t for t in starts if t > now)
    expiry = min(start + c["signal_valid_minutes"]*60000, next_start)
    return {"round_id": datetime.fromtimestamp(start/1000, zone).strftime("%Y-%m-%d_%H%M"),
            "scheduled_at": start, "scheduled_local": datetime.fromtimestamp(start/1000, zone).isoformat(),
            "expires_at": expiry, "next_round_at": next_start,
            "entry_window_open": start <= now < expiry, "timezone": c["timezone"]}


def round_signal(base, now, c):
    ctx = round_context(now, c)
    result = copy.deepcopy(base)
    result["base_signal_id"] = base["signal_id"]
    result["round_id"] = ctx["round_id"]
    result["scheduled_at"] = ctx["scheduled_at"]
    result["available_at"] = max(base["available_at"], ctx["scheduled_at"])
    result["expires_at"] = min(base["expires_at"], ctx["expires_at"])
    result["signal_id"] = digest([base["signal_id"], ctx["round_id"]])[:24]
    return result


def consumed_setup(signal, events):
    """Uma compra por ativo/candle de origem, mesmo depois de vender ou mudar setup."""
    origin = signal["candle_close"] + 1
    return any(e.get("type") == "BUY_FILL" and e.get("confirmed") is True
               and e.get("symbol") == signal["symbol"]
               and (e.get("candle_open") == signal["candle_open"] or origin <= e["at"] < origin+DAY)
               for e in events)


def api_get(endpoint, params=None):
    require(endpoint in ("time", "klines", "exchangeInfo", "ticker/bookTicker"), "Endpoint não autorizado")
    url = API + endpoint + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CriptoResearch/3.1"})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = min(10, max(1, int(exc.headers.get("Retry-After", "2"))))
            time.sleep(delay)
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("Consulta não concluída")


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


def parse_bars(rows, asof, offset=0, fresh=False):
    result = []
    for r in rows:
        b = Bar(int(r[0]), *(float(r[i]) for i in range(1, 6)), int(r[6]))
        require(b.open_time % DAY == offset, "Candle com fuso/abertura inesperados")
        require(b.close_time == b.open_time + DAY - 1, "Duração diária inválida")
        require(all(finite_positive(v) for v in (b.open, b.high, b.low, b.close)), "OHLC inválido")
        require(math.isfinite(b.volume) and b.volume >= 0, "Volume inválido")
        require(b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high, "OHLC inconsistente")
        if b.close_time < asof:
            result.append(b)
    require(result, "Nenhum candle fechado disponível")
    require(all(b.open_time - a.open_time == DAY for a, b in zip(result, result[1:])),
            "Candles duplicados, fora de ordem ou lacuna no histórico")
    if fresh:
        expected = ((asof - offset) // DAY) * DAY + offset - DAY
        require(result[-1].open_time == expected, "Último candle fechado ausente: dados atrasados")
    return result


def fetch_symbol(symbol, c, folder, now, shifted=False):
    delay = c["execution_delay_minutes"] if shifted else 0
    offset = delay * 60000
    suffix = "exec" if shifted else "utc"
    target = Path(folder) / f"{symbol}_{suffix}.json"
    rows = []
    if target.exists():
        old = read(target)
        require(old["symbol"] == symbol and old["offset_ms"] == offset and
                old["history_start"] == c["history_start"], "Cache incompatível com configuração")
        require(old["sha256_rows"] == digest(old["rows"]), "Cache alterado/corrompido antes da atualização")
        rows = old["rows"]
        parse_bars(rows, old["fetched_at"], offset)
    start = rows[-1][0] + DAY if rows else epoch(c["history_start"])
    while start < now:
        params = {"symbol": symbol, "interval": "1d", "limit": 1000, "startTime": start}
        if shifted:
            params["timeZone"] = f"-{delay // 60:02d}:{delay % 60:02d}"
        page = api_get("klines", params)
        require(isinstance(page, list), "Resposta da Binance não é uma lista de candles")
        if not page:
            break
        require(page[0][0] >= start, "Paginação regressiva")
        closed = [r for r in page if r[6] < now]
        rows.extend(closed)
        nxt = page[-1][0] + DAY
        require(nxt > start, "Paginação sem progresso")
        start = nxt
        if len(page) < 1000:
            break
    bars = parse_bars(rows, now, offset, fresh=True)
    doc = {"symbol": symbol, "fetched_at": now, "fetched_at_utc": utc(now),
           "source": API + "klines", "offset_ms": offset,
           "history_start": c["history_start"], "rows": rows, "sha256_rows": digest(rows)}
    save(target, doc)
    return {"symbol": symbol, "series": suffix, "bars": len(bars),
            "first": utc(bars[0].open_time), "last": utc(bars[-1].close_time), "sha256": doc["sha256_rows"]}


def fetch(c, folder, extra_symbols=()):
    now = api_get("time")["serverTime"]
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(fetch_symbol, s, c, folder, now, shifted): (s, shifted)
                for s in sorted(set(c["symbols"]) | set(extra_symbols) | {"BTCUSDT"}) for shifted in (False,)}
        for f in as_completed(jobs):
            try:
                item = f.result()
                results.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
            except Exception as exc:
                errors.append({"job": jobs[f], "error": str(exc)})
    save(Path(folder) / "manifesto.json", {"asof": now, "config_hash": digest(c),
                                           "results": results, "errors": errors})
    require(not errors, "Falha em dados: " + json.dumps(errors, ensure_ascii=False))


def parse_intraday(rows, start, end, allow_open=False):
    """5m é usado para execução/gatilhos; nunca substitui os indicadores diários."""
    require(end > start and start % FIVE_MIN == 0, "Janela intradiária inválida")
    result = []
    for r in rows:
        b = Bar(int(r[0]), *(float(r[i]) for i in range(1, 6)), int(r[6]))
        require(b.open_time % FIVE_MIN == 0 and b.close_time == b.open_time+FIVE_MIN-1,
                "Candle intradiário desalinhado")
        require(all(finite_positive(x) for x in (b.open,b.high,b.low,b.close))
                and math.isfinite(b.volume) and b.volume >= 0
                and b.low <= min(b.open,b.close) <= max(b.open,b.close) <= b.high, "OHLC intradiário inválido")
        if start <= b.open_time < end and (allow_open or b.close_time < end):
            result.append(b)
    last = ((end-1)//FIVE_MIN)*FIVE_MIN if allow_open else (end//FIVE_MIN-1)*FIVE_MIN
    require(result and result[0].open_time == start and result[-1].open_time == last,
            "Cobertura intradiária incompleta")
    require(all(b.open_time-a.open_time == FIVE_MIN for a,b in zip(result,result[1:])),
            "Lacuna/duplicidade intradiária")
    return result


def download_intraday(symbol, start, end):
    rows, cursor = [], start
    while cursor < end:
        page = api_get("klines", {"symbol":symbol,"interval":"5m","startTime":cursor,
                                  "endTime":end-1,"limit":1000})
        require(isinstance(page,list) and page, "Dados intradiários ausentes")
        require(page[0][0] >= cursor, "Paginação intradiária regressiva")
        rows.extend(page)
        cursor = page[-1][0]+FIVE_MIN
    return rows


def market_window(symbol, now):
    start = ((now-DAY)//FIVE_MIN)*FIVE_MIN
    return parse_intraday(download_intraday(symbol,start,now),start,now,allow_open=True)


def fetch_intraday(c, folder, start, end):
    begin, finish = epoch(start), epoch(end)
    require(begin % FIVE_MIN == finish % FIVE_MIN == 0 and finish > begin, "Datas intradiárias inválidas")
    now = api_get("time")["serverTime"]
    require(finish <= now//FIVE_MIN*FIVE_MIN, "Histórico solicitado inclui candle não encerrado")
    # Um dia anterior permite conferir o caminho desde a origem do sinal diário.
    begin -= DAY
    for symbol in sorted(set(c["symbols"]) | {"BTCUSDT"}):
        rows = download_intraday(symbol,begin,finish)
        parse_intraday(rows,begin,finish)
        save(Path(folder)/f"{symbol}_5m.json",{"symbol":symbol,"source":API+"klines",
             "interval":"5m","start":begin,"end":finish,"fetched_at":now,
             "rows":rows,"sha256_rows":digest(rows)})
        print(json.dumps({"symbol":symbol,"interval":"5m","bars":len(rows)}),flush=True)


def entry_reasons(signal, bid, ask, history, at, c):
    reasons = []
    if not signal["available_at"] <= at < signal["expires_at"]:
        reasons.append("Rodada/sinal ainda indisponível ou expirado")
    if not (finite_positive(bid) and finite_positive(ask) and bid <= ask):
        return reasons+["Cotação inválida"]
    if not signal["stop"] < bid <= ask < signal["target"]:
        reasons.append("Preço atual fora dos níveis de entrada")
    threshold = signal["evidence"]["indicators"]["sma50"] if signal["setup"] == 1 else signal["invalidation"]["level"]
    if bid <= threshold:
        reasons.append("Preço atual não sustenta a SMA 50/resistência de entrada")
    origin = signal["candle_close"]+1
    relevant = [b for b in history if origin <= b.open_time < at]
    if origin < at and (not relevant or relevant[0].open_time != origin):
        reasons.append("Histórico desde a origem do setup incompleto")
    if any(b.low <= signal["stop"] or b.high >= signal["target"] for b in relevant):
        reasons.append("Stop/alvo já tocado desde a origem; aguardar outro candle diário")
    if signal["stop"] < ask*(1+c["slippage_per_side"]) < signal["target"]:
        if net_rr(ask*(1+c["slippage_per_side"]),signal["stop"],signal["target"],c) < c["minimum_net_rr"]:
            reasons.append("R/R líquido insuficiente na cotação da rodada")
    else:
        reasons.append("Entrada estimada incompatível com stop/alvo")
    return reasons


def load_series(folder, symbol, c, shifted=False, asof=None, fresh=False):
    doc = read(Path(folder) / f"{symbol}_{'exec' if shifted else 'utc'}.json")
    require(doc["symbol"] == symbol and doc["sha256_rows"] == digest(doc["rows"]), "Cache alterado/corrompido")
    require(doc["history_start"] == c["history_start"], "Origem do histórico incompatível")
    offset = c["execution_delay_minutes"] * 60000 if shifted else 0
    require(doc["offset_ms"] == offset, "Fuso incompatível")
    return parse_bars(doc["rows"], asof or doc["fetched_at"], offset, fresh)


def indicators(bars):
    """SMA completa; EMA sem adjust; RSI e ATR com suavização de Wilder."""
    result = []
    closes = [b.close for b in bars]
    gains, losses, trs = [], [], []
    ema = avg_gain = avg_loss = atr = None
    for i, b in enumerate(bars):
        tr = b.high - b.low if i == 0 else max(b.high - b.low, abs(b.high - closes[i-1]), abs(b.low - closes[i-1]))
        trs.append(tr)
        if i:
            change = b.close - closes[i-1]
            gains.append(max(change, 0)); losses.append(max(-change, 0))
        if i == 9:
            ema = statistics.mean(closes[:10])
        elif i > 9:
            ema += 2 / 11 * (b.close - ema)
        if i == 13:
            atr = statistics.mean(trs[:14])
        elif i > 13:
            atr = (atr * 13 + tr) / 14
        if i == 14:
            avg_gain, avg_loss = statistics.mean(gains[:14]), statistics.mean(losses[:14])
        elif i > 14:
            avg_gain, avg_loss = (avg_gain * 13 + gains[-1]) / 14, (avg_loss * 13 + losses[-1]) / 14
        rsi = None
        if avg_gain is not None:
            rsi = 50.0 if avg_gain == avg_loss == 0 else (100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
        result.append({"sma50": statistics.mean(closes[i-49:i+1]) if i >= 49 else None,
                       "sma200": statistics.mean(closes[i-199:i+1]) if i >= 199 else None,
                       "ema10": ema, "rsi14": rsi, "atr14": atr,
                       "volume20_previous": statistics.mean([x.volume for x in bars[i-20:i]]) if i >= 20 else None})
    return result


def pivots(bars, asof_index, window, sides=2):
    # Só pivôs cuja confirmação já existe no asof_index.
    return [j for j in range(max(sides, asof_index-window+1), asof_index-sides+1)
            if all(bars[j].high > bars[k].high for k in range(j-sides, j+sides+1) if k != j)]


def net_rr(entry, stop, target, c):
    require(all(finite_positive(x) for x in (entry, stop, target)) and stop < entry < target,
            "É necessário 0 < stop < entrada < alvo")
    fee, slip = c["fee_per_side"], c["slippage_per_side"]
    cost = entry * (1 + fee)
    stop_proceeds = stop * (1 - slip) * (1 - fee)
    target_proceeds = target * (1 - slip) * (1 - fee)
    return (target_proceeds - cost) / (cost - stop_proceeds)


def scan_at(symbol, bars, ind, i, c):
    if i + 1 < c["min_bars"]:
        return {"symbol": symbol, "status": "DADOS_INSUFICIENTES", "signals": []}
    b, x = bars[i], ind[i]
    require(x["atr14"] > 0, "ATR nulo: sem risco mensurável")
    atr, signals, checks, rejected = x["atr14"], [], {}, []
    volume_ratio = b.volume / x["volume20_previous"] if x["volume20_previous"] > 0 else 0

    def proposal(setup, raw_stop, target, target_kind, evidence, invalidation):
        stop = b.close - 1.5 * atr if b.close - raw_stop < atr else raw_stop
        if target is None and c["synthetic_targets"]:
            target = b.close + 3 * (b.close-stop)
        if not target or not 0 < stop < b.close < target:
            rejected.append({"setup": setup, "reason": "Alvo ausente ou níveis de stop/entrada/alvo incompatíveis",
                             "reference_entry": b.close, "stop": stop, "target": target})
            return None
        rr = net_rr(b.close, stop, target, c)
        if rr < c["minimum_net_rr"]:
            rejected.append({"setup": setup, "reason": "R/R líquido abaixo do mínimo na referência",
                             "reference_entry": b.close, "stop": stop, "target": target,
                             "net_rr_reference": rr, "minimum_net_rr": c["minimum_net_rr"]})
            # Uma cotação posterior pode melhorar o R/R; somente revalidação permite entrada.
        available = b.close_time + 1 + 10 * 60000
        sig = {"symbol": symbol, "setup": setup, "version": VERSION, "config_hash": digest(c),
               "candle_open": b.open_time, "candle_close": b.close_time,
               "available_at": available, "expires_at": b.close_time+1+DAY,
               "reference_entry": b.close, "stop": stop, "target": target,
               "target_kind": target_kind, "atr14": atr, "net_rr_reference": rr,
               "gross_rr_reference": (target-b.close)/(b.close-stop), "invalidation": invalidation,
               "evidence": evidence, "volume_ratio": volume_ratio}
        sig["signal_id"] = digest([symbol, setup, b.open_time, digest(c)])[:24]
        return sig

    touched = [j for j in range(i-2, i+1) if any(
        abs(bars[j].low-ind[j][m]) < ind[j]["atr14"] for m in ("ema10", "sma50"))]
    prev_rsi = [ind[j]["rsi14"] for j in range(i-5, i)]
    peak_candidates = pivots(bars, i-3, c["target_window"], c["pivot_sides"])
    peak = peak_candidates[-1] if peak_candidates else None
    one = {"above_sma200": b.close > x["sma200"], "sma50_above_sma200": x["sma50"] > x["sma200"],
           "above_sma50_not_invalidated": b.close > x["sma50"], "touch_last3": bool(touched),
           "rsi_was_40_50": any(c["rsi_low"] <= r <= c["rsi_high"] for r in prev_rsi),
           "rsi_rising": x["rsi14"] > ind[i-1]["rsi14"], "bullish_candle": b.close > b.open,
           "prior_confirmed_peak": peak is not None}
    if all(one.values()):
        s = proposal(1, min(z.low for z in bars[i-2:i+1])-0.5*atr, bars[peak].high,
                     "historical_pivot", {"checks": one, "touches": [utc(bars[j].open_time) for j in touched],
                     "target_pivot_date": utc(bars[peak].open_time), "indicators": x}, {"type": "close_below_sma50"})
        if s: signals.append(s)
    checks["setup1"] = one

    previous_peaks = pivots(bars, i-1, c["resistance_window"], c["pivot_sides"])
    tolerance = c["touch_tolerance_atr"] * ind[i-1]["atr14"]
    levels = []
    for a in previous_peaks:
        for z in previous_peaks:
            if z-a < c["touch_separation"] or abs(bars[a].high-bars[z].high) > tolerance:
                continue
            level = max(bars[a].high, bars[z].high)
            if bars[i-1].close <= level < b.close and all(k.close <= level for k in bars[z+1:i]):
                levels.append((level, a, z))
    resistance = max(levels) if levels else None
    two = {"above_sma200": b.close > x["sma200"], "new_break_of_tested_resistance": resistance is not None,
           "volume_confirmation": volume_ratio >= c["breakout_volume"], "rsi_below_max": x["rsi14"] < c["breakout_rsi_max"]}
    if all(two.values()):
        level, a, z = resistance
        higher = [j for j in pivots(bars, i-1, c["target_window"], c["pivot_sides"]) if bars[j].high > b.close]
        target_index = min(higher, key=lambda j: (bars[j].high, j)) if higher else None
        target = bars[target_index].high if target_index is not None else None
        s = proposal(2, level-0.5*atr, target, "historical_pivot" if target is not None else "projected_3R",
                     {"checks": two, "resistance": level, "touches": [utc(bars[a].open_time), utc(bars[z].open_time)],
                      "touch_prices": [bars[a].high, bars[z].high], "tolerance": tolerance,
                      "target_window_bars": c["target_window"], "indicators": x},
                     {"type": "close_below_level", "level": level})
        if s: signals.append(s)
    checks["setup2"] = two
    return {"symbol": symbol, "status": "CANDIDATO_TECNICO" if signals else "SEM_ENTRADA",
            "candle_close": utc(b.close_time), "close": b.close, "indicators": x,
            "checks": checks, "signals": signals,
            "rejected_proposals": rejected, "volume_ratio": volume_ratio,
            "note": "Critérios técnicos completos ainda podem falhar em alvo, stop ou R/R líquido."}


def priority(signal):
    # Não favorece R/R artificialmente alto. Desempates são estáveis.
    return (signal["target_kind"] != "historical_pivot", -signal["volume_ratio"], signal["symbol"], signal["setup"])


def floor_step(value, step):
    return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step)))


def execution_plan(signal, price, at, c, equity, cash, positions, marks, filters=None):
    reasons = []
    if not signal["available_at"] <= at < signal["expires_at"]:
        reasons.append("Sinal ainda indisponível ou expirado")
    if not all(finite_positive(v) for v in (price, equity)) or not math.isfinite(cash) or cash < 0:
        return {"status": "BLOQUEADO", "reasons": reasons+["Capital/preço inválido"]}
    if any(p["symbol"] == signal["symbol"] for p in positions): reasons.append("Já existe posição no ativo")
    if len(positions) >= c["max_positions"]: reasons.append("Limite de posições")
    entry = price * (1+c["slippage_per_side"])
    stop, target = signal["stop"], signal["target"]
    step = 1e-8
    minimum, maximum = 0, float("inf")
    if filters:
        stop, target = floor_step(stop, filters["tick"]), floor_step(target, filters["tick"])
        step, minimum, maximum = filters["step"], filters["min_qty"], filters["max_qty"]
        if not filters["min_price"] <= stop < target <= filters["max_price"]:
            reasons.append("Níveis fora do filtro de preço")
    if not 0 < stop < price <= entry < target:
        return {"status": "BLOQUEADO", "reasons": reasons+["Stop/entrada/alvo incompatíveis"]}
    rr = net_rr(entry, stop, target, c)
    if rr < c["minimum_net_rr"]: reasons.append("R/R líquido abaixo do mínimo no preço executável")
    loss_per_unit = entry*(1+c["fee_per_side"]) - stop*(1-c["slippage_per_side"])*(1-c["fee_per_side"])
    try:
        exposure = sum(p["remaining_qty"] * marks[p["symbol"]] for p in positions)
        open_risk = sum(p["remaining_qty"] * max(0, marks[p["symbol"]] - p["current_stop"]*(1-c["slippage_per_side"])*(1-c["fee_per_side"])) for p in positions)
    except (KeyError, TypeError):
        return {"status": "BLOQUEADO", "reasons": reasons+["Posição/marcação incompleta"]}
    budget = min(equity*c["risk_fraction"], max(0, equity*c["max_open_risk"]-open_risk))
    qty = min(budget/loss_per_unit, cash/(entry*(1+c["fee_per_side"])),
              equity*c["max_position_fraction"]/entry,
              max(0, equity*c["max_exposure_fraction"]-exposure)/entry, maximum)
    qty = floor_step(qty, step)
    if qty <= 0 or qty < minimum: reasons.append("Saldo/risco/quantidade disponível insuficiente")
    if filters and (qty*min(stop, entry, target) < filters["min_notional"] or
                    qty*max(stop, entry, target) > filters["max_notional"]):
        reasons.append("Valor da ordem fora do filtro notional")
    return {"status": "PLANO_SIMULADO" if not reasons else "BLOQUEADO", "reasons": reasons,
            "signal_id": signal["signal_id"], "entry_estimate": entry, "stop": stop, "target": target,
            "quantity": qty, "net_rr": rr, "estimated_loss_usdt": qty*loss_per_unit,
            "risk_fraction_equity": qty*loss_per_unit/equity, "notional_usdt": qty*entry,
            "original_r_per_unit": entry-stop, "evaluated_at": at}


def invalidated(signal, bar, ind):
    inv = signal["invalidation"]
    return bar.close < (ind["sma50"] if inv["type"] == "close_below_sma50" else inv["level"])


def proposed_stop(entry, original_stop, current_stop, price, variant):
    require(0 < original_stop < entry and current_stop >= original_stop, "Stops inválidos")
    if variant == "fixed": return current_stop
    r = entry-original_stop
    new = entry+r if price >= entry+2*r else entry if price >= entry+r else current_stop
    return max(current_stop, new)


def bar_exit(bar, stop, target, c):
    """Somente simulador: stop a mercado idealizado; ambos tocados => stop primeiro."""
    if bar.open <= stop: return bar.open*(1-c["slippage_per_side"]), "stop_gap", False
    if bar.open >= target: return target*(1-c["slippage_per_side"]), "target_gap", False
    stop_hit, target_hit = bar.low <= stop, bar.high >= target
    if stop_hit: return stop*(1-c["slippage_per_side"]), "stop", target_hit
    if target_hit: return target*(1-c["slippage_per_side"]), "target", False
    return None


def replay(events, asof=None):
    """Livro de eventos confirmados. Eventos de opinião/sugestão não alteram posições."""
    positions, event_ids = {}, set()
    asof = int(time.time()*1000) if asof is None else asof
    previous = -1
    for e in events:
        require(isinstance(e.get("event_id"), str) and e["event_id"] not in event_ids, "Evento duplicado/sem ID")
        event_ids.add(e["event_id"])
        require(isinstance(e.get("at"), int) and e["at"] >= previous, "Eventos fora de ordem temporal")
        require(e["at"] <= asof, "Evento datado no futuro")
        previous = e["at"]
        require(e.get("confirmed") is True, "Evento sem confirmação de execução/alteração")
        kind, pid = e["type"], e["position_id"]
        if kind == "BUY_FILL":
            require(pid not in positions, "Reentrada/adição deve ter outra posição após encerrar a anterior")
            require(e["symbol"] in SYMBOLS, "Ativo inválido")
            require(not any(p["symbol"] == e["symbol"] and p["remaining_qty"] > 0 for p in positions.values()), "Piramidação desabilitada")
            require(all(finite_positive(e[k]) for k in ("price", "quantity", "original_stop", "target")), "Compra inválida")
            require(e["original_stop"] < e["price"] < e["target"], "Níveis da compra inválidos")
            require(math.isfinite(e["fee_usdt"]) and e["fee_usdt"] >= 0, "Taxa inválida")
            positions[pid] = {"position_id": pid, "symbol": e["symbol"], "signal_id": e["signal_id"],
                              "base_signal_id": e.get("base_signal_id"), "round_id": e.get("round_id"),
                              "candle_open": e.get("candle_open"),
                              "setup": e["setup"], "entry": e["price"], "entry_at": e["at"],
                              "initial_qty": e["quantity"], "remaining_qty": e["quantity"],
                              "original_stop": e["original_stop"], "current_stop": e["original_stop"],
                              "target": e["target"], "invalidation": e["invalidation"],
                              "stop_history": [], "protection": None, "realized_pnl": 0.0,
                              "entry_fee": e["fee_usdt"], "exit_fills": [], "partial_reasons": []}
        else:
            require(pid in positions, "Posição inexistente")
            p = positions[pid]
            require(p["remaining_qty"] > 0, "Posição já encerrada")
            if kind == "PROTECTION_CONFIRMED":
                require(e["stop"] >= p["current_stop"] and e["stop"] < e["current_price"] < p["target"], "Stop não pode descer ou nascer disparado")
                require(math.isclose(e["quantity"], p["remaining_qty"], rel_tol=1e-8), "Proteção não cobre quantidade restante")
                require(e["order_type"] in ("STOP_LOSS", "STOP_LOSS_LIMIT"), "Tipo de stop inválido")
                require(e["order_list_id"], "ID da proteção ausente")
                if e["order_type"] == "STOP_LOSS_LIMIT":
                    require(0 < e["limit_price"] <= e["stop"], "Preço limite do stop inválido")
                p["current_stop"] = e["stop"]
                p["stop_history"].append({"at": e["at"], "stop": e["stop"]})
                p["protection"] = e
            elif kind == "SELL_FILL":
                require(finite_positive(e["quantity"]) and e["quantity"] <= p["remaining_qty"]+1e-10 and finite_positive(e["price"]), "Venda excede saldo ou é inválida")
                require(math.isfinite(e["fee_usdt"]) and e["fee_usdt"] >= 0, "Taxa inválida")
                if e["quantity"] < p["remaining_qty"]-1e-10:
                    require(isinstance(e.get("partial_reason_id"), str) and e["partial_reason_id"].strip(),
                            "Venda parcial exige motivo identificável")
                # Um motivo de parcial já concluído não autoriza outra redução automática.
                if e.get("partial_reason_id"):
                    require(e["partial_reason_id"] not in p["partial_reasons"], "Parcial repetida pelo mesmo motivo")
                    p["partial_reasons"].append(e["partial_reason_id"])
                qty = min(e["quantity"], p["remaining_qty"])
                p["realized_pnl"] += qty*(e["price"]-p["entry"]) - e["fee_usdt"] - p["entry_fee"]*qty/p["initial_qty"]
                p["remaining_qty"] = max(0, p["remaining_qty"]-qty)
                p["exit_fills"].append(e)
                p["protection"] = None  # Reconciliar OCO e saldo após qualquer execução.
            elif kind == "PROTECTION_CANCELLED":
                p["protection"] = None
            else:
                raise ValueError("Tipo de evento desconhecido")
    for p in positions.values():
        p["realized_R"] = p["realized_pnl"]/(p["initial_qty"]*(p["entry"]-p["original_stop"]))
        p["status"] = "CLOSED" if p["remaining_qty"] == 0 else "OPEN"
    return list(positions.values())


def review_position(p, bars, now, price, c, intraday=None):
    require(p["status"] == "OPEN", "Revisão requer posição aberta")
    ind = indicators(bars)
    current = p["protection"]
    flags = []
    if current is None: flags.append("PROTECAO_NAO_CONFIRMADA")
    # O candle diário que contém a ativação não permite isolar o trecho posterior.
    if current:
        if any(b.open_time < current["at"] <= b.close_time for b in bars):
            flags.append("CANDLE_ABRANGE_ATIVACAO_CONFERIR_EXECUCOES_INTRADIARIAS")
        after = [b for b in bars if b.open_time >= current["at"]]
        if any(b.low <= current["stop"] or b.high >= p["target"] for b in after):
            flags.append("GATILHO_TOCADO_EXECUCAO_NAO_CONFIRMADA")
        if intraday is not None:
            after_short = [b for b in intraday if b.open_time >= current["at"]]
            if any(b.low <= current["stop"] or b.high >= p["target"] for b in after_short):
                flags.append("GATILHO_INTRADIARIO_TOCADO_EXECUCAO_NAO_CONFIRMADA")
            overlap = [b for b in intraday if b.open_time < current["at"] <= b.close_time]
            if any(b.low <= current["stop"] or b.high >= p["target"] for b in overlap):
                flags.append("GATILHO_EM_CANDLE_QUE_ABRANGE_ALTERACAO_CONFERIR_EXECUCAO")
    if current and (price <= current["stop"] or price >= p["target"]):
        flags.append("PRECO_ATUAL_ALEM_DO_GATILHO_CONFERIR_ORDEM")
    if invalidated(p, bars[-1], ind[-1]): flags.append("SAIDA_TECNICA_SUGERIDA_TESE_INVALIDADA")
    if price <= p["current_stop"] or price >= p["target"] or invalidated(p,bars[-1],ind[-1]):
        decision = "VENDER"
    elif current is None or any("GATILHO" in flag for flag in flags):
        decision = "CONFERIR_EXECUCAO"
    else:
        decision = "MANTER"
    next_stop = proposed_stop(p["entry"], p["original_stop"], p["current_stop"], price, c["stop_variant"])
    if next_stop > p["current_stop"] and next_stop < price: flags.append("ALTERACAO_DE_STOP_SUGERIDA_NAO_EXECUTADA")
    remaining_cost = p["remaining_qty"]*p["entry"] + p["entry_fee"]*p["remaining_qty"]/p["initial_qty"]
    unrealized = p["remaining_qty"]*price*(1-c["fee_per_side"])*(1-c["slippage_per_side"])-remaining_cost
    return {"position_id": p["position_id"], "symbol": p["symbol"], "flags": flags or ["MANTER"],
            "current_bid": price,
            "decision": decision,
            "decision_condition": "VENDER é recomendação apenas para quantidade ainda existente; confirmar se a OCO já executou.",
            "position_record": copy.deepcopy(p),
            "realized_pnl": p["realized_pnl"], "realized_R": p["realized_R"],
            "unrealized_pnl_estimate": unrealized,
            "total_R_estimate": (p["realized_pnl"]+unrealized)/(p["initial_qty"]*(p["entry"]-p["original_stop"])),
            "current_stop": p["current_stop"], "proposed_stop": next_stop if decision == "MANTER" else None, "reviewed_at": now,
            "execution_status": "Somente eventos confirmados alteram o registro"}


def snapshot(c, folder, ledger_path, output):
    started = api_get("time")["serverTime"]
    ledger = read(ledger_path)
    positions = replay(ledger["events"], asof=started)
    opened = [p for p in positions if p["status"] == "OPEN"]
    symbols = sorted(set(c["symbols"]) | {p["symbol"] for p in opened})
    series, scans, windows, quotes, errors = {}, [], {}, {}, []
    for symbol in symbols:
        try:
            bars = load_series(folder,symbol,c,asof=started,fresh=True)
            series[symbol] = bars
            scans.append(scan_at(symbol,bars,indicators(bars),len(bars)-1,c))
        except Exception as exc:
            errors.append({"symbol":symbol,"stage":"daily","error":str(exc)})
    def collect(symbol):
        at = api_get("time")["serverTime"]
        history = market_window(symbol,at)
        quote = api_get("ticker/bookTicker",{"symbol":symbol})
        require(finite_positive(float(quote["bidPrice"])) and float(quote["askPrice"]) >= float(quote["bidPrice"]),
                "Cotação inválida")
        return history,quote,at
    stamps = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(collect,s):s for s in symbols if s in series}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                windows[symbol],quotes[symbol],stamps[symbol] = job.result()
            except Exception as exc:
                errors.append({"symbol":symbol,"stage":"intraday_quote","error":str(exc)})
    now = api_get("time")["serverTime"]
    ctx = round_context(now,c)
    daily_changed = now//DAY != started//DAY
    if daily_changed: errors.append({"error":"Novo fechamento diário durante a coleta; executar novamente"})
    account = ledger.get("account",{})
    reconciled = account.get("reconciled_at")
    result = {"version":VERSION,"mode":"ANALYSIS_ONLY","asof":now,"asof_utc":utc(now),
              "round":ctx,"config_hash":digest(c),"ledger_hash":digest(ledger),
              "ledger_events_hash":digest(ledger["events"]),
              "market_quotes":{s:{"bid":float(q["bidPrice"]),"ask":float(q["askPrice"]),
                  "collected_at":stamps[s],"fresh":0 <= now-stamps[s] <= 60000} for s,q in quotes.items()},
              "scans":scans,"position_reviews":[],"errors":errors,"technical_candidates":[],
              "currently_valid_candidates":[],"rejected_candidates":[],
              "account_configured":bool(c["cost_assumptions_confirmed"] and c["risk_limits_confirmed"]
                  and isinstance(account.get("free_usdt"),(float,int)) and account["free_usdt"] >= 0
                  and all(finite_positive(account.get(k)) for k in ("peak_equity","day_start_equity","week_start_equity"))),
              "account_reconciled_at":reconciled,
              "account_reconciliation_fresh":isinstance(reconciled,int) and 0 <= now-reconciled <= 15*60000,
              "notice":"Rodada completa: posições primeiro, depois entradas. Nenhuma ordem enviada."}
    for p in opened:
        symbol = p["symbol"]
        if daily_changed or symbol not in quotes or not 0 <= now-stamps[symbol] <= 60000:
            result["position_reviews"].append({"position_id":p["position_id"],"symbol":symbol,
                  "decision":"DADOS_INSUFICIENTES","flags":["REVISAO_NAO_CONCLUIDA"],"position_record":p})
            continue
        try:
            result["position_reviews"].append(review_position(p,series[symbol],now,float(quotes[symbol]["bidPrice"]),c,windows[symbol]))
        except Exception as exc:
            result["position_reviews"].append({"position_id":p["position_id"],"symbol":symbol,
                  "decision":"DADOS_INSUFICIENTES","flags":[str(exc)],"position_record":p})
    position_block = any(p["decision"] != "MANTER" for p in result["position_reviews"])
    ledger_changed = digest(read(ledger_path)) != digest(ledger)
    if ledger_changed: result["errors"].append({"error":"Registro mudou durante a rodada; executar novamente"})
    for base in sorted([sig for item in scans for sig in item["signals"] if sig["symbol"] in c["symbols"]],key=priority):
        sig = round_signal(base,now,c)
        symbol = sig["symbol"]
        reasons = []
        if symbol in {p["symbol"] for p in opened}: reasons.append("Ativo já em posição")
        if consumed_setup(sig,ledger["events"]): reasons.append("Setup diário já utilizado em compra confirmada")
        if position_block: reasons.append("Resolver primeiro saída/proteção/reconciliação de posição aberta")
        if ledger_changed: reasons.append("Registro mudou durante a rodada")
        if daily_changed: reasons.append("Novo fechamento diário durante a coleta")
        if symbol not in quotes or not 0 <= now-stamps[symbol] <= 60000:
            reasons.append("Dados intradiários/cotação ausentes ou coleta com mais de 60 segundos")
        else:
            quote = quotes[symbol]
            reasons.extend(entry_reasons(sig,float(quote["bidPrice"]),float(quote["askPrice"]),windows[symbol],now,c))
            sig["quote"] = quote
            sig["quote_collected_at"] = stamps[symbol]
            sig["entry_estimate"] = float(quote["askPrice"])*(1+c["slippage_per_side"])
            if 0 < sig["stop"] < sig["entry_estimate"] < sig["target"]:
                sig["net_rr_current"] = net_rr(sig["entry_estimate"],sig["stop"],sig["target"],c)
                sig["gross_rr_current"] = (sig["target"]-sig["entry_estimate"])/(sig["entry_estimate"]-sig["stop"])
        result["technical_candidates"].append(sig)
        if reasons: result["rejected_candidates"].append({"signal_id":sig["signal_id"],"symbol":symbol,"reasons":reasons})
        else: result["currently_valid_candidates"].append(sig)
    result["sources"] = [API+"klines (1d UTC e 5m)",API+"ticker/bookTicker",API+"time"]
    result["position_actions_pending"] = position_block
    result["outcome"] = "CANDIDATOS_PARA_PRE_CHECAGEM" if result["currently_valid_candidates"] else "SEM_ENTRADA_OPERACIONAL"
    if output is None: output = str(Path("resultados")/(ctx["round_id"]+".json"))
    save(output,result)
    result["output_file"] = output
    return result


def filter_values(info):
    require(info["status"] == "TRADING" and info.get("isSpotTradingAllowed", False), "Par spot indisponível")
    fs = {f["filterType"]: f for f in info["filters"]}
    price, lot = fs["PRICE_FILTER"], fs["LOT_SIZE"]
    market = fs.get("MARKET_LOT_SIZE", lot)
    # A quantidade deve servir à compra a mercado e às duas pernas da proteção.
    step = max(float(lot["stepSize"]), float(market["stepSize"]))
    steps = [float(f["stepSize"]) for f in (lot, market) if float(f["stepSize"]) > 0]
    require(all(abs(step/s-round(step/s)) < 1e-6 for s in steps), "Passos de lote incompatíveis")
    notion = fs.get("NOTIONAL", fs.get("MIN_NOTIONAL", {}))
    return {"tick": float(price["tickSize"]), "step": step,
            "min_qty": max(float(lot["minQty"]), float(market["minQty"])),
            "max_qty": min(float(f["maxQty"]) for f in (lot, market) if float(f["maxQty"]) > 0),
            "min_price": float(price["minPrice"]), "max_price": float(price["maxPrice"]) or float("inf"),
            "min_notional": float(notion.get("minNotional", 0)),
            "max_notional": float(notion.get("maxNotional", "inf"))}


def preflight(c, snapshot_path, signal_id, ledger_path, output):
    snap, ledger = read(snapshot_path), read(ledger_path)
    require(snap["config_hash"] == digest(c), "Configuração difere da usada no sinal")
    candidates = [s for s in snap["technical_candidates"] if s["signal_id"] == signal_id]
    require(len(candidates) == 1, "Sinal inexistente/duplicado")
    signal = candidates[0]
    now = api_get("time")["serverTime"]
    require(snap.get("ledger_events_hash") == digest(ledger["events"]),
            "Eventos da carteira mudaram desde a análise; execute nova rodada")
    require(signal.get("round_id") == round_context(now,c)["round_id"]
            and signal["available_at"] <= now < signal["expires_at"], "Sinal de outra rodada ou expirado")
    require(not snap.get("position_actions_pending"), "Há revisão de posição pendente; atualizar rodada")
    require(not consumed_setup(signal,ledger["events"]), "Setup diário já utilizado")
    account = ledger.get("account", {})
    require(account.get("reconciled_at") and 0 <= now-account["reconciled_at"] <= 15*60000,
            "Atualize saldo, posição e ordens; reconciliação deve ter menos de 15 minutos")
    require(c["cost_assumptions_confirmed"] and c["risk_limits_confirmed"],
            "Custos e limites reais ainda não foram configurados; exemplos são apenas simulação")
    positions = [p for p in replay(ledger["events"], asof=now) if p["status"] == "OPEN"]
    require(all(p["protection"] for p in positions), "Há posição sem proteção reconciliada")
    marks = {}
    for p in positions:
        marks[p["symbol"]] = float(api_get("ticker/bookTicker", {"symbol": p["symbol"]})["bidPrice"])
        require(p["current_stop"] < marks[p["symbol"]] < p["target"],
                f"Gatilho de posição aberta ultrapassado; reconciliar execução: {p['symbol']}")
        window = market_window(p["symbol"],now)
        require(not any(b.close_time >= p["protection"]["at"] and
                (b.low <= p["current_stop"] or b.high >= p["target"]) for b in window),
                "Possível gatilho de posição aberta entre rodadas; reconciliar execução")
    quote = api_get("ticker/bookTicker", {"symbol": signal["symbol"]})
    reasons = entry_reasons(signal,float(quote["bidPrice"]),float(quote["askPrice"]),
                            market_window(signal["symbol"],now),now,c)
    require(not reasons, "; ".join(reasons))
    require(float(quote["bidPrice"]) > signal["stop"], "Melhor oferta de compra já ultrapassou o stop")
    info = api_get("exchangeInfo", {"symbol": signal["symbol"]})["symbols"][0]
    cash = account["free_usdt"]
    require(isinstance(cash, (int, float)) and cash >= 0 and math.isfinite(cash), "Saldo inválido")
    equity = cash + sum(p["remaining_qty"]*marks[p["symbol"]] for p in positions)
    anchors = [account.get(k) for k in ("peak_equity", "day_start_equity", "week_start_equity")]
    require(all(finite_positive(x) for x in anchors), "Âncoras de risco da carteira ausentes")
    require(account.get("day_utc") == utc(now)[:10] and account.get("iso_week") ==
            datetime.fromtimestamp(now/1000, timezone.utc).strftime("%G-W%V"), "Âncoras diária/semanal desatualizadas")
    limits = [c["max_drawdown"], c["max_daily_loss"], c["max_weekly_loss"]]
    require(all(equity/base > 1-limit for base, limit in zip(anchors, limits)), "Limite de perda atingido")
    finished_at = api_get("time")["serverTime"]
    require(digest(read(ledger_path)) == digest(ledger), "Carteira mudou durante a pré-checagem; executar novamente")
    require(0 <= finished_at-now <= 60*1000, "Coleta demorou mais de 60 segundos: recotar")
    plan = execution_plan(signal, float(quote["askPrice"]), finished_at, c, equity, cash, positions, marks, filter_values(info))
    plan["signal"] = signal
    plan["quote"] = quote
    plan["valid_until"] = min(signal["expires_at"], now+60*1000)
    plan["mode"] = "MANUAL_REVIEW_ONLY"
    plan["ai_decision_checked"] = False
    plan["ai_review_required"] = "Se houver análise da IA, conferir separadamente decisão tempestiva ALLOW/VETO; esta pré-checagem só valida os critérios mecânicos."
    plan["exchange_confirmation_required"] = "Filtros dinâmicos, saldo reservado, taxa efetiva e aceitação OCO devem ser conferidos na Binance. Nenhuma ordem foi enviada."
    save(output, plan)
    return plan


def summary(trades):
    rs = [t["net_R"] for t in trades]
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [-t["pnl"] for t in trades if t["pnl"] < 0]
    return {"closed_trades": len(trades), "wins": len(wins), "losses": len(losses),
            "flat": sum(t["pnl"] == 0 for t in trades),
            "win_rate": len(wins)/len(trades) if trades else None,
            "mean_net_R": statistics.mean(rs) if rs else None,
            "profit_factor": sum(wins)/sum(losses) if losses else None,
            "net_pnl": sum(t["pnl"] for t in trades),
            "ambiguous_bars": sum(t["ambiguous"] for t in trades)}


def load_intraday(folder, symbol, start, end):
    doc = read(Path(folder)/f"{symbol}_5m.json")
    require(doc["symbol"] == symbol and doc["interval"] == "5m"
            and doc["sha256_rows"] == digest(doc["rows"]), "Histórico 5m inválido/corrompido")
    return parse_intraday(doc["rows"],start,end)


def backtest(c, folder, start, end, vetoes=None):
    """Três decisões diárias; candles 5m servem à execução e proteção, não aos setups."""
    begin, finish = epoch(start), epoch(end)
    require(begin % DAY == finish % DAY == 0 and finish > begin, "Período UTC inválido")
    daily, computed, openings, intraday = {}, {}, {}, {}
    for symbol in sorted(set(c["symbols"]) | {"BTCUSDT"}):
        bars = load_series(folder,symbol,c)
        require(bars[-1].open_time >= finish-2*DAY, "Histórico UTC não cobre decisões")
        daily[symbol],computed[symbol] = bars,indicators(bars)
        openings[symbol] = [b.open_time for b in bars]
        # Falhar se só houver dados diários da v3.0; não inventar a sequência intradiária.
        intraday[symbol] = {b.open_time:b for b in load_intraday(folder,symbol,begin-DAY,finish)}
    cash = peak = c["paper_equity"]
    positions, trades, curve, signals_log, events = [], [], [], [], []
    day_key,week_key,day_anchor,week_anchor = None,None,cash,cash
    max_dd,hard_halt = 0.0,False
    veto_map = {}
    for v in vetoes or []:
        require(v["decision"] in ("ALLOW","VETO") and v["signal_id"] not in veto_map, "Decisão IA inválida/duplicada")
        require(v.get("prompt_version") and v.get("model") and v.get("sources"), "Decisão IA sem proveniência")
        veto_map[v["signal_id"]] = v
    scan_cache = {}
    def close_position(p,price,at,reason,ambiguous=False):
        nonlocal cash
        proceeds = p["remaining_qty"]*price*(1-c["fee_per_side"])
        pnl = proceeds-p["entry_cost"]
        cash += proceeds
        trades.append({"symbol":p["symbol"],"setup":p["signal"]["setup"],"signal_id":p["signal"]["signal_id"],
                       "round_id":p["signal"]["round_id"],"entry_at":p["entry_at"],"exit_known_at":at,
                       "entry":p["entry"],"exit":price,"quantity":p["remaining_qty"],
                       "original_stop":p["original_stop"],"target":p["target"],"pnl":pnl,
                       "net_R":pnl/(p["remaining_qty"]*(p["entry"]-p["original_stop"])),
                       "reason":reason,"ambiguous":ambiguous})
        positions.remove(p)
    delay = c["execution_delay_minutes"]*60000
    for at in range(begin,finish,FIVE_MIN):
        marks = {s:intraday[s][at].open for s in c["symbols"]}
        # Somente gaps da abertura são conhecidos antes da decisão; high/low são avaliados depois.
        for p in list(positions):
            price = marks[p["symbol"]]
            if price <= p["current_stop"]:
                close_position(p,price*(1-c["slippage_per_side"]),at,"stop_gap")
            elif price >= p["target"]:
                close_position(p,p["target"]*(1-c["slippage_per_side"]),at,"target_gap")
        equity = cash+sum(p["remaining_qty"]*marks[p["symbol"]] for p in positions)
        peak = max(peak,equity)
        max_dd = max(max_dd,1-equity/peak)
        hard_halt = hard_halt or equity <= peak*(1-c["max_drawdown"])
        date = datetime.fromtimestamp(at/1000,timezone.utc)
        if date.date() != day_key: day_key,day_anchor = date.date(),equity
        week = date.strftime("%G-W%V")
        if week != week_key: week_key,week_anchor = week,equity
        decision_at = at-delay
        ctx = round_context(decision_at,c)
        is_round = ctx["scheduled_at"] == decision_at
        if is_round:
            indexes = {s:bisect_right(openings[s],decision_at-DAY)-1 for s in c["symbols"]}
            for p in list(positions):
                s = p["symbol"]; i = indexes[s]
                require(i >= 0,"Candle ausente para posição")
                if invalidated(p["signal"],daily[s][i],computed[s][i]):
                    close_position(p,marks[s]*(1-c["slippage_per_side"]),at,"invalidation")
                else:
                    p["current_stop"] = proposed_stop(p["entry"],p["original_stop"],p["current_stop"],marks[s],c["stop_variant"])
            candidates = []
            for s in c["symbols"]:
                i = indexes[s]
                if i < 0 or i+1 < c["min_bars"]: continue
                key = s,i
                if key not in scan_cache: scan_cache[key] = scan_at(s,daily[s],computed[s],i,c)["signals"]
                candidates.extend(round_signal(sig,decision_at,c) for sig in scan_cache[key])
            for sig in sorted(candidates,key=priority):
                symbol = sig["symbol"]
                origin = sig["candle_close"]+1
                history = [intraday[symbol][t] for t in range(origin,at,FIVE_MIN)]
                reasons = entry_reasons(sig,marks[symbol],marks[symbol],history,at,c)
                if consumed_setup(sig,events): reasons.append("setup_already_used")
                eq = cash+sum(p["remaining_qty"]*marks[p["symbol"]] for p in positions)
                peak = max(peak,eq); max_dd = max(max_dd,1-eq/peak)
                hard_halt = hard_halt or eq <= peak*(1-c["max_drawdown"])
                if hard_halt or eq <= day_anchor*(1-c["max_daily_loss"]) or eq <= week_anchor*(1-c["max_weekly_loss"]):
                    reasons.append("risk_halt")
                v = veto_map.get(sig["signal_id"])
                if vetoes is not None:
                    if not v or not sig["available_at"] <= v.get("observed_at",0) <= at:
                        reasons.append("missing_timely_ai_decision")
                    elif v["decision"] == "VETO": reasons.append("ai_veto")
                plan = execution_plan(sig,marks[symbol],at,c,eq,cash,positions,marks)
                reasons.extend(plan["reasons"])
                signals_log.append({"signal_id":sig["signal_id"],"base_signal_id":sig["base_signal_id"],
                    "round_id":sig["round_id"],"symbol":symbol,"setup":sig["setup"],"available_at":sig["available_at"],
                    "entry_attempt_at":at,"decision":"BLOQUEADO" if reasons else "PLANO_SIMULADO","reasons":reasons})
                if reasons: continue
                qty,entry = plan["quantity"],plan["entry_estimate"]
                cost = qty*entry*(1+c["fee_per_side"])
                cash -= cost
                positions.append({"symbol":symbol,"signal":sig,"entry":entry,"entry_at":at,
                    "remaining_qty":qty,"original_stop":plan["stop"],"current_stop":plan["stop"],
                    "target":plan["target"],"entry_cost":cost})
                events.append({"type":"BUY_FILL","confirmed":True,"symbol":symbol,"at":at,"candle_open":sig["candle_open"]})
        for p in list(positions):
            b = intraday[p["symbol"]][at]
            hit = bar_exit(b,p["current_stop"],p["target"],c)
            if hit: close_position(p,hit[0],b.close_time+1,hit[1],hit[2])
        marks_end = {s:intraday[s][at].close for s in c["symbols"]}
        equity = cash+sum(p["remaining_qty"]*marks_end[p["symbol"]] for p in positions)
        peak = max(peak,equity); max_dd = max(max_dd,1-equity/peak)
        hard_halt = hard_halt or equity <= peak*(1-c["max_drawdown"])
        if is_round or (at+FIVE_MIN) % DAY == 0:
            curve.append({"known_at":at+FIVE_MIN,"equity":equity,"cash":cash,"positions":len(positions),"halted":hard_halt})
    opened = [{"symbol":p["symbol"],"quantity":p["remaining_qty"],"entry":p["entry"],
               "mark":intraday[p["symbol"]][finish-FIVE_MIN].close} for p in positions]
    return {"version":VERSION,"config":c,"config_hash":digest(c),"start":start,"end_exclusive":end,
            "initial_equity":c["paper_equity"],"final_equity":equity,"return":equity/c["paper_equity"]-1,
            "max_drawdown_sampled":max_dd,"hard_halt":hard_halt,"total":summary(trades),
            "by_setup":{str(k):summary([t for t in trades if t["setup"]==k]) for k in (1,2)},
            "trades":trades,"signals":signals_log,"equity_curve":curve,"open_positions_not_counted_as_wins":opened,
            "btc_buy_hold_gross_return":intraday["BTCUSDT"][finish-FIVE_MIN].close/intraday["BTCUSDT"][begin].open-1,
            "cash_benchmark_return":0,"analysis_times":c["analysis_times"],"timezone":c["timezone"],
            "limitations":["Universo fixo e custos hipotéticos; sem filtros históricos/filas/falhas OCO.",
                "Proteção simulada a mercado; stop primeiro se ambos tocarem dentro do mesmo candle 5m.",
                "Entrada/invalidação/ajuste de stop cinco minutos após cada rodada; latência real varia.",
                "Drawdown amostrado em 5m; posição final marcada sem custo de saída hipotética.",
                "Sem diário contemporâneo de IA, não mede o Grok; resultados v3.0 não validam v3.1."]}


def append_event(ledger_path, event_path):
    doc, event = read(ledger_path), read(event_path)
    proposed = doc["events"] + [event]
    replay(proposed)
    doc["events"] = proposed
    doc["account"]["reconciled_at"] = None
    save(ledger_path, doc)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--data", default="dados")
    f.add_argument("--ledger",default="registro.json")
    intr = sub.add_parser("fetch-intraday"); intr.add_argument("--data",default="dados"); intr.add_argument("--start",required=True); intr.add_argument("--end",required=True)
    run = sub.add_parser("run"); run.add_argument("--data",default="dados"); run.add_argument("--ledger",default="registro.json"); run.add_argument("--output")
    s = sub.add_parser("snapshot"); s.add_argument("--data", default="dados"); s.add_argument("--ledger", default="registro.json"); s.add_argument("--output", default="resultados/snapshot.json")
    v = sub.add_parser("preflight"); v.add_argument("--snapshot", required=True); v.add_argument("--signal-id", required=True); v.add_argument("--ledger", default="registro.json"); v.add_argument("--output", default="resultados/preflight.json")
    b = sub.add_parser("backtest"); b.add_argument("--data", default="dados"); b.add_argument("--start", required=True); b.add_argument("--end", required=True); b.add_argument("--output", required=True); b.add_argument("--stop-variant", choices=["fixed", "steps"]); b.add_argument("--vetoes")
    j = sub.add_parser("journal"); j.add_argument("--ledger", default="registro.json"); j.add_argument("--event", required=True)
    r = sub.add_parser("review-journal"); r.add_argument("--ledger", default="registro.json"); r.add_argument("--output", default="resultados/posicoes.json")
    args = p.parse_args()
    try:
        c = config(args.config)
        if args.command in ("fetch","run"):
            opened = [p["symbol"] for p in replay(read(args.ledger)["events"]) if p["status"] == "OPEN"]
            fetch_errors = []
            try: fetch(c,args.data,opened)
            except Exception as exc:
                if args.command == "fetch": raise
                fetch_errors.append(str(exc))
            if args.command == "run":
                out = snapshot(c,args.data,args.ledger,args.output)
                out["fetch_errors"] = fetch_errors
                save(out["output_file"],out)
                print(json.dumps({"output":out["output_file"],"round":out["round"],
                    "positions":len(out["position_reviews"]),"valid_candidates":len(out["currently_valid_candidates"]),
                    "errors":out["errors"],"fetch_errors":fetch_errors},ensure_ascii=False))
        elif args.command == "fetch-intraday": fetch_intraday(c,args.data,args.start,args.end)
        elif args.command == "snapshot":
            out = snapshot(c, args.data, args.ledger, args.output)
            print(json.dumps({"output": args.output, "candidates": len(out["technical_candidates"]), "errors": out["errors"]}, ensure_ascii=False))
        elif args.command == "preflight":
            print(json.dumps(preflight(c, args.snapshot, args.signal_id, args.ledger, args.output), ensure_ascii=False))
        elif args.command == "backtest":
            if args.stop_variant: c["stop_variant"] = args.stop_variant
            out = backtest(c, args.data, args.start, args.end, read(args.vetoes) if args.vetoes else None)
            save(args.output, out)
            print(json.dumps({k: out[k] for k in ("return", "max_drawdown_sampled", "total", "hard_halt")}, ensure_ascii=False))
        elif args.command == "journal": append_event(args.ledger, args.event)
        elif args.command == "review-journal": save(args.output, replay(read(args.ledger)["events"]))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"status": "BLOQUEADO", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
