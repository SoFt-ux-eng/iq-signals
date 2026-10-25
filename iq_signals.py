"""
Master Multi-Strategy Trading Bot (2 PM - 6 PM WAT Window)
- Complete version with ALL strategies: Engulfing, Pin Bars, Liquidity Sweeps, 
  Momentum Breakouts, V-Reversals, and MT5 Gold Breakouts.
- Built-in token fallback and explicit error logs to prevent silent failures.
"""
import os
import sys
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# --- Configurations & Secrets ---
KEY = os.environ.get("TWELVE_DATA_KEY", "")
# Automatically checks both secret names so it never fails due to naming mismatches
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ENV = os.environ.get("TELEGRAM_CHAT_ID", "")
CHATS = [c.strip() for c in CHAT_ENV.split(",") if c.strip()]

WEEKDAY_PAIRS = {
    "EUR/USD": {"td": "EUR/USD", "pip": 0.0001, "move": 4, "bounce": 1.2},
    "GBP/USD": {"td": "GBP/USD", "pip": 0.0001, "move": 4, "bounce": 1.2},
    "USD/JPY": {"td": "USD/JPY", "pip": 0.01,   "move": 4, "bounce": 1.2},
    "XAU/USD": {"td": "XAU/USD", "pip": 0.10,   "move": 8, "bounce": 2.0},
}

WEEKEND_OTC_PAIRS = {
    "EUR/USD OTC": {"iq": "EURUSD-OTC", "pip": 0.0001, "move": 4, "bounce": 1.2},
    "GBP/USD OTC": {"iq": "GBPUSD-OTC", "pip": 0.0001, "move": 4, "bounce": 1.2},
    "USD/JPY OTC": {"iq": "USDJPY-OTC", "pip": 0.01,   "move": 4, "bounce": 1.2},
    "Gold OTC":    {"iq": "XAUUSD-OTC", "pip": 0.10,   "move": 8, "bounce": 2.0},
}

EXPIRY = "5 min"
COOLDOWN = 240  # 4-minute cooldown between signals per asset
TZ = ZoneInfo("Africa/Lagos")
last_alert = {}

# --- MT5 Gold settings ---
MT5_GOLD_COOLDOWN = 1800
MT5_STOP_PCT = 0.15
MT5_RR = 2
mt5_last_alert = 0


def mode_today():
    return "WEEKDAY" if datetime.now(TZ).weekday() < 5 else "OTC"


def is_liquidity_time():
    """Returns True only during the peak liquidity window: 2:00 PM to 6:00 PM WAT."""
    now = datetime.now(TZ)
    return 14 <= now.hour < 18


iq = None


def iq_candles(cfg):
    global iq
    if not TOKEN:
        print("Warning: Telegram Token is missing!")
    if iq is None:
        try:
            from iqoptionapi.stable_api import IQ_Option
            email = os.environ.get("IQ_EMAIL", "")
            password = os.environ.get("IQ_PASSWORD", "")
            api = IQ_Option(email, password)
            ok, reason = api.connect()
            if not ok:
                print(f"IQ Option connection failed: {reason}")
                return None
            api.change_balance("PRACTICE")
            iq = api
        except Exception as e:
            print(f"IQ Option initialization error: {e}")
            return None
    
    try:
        raw = iq.get_candles(cfg["iq"], 60, 40, time.time())
        if not raw:
            print(f"IQ Option returned no candles for {cfg['iq']}")
            iq = None
            return None
        return [{"open": x["open"], "high": x["max"], "low": x["min"], "close": x["close"]} for x in raw][:-1]
    except Exception as e:
        print(f"IQ Option candle fetch error for {cfg['iq']}: {e}")
        iq = None
        return None


def get_candles(mode, cfg):
    if mode == "OTC":
        return iq_candles(cfg)
    
    try:
        resp = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": cfg["td"], "interval": "1min", "outputsize": 40, "apikey": KEY},
            timeout=15).json()
        if "values" not in resp:
            print(f"Twelve Data error for {cfg['td']}: {resp}")
            return None
        v = resp["values"][::-1]
        return [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in v][:-1]
    except Exception as e:
        print(f"Twelve Data request exception for {cfg['td']}: {e}")
        return None


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, period + 1)]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, period + 1)]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    for i in range(period + 1, len(closes)):
        gain = max(closes[i] - closes[i - 1], 0)
        loss = max(closes[i - 1] - closes[i], 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def detect_all_profitable_setups(c, cfg):
    """Scans for ALL major profitable setups: Engulfing, Pin Bars, Liquidity Sweeps, V-Reversals, and Momentum Breakouts."""
    if not c or len(c) < 30:
        return None

    closes = [x["close"] for x in c]
    current_rsi = rsi(closes, 14)
    PIP = cfg["pip"]

    current = c[-1]
    prev = c[-2]

    # 1. BULLISH / BEARISH ENGULFING PATTERN
    if current["close"] > current["open"] and prev["close"] < prev["open"]:
        if current["close"] >= prev["open"] and current["open"] <= prev["close"] and current_rsi < 48:
            return "HIGHER", "🔥 ENGULFING PATTERN (Bullish Reversal)", f"Strong bullish engulfing candle | RSI: {current_rsi:.0f}"

    if current["close"] < current["open"] and prev["close"] > prev["open"]:
        if current["close"] <= prev["open"] and current["open"] >= prev["close"] and current_rsi > 52:
            return "LOWER", "🔥 ENGULFING PATTERN (Bearish Reversal)", f"Strong bearish engulfing candle | RSI: {current_rsi:.0f}"

    # 2. REJECTION PIN BAR / HAMMER / SHOOTING STAR
    total_range = current["high"] - current["low"]
    if total_range > 0:
        upper_wick = current["high"] - max(current["open"], current["close"])
        lower_wick = min(current["open"], current["close"]) - current["low"]

        if lower_wick >= (total_range * 0.6) and current["close"] > current["open"] and current_rsi < 42:
            return "HIGHER", "📌 PIN BAR / HAMMER (Bullish Rejection)", f"Long lower wick rejection at support | RSI: {current_rsi:.0f}"
        if upper_wick >= (total_range * 0.6) and current["close"] < current["open"] and current_rsi > 58:
            return "LOWER", "📌 PIN BAR / SHOOTING STAR (Bearish Rejection)", f"Long upper wick rejection at resistance | RSI: {current_rsi:.0f}"

    # 3. INSTITUTIONAL LIQUIDITY SWEEP (STOP HUNT)
    lookback = c[-25:-1]
    hi = max(x["high"] for x in lookback)
    lo = min(x["low"] for x in lookback)

    if current["high"] > hi and current["close"] < hi and current["close"] < current["open"]:
        sweep_pips = (current["high"] - hi) / PIP
        if sweep_pips >= 0.8 and current_rsi >= 50:
            return "LOWER", "🌊 LIQUIDITY SWEEP (Stop Hunt)", f"Grabbed buy-stops above {hi:.4f} by {sweep_pips:.1f}p & rejected | RSI: {current_rsi:.0f}"

    if current["low"] < lo and current["close"] > lo and current["close"] > current["open"]:
        sweep_pips = (lo - current["low"]) / PIP
        if sweep_pips >= 0.8 and current_rsi <= 50:
            return "HIGHER", "🌊 LIQUIDITY SWEEP (Stop Hunt)", f"Grabbed sell-stops below {lo:.4f} by {sweep_pips:.1f}p & rejected | RSI: {current_rsi:.0f}"

    # 4. MOMENTUM RANGE BREAKOUT
    range_hi = max(x["high"] for x in c[-15:-2])
    range_lo = min(x["low"] for x in c[-15:-2])
    body_size = abs(current["close"] - current["open"]) / PIP

    if current["close"] > range_hi and body_size >= 2.5 and current["close"] > current["open"] and current_rsi > 55:
        return "HIGHER", "🚀 MOMENTUM BREAKOUT (Bullish)", f"Expansion candle broke 15-candle high | Body: {body_size:.1f}p | RSI: {current_rsi:.0f}"
    if current["close"] < range_lo and body_size >= 2.5 and current["close"] < current["open"] and current_rsi < 45:
        return "LOWER", "🚀 MOMENTUM BREAKOUT (Bearish)", f"Expansion candle broke 15-candle low | Body: {body_size:.1f}p | RSI: {current_rsi:.0f}"

    # 5. V-REVERSALS
    r = c[-15:]
    n = len(r)
    i_low = min(range(n), key=lambda k: r[k]["low"])
    if i_low >= 2 and 1 <= n - 1 - i_low <= 6:
        low_val, top_val = r[i_low]["low"], max(x["high"] for x in r[:i_low])
        drop, bounce = (top_val - low_val) / PIP, (r[-1]["close"] - low_val) / PIP
        if drop >= cfg["move"] and cfg["bounce"] <= bounce <= 0.7 * drop and r[-1]["close"] > r[-1]["open"] and current_rsi <= 45:
            return "HIGHER", "⚡ V-REVERSAL (Support Bounce)", f"Dropped {drop:.1f}p, bounced {bounce:.1f}p | RSI: {current_rsi:.0f}"

    i_high = max(range(n), key=lambda k: r[k]["high"])
    if i_high >= 2 and 1 <= n - 1 - i_high <= 6:
        high_val, bot_val = r[i_high]["high"], min(x["low"] for x in r[:i_high])
        rise, pull = (high_val - bot_val) / PIP, (high_val - r[-1]["close"]) / PIP
        if rise >= cfg["move"] and cfg["bounce"] <= pull <= 0.7 * rise and r[-1]["close"] < r[-1]["open"] and current_rsi >= 55:
            return "LOWER", "⚡ V-REVERSAL (Resistance Pullback)", f"Rallied {rise:.1f}p, pulled back {pull:.1f}p | RSI: {current_rsi:.0f}"

    return None


def ping(text):
    if not TOKEN:
        print("Telegram broadcast failed: TOKEN is empty.")
        return
    for chat in CHATS:
        try:
            resp = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "Markdown"}, timeout=15)
            if not resp.json().get("ok"):
                print(f"Telegram API response error: {resp.text}")
        except Exception as e:
            print("Telegram exception error:", e)


def mt5_gold_check():
    global mt5_last_alert
    if time.time() - mt5_last_alert < MT5_GOLD_COOLDOWN:
        return
    try:
        resp = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": "XAU/USD", "interval": "1h", "outputsize": 30, "apikey": KEY},
            timeout=15).json()
        if "values" not in resp:
            print("mt5 gold error:", resp)
            return
        v = resp["values"][::-1]
        candles = [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in v]
    except Exception as e:
        print("mt5 gold error:", e)
        return
    if len(candles) < 10:
        return

    closes = [c["close"] for c in candles]
    current = candles[-1]
    lookback = candles[-9:-1]
    hi8 = max(c["high"] for c in lookback)
    lo8 = min(c["low"] for c in lookback)
    r = rsi(closes[:-1], 14)
    if r is None:
        return
    rng = hi8 - lo8

    if current["close"] < lo8 and r < 45:
        entry = current["close"]
        stop = entry + rng * MT5_STOP_PCT
        target = entry - (stop - entry) * MT5_RR
        mt5_last_alert = time.time()
        ping(
            f"📊 *MT5 GOLD — PROFITABLE SELL BREAKOUT* 📊\n\n"
            f"Closed below 8h low (`{lo8:.2f}`) at `{entry:.2f}`\n"
            f"• **Entry ~**: `{entry:.2f}`\n"
            f"• **Stop**: `{stop:.2f}`\n"
            f"• **Target**: `{target:.2f}`\n"
            f"• **RSI Momentum**: `{r:.0f}`\n\n"
            f"_⚠️ High-probability institutional setup._"
        )
    elif current["close"] > hi8 and r > 55:
        entry = current["close"]
        stop = entry - rng * MT5_STOP_PCT
        target = entry + (entry - stop) * MT5_RR
        mt5_last_alert = time.time()
        ping(
            f"📊 *MT5 GOLD — PROFITABLE BUY BREAKOUT* 📊\n\n"
            f"Closed above 8h high (`{hi8:.2f}`) at `{entry:.2f}`\n"
            f"• **Entry ~**: `{entry:.2f}`\n"
            f"• **Stop**: `{stop:.2f}`\n"
            f"• **Target**: `{target:.2f}`\n"
            f"• **RSI Momentum**: `{r:.0f}`\n\n"
            f"_⚠️ High-probability institutional setup._"
        )


def check():
    if not is_liquidity_time():
        print("Outside liquidity window (2 PM - 6 PM WAT). Skipping scan.")
        return

    mode = mode_today()
    pairs_to_scan = WEEKDAY_PAIRS if mode == "WEEKDAY" else WEEKEND_OTC_PAIRS

    for sym, cfg in pairs_to_scan.items():
        candles = get_candles(mode, cfg)
        if not candles:
            continue

        setup = detect_all_profitable_setups(candles, cfg)
        if setup and time.time() - last_alert.get(sym, 0) >= COOLDOWN:
            last_alert[sym] = time.time()
            head = "⚡ *LIVE MARKET PROFITABLE SIGNAL* ⚡" if mode == "WEEKDAY" else "🕑 *OTC PROFITABLE SIGNAL* 🕑"
            arrow = "🟢 *HIGHER (BUY)*" if setup[0] == "HIGHER" else "🔴 *LOWER (SELL)*"

            ping(
                f"{head}\n\n"
                f"📊 **Asset:** `{sym}`\n"
                f"🎯 **Direction:** {arrow}\n"
                f"🛡️ **Pattern:** *{setup[1]}*\n"
                f"⏱️ **Timeframe:** `1m` | **Expiry:** `{EXPIRY}`\n"
                f"📈 **Analysis:** {setup[2]}\n\n"
                f"_🚀 Jump in and execute!_"
            )

    # Check MT5 Gold Hourly Breakouts during weekdays within liquidity hours
    if mode == "WEEKDAY":
        try:
            mt5_gold_check()
        except Exception as e:
            print("error mt5 gold", e)


if __name__ == "__main__":
    if "--test" in sys.argv:
        ping("✅ *Test Broadcast:* Master Bot has been fully updated with all strategies and is active!")
        sys.exit(0)
    try:
        check()
    except Exception as e:
        print("Fatal runtime error:", e)

