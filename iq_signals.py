"""
Multi-Strategy Profitable Trading Bot:
  1) V-Reversals: Sharp impulse moves + bounce/pullback.
  2) Liquidity Sweeps (Stop Hunts): Institutional wicks past swing highs/lows + rejection.
  3) Momentum Breakouts: Strong body candles breaking 15-candle ranges.
  4) Rejection Pin Bars: Hammer / Shooting star rejections at local extremes.
  5) MT5 Gold Breakouts: Hourly 8h range breakouts with RSI confirmation.

Pairs: EUR/USD, GBP/USD, USD/JPY, XAU/USD (Gold).
Weekdays = BLITZ (Twelve Data) | Weekends = OTC (IQ Option).
"""
import os, sys, time, requests
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ.get("TWELVE_DATA_KEY", "")
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ENV = os.environ.get("TELEGRAM_CHAT_ID", "")
CHATS = [c.strip() for c in CHAT_ENV.split(",") if c.strip()]

PAIRS = {
    "EUR/USD": {"td": "EUR/USD", "iq": "EURUSD-OTC", "pip": 0.0001, "move": 5, "bounce": 1.5},
    "GBP/USD": {"td": "GBP/USD", "iq": "GBPUSD-OTC", "pip": 0.0001, "move": 5, "bounce": 1.5},
    "USD/JPY": {"td": "USD/JPY", "iq": "USDJPY-OTC", "pip": 0.01,   "move": 5, "bounce": 1.5},
    "XAU/USD": {"td": "XAU/USD", "iq": "XAUUSD-OTC", "pip": 0.10,   "move": 10, "bounce": 2.5},
}
MAX_RETRACE = 0.65
EXPIRY = "5 min"
COOLDOWN = 300
TZ = ZoneInfo("Africa/Lagos")
last_alert = {}

# --- MT5 Gold breakout settings ---
MT5_GOLD_COOLDOWN = 3600   
MT5_STOP_PCT = 0.15        
MT5_RR = 2                 
mt5_last_alert = 0


def mode_today():
    return "BLITZ" if datetime.now(TZ).weekday() < 5 else "OTC"


iq = None


def iq_candles(cfg):
    """Weekend OTC prices straight from IQ Option."""
    global iq
    if iq is None:
        from iqoptionapi.stable_api import IQ_Option
        email = os.environ.get("IQ_EMAIL", "")
        password = os.environ.get("IQ_PASSWORD", "")
        api = IQ_Option(email, password)
        ok, reason = api.connect()
        if not ok:
            raise RuntimeError(f"IQ login failed: {reason}")
        api.change_balance("PRACTICE")
        iq = api
    raw = iq.get_candles(cfg["iq"], 60, 45, time.time())
    if not raw:
        iq = None
        raise RuntimeError("IQ returned no candles")
    return [{"open": x["open"], "high": x["max"], "low": x["min"], "close": x["close"]}
            for x in raw][:-1]


def get_candles(mode, cfg):
    if mode == "OTC":
        return iq_candles(cfg)
    resp = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": cfg["td"], "interval": "1min", "outputsize": 45, "apikey": KEY},
        timeout=15).json()
    if "values" not in resp:
        raise RuntimeError(f"Twelve Data error for {cfg['td']}: {resp}")
    v = resp["values"][::-1]
    return [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in v][:-1]


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


def detect_strategies(c, cfg):
    """Scans for multiple profitable setups: V-Reversal, Liquidity Sweep, Momentum Breakout, and Pin Bar."""
    if len(c) < 35:
        return None

    closes = [x["close"] for x in c]
    current_rsi = rsi(closes, 14)
    PIP = cfg["pip"]
    MIN_MOVE_PIPS, MIN_BOUNCE_PIPS = cfg["move"], cfg["bounce"]
    
    r = c[-18:]
    n = len(r)
    current = c[-1]
    prev = c[-2]

    # --- STRATEGY 1: V-REVERSAL ---
    i = min(range(n), key=lambda k: r[k]["low"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        low, top = r[i]["low"], max(x["high"] for x in r[:i])
        drop, bounce = (top - low) / PIP, (r[-1]["close"] - low) / PIP
        if (drop >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= bounce <= MAX_RETRACE * drop
                and r[-1]["close"] > r[-1]["open"] and current_rsi <= 45):
            return "HIGHER", "⚡ V-REVERSAL (Support Bounce)", f"Dropped {drop:.1f}p, bounced {bounce:.1f}p | RSI: {current_rsi:.0f}"

    i = max(range(n), key=lambda k: r[k]["high"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        high, bot = r[i]["high"], min(x["low"] for x in r[:i])
        rise, pull = (high - bot) / PIP, (high - r[-1]["close"]) / PIP
        if (rise >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= pull <= MAX_RETRACE * rise
                and r[-1]["close"] < r[-1]["open"] and current_rsi >= 55):
            return "LOWER", "⚡ V-REVERSAL (Resistance Pullback)", f"Rallied {rise:.1f}p, pulled back {pull:.1f}p | RSI: {current_rsi:.0f}"

    # --- STRATEGY 2: LIQUIDITY SWEEP / STOP HUNT ---
    lookback = c[-30:-1]
    hi = max(x["high"] for x in lookback)
    lo = min(x["low"] for x in lookback)

    if current["high"] > hi and current["close"] < hi and current["close"] < current["open"]:
        sweep_pips = (current["high"] - hi) / PIP
        if sweep_pips >= 1.0 and current_rsi >= 50:
            return "LOWER", "🌊 LIQUIDITY SWEEP (Bearish Stop Hunt)", f"Grabbed buy-stops above {hi:.4f} by {sweep_pips:.1f}p & rejected | RSI: {current_rsi:.0f}"

    if current["low"] < lo and current["close"] > lo and current["close"] > current["open"]:
        sweep_pips = (lo - current["low"]) / PIP
        if sweep_pips >= 1.0 and current_rsi <= 50:
            return "HIGHER", "🌊 LIQUIDITY SWEEP (Bullish Stop Hunt)", f"Grabbed sell-stops below {lo:.4f} by {sweep_pips:.1f}p & rejected | RSI: {current_rsi:.0f}"

    # --- STRATEGY 3: MOMENTUM BREAKOUT ---
    range_hi = max(x["high"] for x in c[-15:-2])
    range_lo = min(x["low"] for x in c[-15:-2])
    body_size = abs(current["close"] - current["open"]) / PIP

    if current["close"] > range_hi and body_size >= 3.0 and current["close"] > current["open"] and current_rsi > 55:
        return "HIGHER", "🚀 MOMENTUM BREAKOUT (Bullish)", f"Strong expansion candle broke 15-candle high | Body: {body_size:.1f}p | RSI: {current_rsi:.0f}"

    if current["close"] < range_lo and body_size >= 3.0 and current["close"] < current["open"] and current_rsi < 45:
        return "LOWER", "🚀 MOMENTUM BREAKOUT (Bearish)", f"Strong expansion candle broke 15-candle low | Body: {body_size:.1f}p | RSI: {current_rsi:.0f}"

    # --- STRATEGY 4: REJECTION PIN BAR ---
    total_range = current["high"] - current["low"]
    if total_range > 0:
        upper_wick = current["high"] - max(current["open"], current["close"])
        lower_wick = min(current["open"], current["close"]) - current["low"]
        
        # Bullish Hammer Pin Bar at local low
        if lower_wick >= (total_range * 0.6) and current["close"] > current["open"] and current_rsi < 40:
            return "HIGHER", "📌 REJECTION PIN BAR (Bullish Hammer)", f"Long lower rejection wick at local low | RSI: {current_rsi:.0f}"
            
        # Bearish Shooting Star Pin Bar at local high
        if upper_wick >= (total_range * 0.6) and current["close"] < current["open"] and current_rsi > 60:
            return "LOWER", "📌 REJECTION PIN BAR (Bearish Shooting Star)", f"Long upper rejection wick at local high | RSI: {current_rsi:.0f}"

    return None


def ping(text):
    for chat in CHATS:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "Markdown"}, timeout=15)
        except Exception as e:
            print("telegram error:", e)


def mt5_gold_check():
    global mt5_last_alert
    if time.time() - mt5_last_alert < MT5_GOLD_COOLDOWN:
        return
    try:
        resp = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": "XAU/USD", "interval": "1h", "outputsize": 30, "apikey": KEY},
            timeout=15).json()
        if "values" not in resp:
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
            f"📊 *MT5 GOLD — SELL BREAKOUT* 📊\n\n"
            f"Closed below 8h low (`{lo8:.2f}`) at `{entry:.2f}`\n"
            f"• **Entry ~**: `{entry:.2f}`\n"
            f"• **Stop**: `{stop:.2f}`\n"
            f"• **Target**: `{target:.2f}`\n"
            f"• **RSI Momentum**: `{r:.0f}`\n\n"
            f"_⚠️ High probability breakout setup._"
        )
    elif current["close"] > hi8 and r > 55:
        entry = current["close"]
        stop = entry - rng * MT5_STOP_PCT
        target = entry + (entry - stop) * MT5_RR
        mt5_last_alert = time.time()
        ping(
            f"📊 *MT5 GOLD — BUY BREAKOUT* 📊\n\n"
            f"Closed above 8h high (`{hi8:.2f}`) at `{entry:.2f}`\n"
            f"• **Entry ~**: `{entry:.2f}`\n"
            f"• **Stop**: `{stop:.2f}`\n"
            f"• **Target**: `{target:.2f}`\n"
            f"• **RSI Momentum**: `{r:.0f}`\n\n"
            f"_⚠️ High probability breakout setup._"
        )


def check():
    mode = mode_today()
    for sym, cfg in PAIRS.items():
        try:
            candles = get_candles(mode, cfg)
        except Exception as e:
            print(f"error fetching {sym}: {e}")
            continue

        setup = detect_strategies(candles, cfg)
        if setup and time.time() - last_alert.get(sym, 0) >= COOLDOWN:
            last_alert[sym] = time.time()
            head = "⚡ *BLITZ PROFITABLE SIGNAL* ⚡" if mode == "BLITZ" else "🕑 *OTC PROFITABLE SIGNAL* 🕑"
            arrow = "🟢 *HIGHER (BUY)*" if setup[0] == "HIGHER" else "🔴 *LOWER (SELL)*"
            
            ping(
                f"{head}\n\n"
                f"📊 **Asset:** `{sym}`\n"
                f"🎯 **Direction:** {arrow}\n"
                f"🛡️ **Strategy:** *{setup[1]}*\n"
                f"⏱️ **Timeframe:** `1m` | **Expiry:** `{EXPIRY}`\n"
                f"📈 **Analysis:** {setup[2]}\n\n"
                f"_🚀 Multi-strategy setup confirmed. Check chart & execute!_"
            )

    # Check MT5 Gold Breakouts (Weekdays only)
    if mode == "BLITZ":
        try:
            mt5_gold_check()
        except Exception as e:
            print("error mt5 gold", e)


if __name__ == "__main__":
    if "--test" in sys.argv:
        ping("✅ *Test Broadcast:* Multi-Strategy Bot is online and scanning all profitable setups!")
        sys.exit(0)
    deadline = time.time() + int(sys.argv[sys.argv.index("--for") + 1]) if "--for" in sys.argv else None
    while deadline is None or time.time() < deadline:
        try:
            check()
        except Exception as e:
            print("error:", e)
        time.sleep(60)

