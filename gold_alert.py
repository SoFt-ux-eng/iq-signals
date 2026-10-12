"""
Gold (XAU/USD) alert for MT5 trading.
Strategy: trend pullback. 4H + 1H trend agree, price pulls back to the
1H 50 EMA, then a candle closes back through the 20 EMA.
Uses CLOSED candles only. Sends a Telegram alert. Does not touch the
IQ Option bot.

Run normally:   python gold_alert.py
Status message: python gold_alert.py --test
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

SYMBOL = "XAU/USD"
API = "https://api.twelvedata.com/time_series"

KEY = os.environ.get("TWELVE_DATA_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

# Strategy settings (same as the MT5 bot)
EMA_FAST, EMA_MID, EMA_SLOW = 20, 50, 200
ATR_PERIOD = 14
PULLBACK_LOOKBACK = 5
PULLBACK_TOL_ATR = 0.3
SWING_LOOKBACK = 5
SL_ATR_BUFFER = 0.3
RR = 2.0
MIN_SL_ATR, MAX_SL_ATR = 0.3, 3.0   # skip silly stop sizes
FRESH_MINUTES = 50                   # only alert on a candle that just closed
STALE_HOURS = 2                      # market closed / data stale -> skip


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC] {msg}", flush=True)


def fetch(interval, length):
    """Return closed candles as (time, open, high, low, close), oldest first."""
    r = requests.get(
        API,
        params={
            "symbol": SYMBOL,
            "interval": interval,
            "outputsize": 400,
            "timezone": "UTC",
            "apikey": KEY,
        },
        timeout=30,
    )
    data = r.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(f"Twelve Data error ({interval}): {data.get('message', data)}")

    rows = []
    for v in data["values"]:
        t = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        o, h, l, c = (float(v[k]) for k in ("open", "high", "low", "close"))
        if min(o, h, l, c) <= 0 or h < l:
            raise RuntimeError(f"Bad candle data at {v['datetime']} ({interval})")
        rows.append((t, o, h, l, c))

    rows.sort(key=lambda x: x[0])
    now = datetime.now(timezone.utc)
    rows = [x for x in rows if x[0] + length <= now]  # drop the candle still forming
    return rows


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr(rows, n):
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i][2], rows[i][3], rows[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = [None] * len(rows)
    if len(trs) < n:
        return out
    a = sum(trs[:n]) / n
    out[n] = a
    for i in range(n + 1, len(rows)):
        a = (a * (n - 1) + trs[i - 1]) / n
        out[i] = a
    return out


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        raise RuntimeError("Telegram secrets missing")
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": text},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text}")


def analyse():
    h1 = fetch("1h", timedelta(hours=1))
    h4 = fetch("4h", timedelta(hours=4))
    if len(h1) < EMA_SLOW + 20 or len(h4) < EMA_SLOW + 5:
        raise RuntimeError(f"Not enough candles (1h={len(h1)}, 4h={len(h4)})")

    now = datetime.now(timezone.utc)
    last_close_time = h1[-1][0] + timedelta(hours=1)
    age = now - last_close_time
    if age > timedelta(hours=STALE_HOURS):
        return {"status": "stale", "age": age}

    closes = [r[4] for r in h1]
    lows = [r[3] for r in h1]
    highs = [r[2] for r in h1]
    e20, e50, e200 = ema(closes, EMA_FAST), ema(closes, EMA_MID), ema(closes, EMA_SLOW)
    a = atr(h1, ATR_PERIOD)
    t_closes = [r[4] for r in h4]
    t50, t200 = ema(t_closes, EMA_MID), ema(t_closes, EMA_SLOW)

    i = len(h1) - 1
    needed = [e20[i], e20[i - 1], e50[i], e200[i], a[i], t50[-1], t200[-1]]
    if any(x is None for x in needed):
        raise RuntimeError("Indicator values missing")

    up = e50[i] > e200[i] and closes[i] > e200[i] and t50[-1] > t200[-1]
    down = e50[i] < e200[i] and closes[i] < e200[i] and t50[-1] < t200[-1]
    tol = a[i] * PULLBACK_TOL_ATR
    rng = range(i - PULLBACK_LOOKBACK + 1, i + 1)

    result = {
        "status": "ok",
        "price": closes[i],
        "trend": "UP" if up else "DOWN" if down else "NONE",
        "fresh": age <= timedelta(minutes=FRESH_MINUTES),
        "signal": None,
        "candle_time": last_close_time,
    }

    if up:
        pulled = any(lows[j] <= e50[j] + tol for j in rng)
        trigger = closes[i] > e20[i] and closes[i - 1] <= e20[i - 1]
        if pulled and trigger:
            entry = closes[i]
            sl = min(lows[i - SWING_LOOKBACK + 1: i + 1]) - a[i] * SL_ATR_BUFFER
            dist = entry - sl
            if MIN_SL_ATR * a[i] <= dist <= MAX_SL_ATR * a[i]:
                result["signal"] = ("BUY", entry, sl, entry + dist * RR, dist)

    if down:
        pulled = any(highs[j] >= e50[j] - tol for j in rng)
        trigger = closes[i] < e20[i] and closes[i - 1] >= e20[i - 1]
        if pulled and trigger:
            entry = closes[i]
            sl = max(highs[i - SWING_LOOKBACK + 1: i + 1]) + a[i] * SL_ATR_BUFFER
            dist = sl - entry
            if MIN_SL_ATR * a[i] <= dist <= MAX_SL_ATR * a[i]:
                result["signal"] = ("SELL", entry, sl, entry - dist * RR, dist)

    return result


def main():
    if not KEY:
        raise RuntimeError("TWELVE_DATA_KEY missing")

    test = "--test" in sys.argv
    res = analyse()

    if res["status"] == "stale":
        log(f"Market closed or data stale (last candle closed {res['age']} ago). Skipping.")
        if test:
            send_telegram("Gold alert bot is connected, but gold market looks closed right now (no fresh candles).")
        return

    log(f"Price {res['price']:.2f} | trend {res['trend']} | signal {res['signal']}")

    if test:
        send_telegram(
            "Gold alert bot is connected and working.\n"
            f"Gold price: {res['price']:.2f}\n"
            f"Current trend: {res['trend']}\n"
            "You'll get an alert here when a setup appears."
        )
        return

    if res["signal"] and res["fresh"]:
        side, entry, sl, tp, dist = res["signal"]
        skip_if = dist * 0.25
        send_telegram(
            f"GOLD {side} (MT5)\n"
            f"Timeframe: 1H, 4H trend agrees\n"
            f"Entry: about {entry:.2f} (market)\n"
            f"Stop loss: {sl:.2f}\n"
            f"Take profit: {tp:.2f}\n"
            f"Risk: {dist:.2f} points | Reward: 2x\n\n"
            f"Skip it if price has already moved more than {skip_if:.2f} from the entry."
        )
        log("Alert sent.")
    elif res["signal"]:
        log("Signal found but candle is not fresh. Not sending.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # data problems never produce a signal
        log(f"ERROR: {e}")
        sys.exit(1)
