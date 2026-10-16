
"""
Combined signal bot:
  1) V-reversal: selloff -> bottom -> green bounce => HIGHER
                 rally -> top -> red pullback      => LOWER
     1m candles, 5 min expiry. Weekdays = BLITZ (Twelve Data),
     weekends = OTC (IQ Option, direct).
     Pairs: EUR/USD, GBP/USD, USD/JPY, XAU/USD.

  2) MT5 GOLD breakout: XAU/USD closes beyond its own 8h high/low,
     with RSI(14) for context. Weekdays only (same hours gold/forex
     markets are actually open). Independent cooldown from (1).

Env required: TWELVE_DATA_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
              IQ_EMAIL, IQ_PASSWORD
Run: python iq_signals.py [--for SECONDS]
"""
import os, sys, time, requests
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ["TWELVE_DATA_KEY"]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHATS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",")]

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
MT5_GOLD_COOLDOWN = 3600   # don't repeat the same breakout call more than once/hour
MT5_STOP_PCT = 0.15        # stop = 15% of the 8h range beyond entry (tune this)
MT5_RR = 2                 # target = 2x the stop distance
mt5_last_alert = 0


def mode_today():
    return "BLITZ" if datetime.now(TZ).weekday() < 5 else "OTC"


iq = None


def iq_candles(cfg):
    """Weekend OTC prices straight from IQ Option (unofficial library, read-only use)."""
    global iq
    if iq is None:
        from iqoptionapi.stable_api import IQ_Option
        api = IQ_Option(os.environ["IQ_EMAIL"], os.environ["IQ_PASSWORD"])
        ok, reason = api.connect()
        if not ok:
            raise RuntimeError(f"IQ login failed: {reason}")
        api.change_balance("PRACTICE")
        iq = api
    raw = iq.get_candles(cfg["iq"], 60, 35, time.time())
    if not raw:
        iq = None
        raise RuntimeError("IQ returned no candles")
    return [{"open": x["open"], "high": x["max"], "low": x["min"], "close": x["close"]}
            for x in raw][:-1]


def get_candles(mode, cfg):
    if mode == "OTC":
        return iq_candles(cfg)
    v = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": cfg["td"], "interval": "1min", "outputsize": 35, "apikey": KEY},
        timeout=15).json()["values"][::-1]
    return [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in v][:-1]


def detect(c, cfg):
    PIP, MIN_MOVE_PIPS, MIN_BOUNCE_PIPS = cfg["pip"], cfg["move"], cfg["bounce"]
    r = c[-18:]
    n = len(r)

    i = min(range(n), key=lambda k: r[k]["low"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        low, top = r[i]["low"], max(x["high"] for x in r[:i])
        drop, bounce = (top - low) / PIP, (r[-1]["close"] - low) / PIP
        if (drop >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= bounce <= MAX_RETRACE * drop
                and r[-1]["close"] > r[-1]["open"]):
            return "HIGHER", f"dropped {drop:.1f}p, bounced {bounce:.1f}p"

    i = max(range(n), key=lambda k: r[k]["high"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        high, bot = r[i]["high"], min(x["low"] for x in r[:i])
        rise, pull = (high - bot) / PIP, (high - r[-1]["close"]) / PIP
        if (rise >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= pull <= MAX_RETRACE * rise
                and r[-1]["close"] < r[-1]["open"]):
            return "LOWER", f"rallied {rise:.1f}p, pulled back {pull:.1f}p"

    return None


def rsi(closes, period=14):
    """Standard Wilder's-smoothing RSI."""
    if len(closes) < period + 1:
        return None
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


def ping(text):
    for chat in CHATS:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=15)
        except Exception as e:
            print("telegram error:", e)


def mt5_gold_check():
    global mt5_last_alert
    if time.time() - mt5_last_alert < MT5_GOLD_COOLDOWN:
        return
    try:
        v = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": "XAU/USD", "interval": "1h", "outputsize": 30, "apikey": KEY},
            timeout=15).json()["values"][::-1]
        candles = [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in v]
    except Exception as e:
        print("mt5 gold error:", e)
        return
    if len(candles) < 10:
        return

    closes = [c["close"] for c in candles]
    current = candles[-1]
    lookback = candles[-9:-1]          # last 8 completed hours, excludes the forming one
    hi8 = max(c["high"] for c in lookback)
    lo8 = min(c["low"] for c in lookback)
    r = rsi(closes[:-1], 14)
    if r is None:
        return
    rng = hi8 - lo8

    if current["close"] < lo8:
        entry = current["close"]
        stop = entry + rng * MT5_STOP_PCT
        target = entry - (stop - entry) * MT5_RR
        mt5_last_alert = time.time()
        ping(f"📊 MT5 GOLD — SELL setup\nClosed below the 8h low ({lo8:.2f}) at {entry:.2f}\n"
             f"Entry ~{entry:.2f}\nStop {stop:.2f}\nTarget {target:.2f}\nRSI {r:.0f}\n\n"
             f"Confirm before entering.")
    elif current["close"] > hi8:
        entry = current["close"]
        stop = entry - rng * MT5_STOP_PCT
        target = entry + (entry - stop) * MT5_RR
        mt5_last_alert = time.time()
        ping(f"📊 MT5 GOLD — BUY setup\nClosed above the 8h high ({hi8:.2f}) at {entry:.2f}\n"
             f"Entry ~{entry:.2f}\nStop {stop:.2f}\nTarget {target:.2f}\nRSI {r:.0f}\n\n"
             f"Confirm before entering.")


def check():
    mode = mode_today()
    for sym, cfg in PAIRS.items():
        try:
            sig = detect(get_candles(mode, cfg), cfg)
        except Exception as e:
            print("error", sym, e)
            continue
        if not sig or time.time() - last_alert.get(sym, 0) < COOLDOWN:
            continue
        last_alert[sym] = time.time()
        head = "⚡⚡ BLITZ SIGNAL ⚡⚡\nBLITZ ONLY - NOT OTC" if mode == "BLITZ" else "🕑🕑 OTC SIGNAL 🕑🕑\nOTC ONLY - NOT BLITZ"
        arrow = "🟢 HIGHER" if sig[0] == "HIGHER" else "🔴 LOWER"
        ping(f"{head}\n\n{sym} -> {arrow}\nTimeframe: 1m | Expiry: {EXPIRY}\n{sig[1]}\n\n"
             f"Confirm on the IQ chart before entering.")

    if mode == "BLITZ":
        try:
            mt5_gold_check()
        except Exception as e:
            print("error mt5 gold", e)


if __name__ == "__main__":
    if "--test" in sys.argv:
        ping("✅ Test ping: the bot can reach you")
        sys.exit(0)
    deadline = time.time() + int(sys.argv[sys.argv.index("--for") + 1]) if "--for" in sys.argv else None
    while deadline is None or time.time() < deadline:
        try:
            check()
        except Exception as e:
            print("error:", e)
        time.sleep(60)
