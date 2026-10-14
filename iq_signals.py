"""
V-reversal alert: selloff -> bottom -> green-candle bounce => HIGHER
                  rally -> top -> red-candle pullback      => LOWER
1m candles, 5 min expiry. Weekdays are labelled BLITZ, weekends OTC.

Env: TWELVE_DATA_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Run: python reversal_alert.py [--for SECONDS]
"""
import os, sys, time, requests
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ["TWELVE_DATA_KEY"]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHATS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",")]

# Optimized parameters: slightly lower pip requirements & wider lookback to catch more setups
PAIRS = {
    "EUR/USD": {"td": "EUR/USD", "iq": "EURUSD-OTC", "pip": 0.0001, "move": 5, "bounce": 1.5},
    "GBP/USD": {"td": "GBP/USD", "iq": "GBPUSD-OTC", "pip": 0.0001, "move": 5, "bounce": 1.5},
    "USD/JPY": {"td": "USD/JPY", "iq": "USDJPY-OTC", "pip": 0.01,   "move": 5, "bounce": 1.5},
    "XAU/USD": {"td": "XAU/USD", "iq": "XAUUSD-OTC", "pip": 0.10,   "move": 10, "bounce": 2.5},
}
MAX_RETRACE = 0.65     # slightly more flexible retracement cap
EXPIRY = "5 min"
COOLDOWN = 300         # reduced cooldown to 5 minutes so you don't miss recurring setups
TZ = ZoneInfo("Africa/Lagos")
last_alert = {}


def mode_today():
    return "BLITZ" if datetime.now(TZ).weekday() < 5 else "OTC"


iq = None


def iq_candles(cfg):
    """Weekend OTC prices straight from IQ Option."""
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
    r = c[-18:]  # scan wider history
    n = len(r)
    
    # Check for bottom bounce (HIGHER)
    i = min(range(n), key=lambda k: r[k]["low"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        low, top = r[i]["low"], max(x["high"] for x in r[:i])
        drop, bounce = (top - low) / PIP, (r[-1]["close"] - low) / PIP
        if (drop >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= bounce <= MAX_RETRACE * drop
                and r[-1]["close"] > r[-1]["open"]):  # confirmed current candle is green
            return "HIGHER", f"dropped {drop:.1f}p, bounced {bounce:.1f}p"
            
    # Check for top pullback (LOWER)
    i = max(range(n), key=lambda k: r[k]["high"])
    if i >= 2 and 1 <= n - 1 - i <= 7:
        high, bot = r[i]["high"], min(x["low"] for x in r[:i])
        rise, pull = (high - bot) / PIP, (high - r[-1]["close"]) / PIP
        if (rise >= MIN_MOVE_PIPS and MIN_BOUNCE_PIPS <= pull <= MAX_RETRACE * rise
                and r[-1]["close"] < r[-1]["open"]):  # confirmed current candle is red
            return "LOWER", f"rallied {rise:.1f}p, pulled back {pull:.1f}p"
            
    return None


def ping(text):
    for chat in CHATS:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=15)
        except Exception as e:
            print("telegram error:", e)


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
        
        if mode == "BLITZ":
            head = "⚡⚡ BLITZ SIGNAL ⚡⚡\nBLITZ ONLY - NOT OTC"
        else:
            head = "🕑🕑 OTC SIGNAL 🕑🕑\nOTC ONLY - NOT BLITZ"
            
        arrow = "🟢 HIGHER" if sig[0] == "HIGHER" else "🔴 LOWER"
        ping(f"{head}\n\n{sym} -> {arrow}\nTimeframe: 1m | Expiry: {EXPIRY}\n{sig[1]}\n\nConfirm on the IQ chart before entering.")


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
