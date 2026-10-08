import os
import datetime
import requests

KEY = os.environ.get("TWELVE_DATA_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "EUR/GBP", "EUR/JPY"]
INTERVAL = "5min"
EXPIRY = "15 minutes"


def rsi(vals, n=14):
    g = [max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals))]
    l = [max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals))]
    ag = sum(g[:n]) / n
    al = sum(l[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def signal(closes):
    w = closes[-20:]
    m = sum(w) / 20
    sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
    lo, hi = m - 2 * sd, m + 2 * sd
    r = rsi(closes)
    c = closes[-1]
    if c < lo and r < 35:
        return "BUY", r
    if c > hi and r > 65:
        return "SELL", r
    return None, r


def get_closes(pair):
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": pair, "interval": INTERVAL, "outputsize": 120, "apikey": KEY},
        timeout=(10, 40),
    ).json()
    values = resp.get("values")
    if not values:
        return None
    closes = [float(v["close"]) for v in reversed(values)]
    return closes[:-1]


def send(msg):
    if TG_TOKEN and TG_CHAT:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=20,
        )
    print(msg)


def main():
    if datetime.datetime.utcnow().weekday() >= 5:
        print("Weekend, market closed.")
        return
    for pair in PAIRS:
        try:
            closes = get_closes(pair)
        except Exception as e:
            print(pair, "failed:", e)
            continue
        if not closes or len(closes) < 60:
            continue
        now, r = signal(closes)
        before, _ = signal(closes[:-1])
        if now and now != before:
            send(f"{now} {pair}\nExpiry: {EXPIRY}\nRSI: {r:.0f}\nEnter now.")


if __name__ == "__main__":
    main()
