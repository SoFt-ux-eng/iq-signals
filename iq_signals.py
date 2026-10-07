import os
import datetime
import requests

KEY = os.environ.get("TWELVE_DATA_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "EUR/GBP", "EUR/JPY"]
INTERVAL = "5min"
EXPIRY = "5 minutes"


def ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals, n=14):
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100
    return 100 - 100 / (1 + ag / al)


def signal(closes):
    e9 = ema(closes, 9)[-1]
    e21 = ema(closes, 21)[-1]
    e50 = ema(closes, 50)[-1]
    r = rsi(closes)
    p, prev = closes[-1], closes[-2]
    if e9 > e21 > e50 and 50 <= r <= 68 and p > e9 and p > prev:
        return "BUY", r
    if e9 < e21 < e50 and 32 <= r <= 50 and p < e9 and p < prev:
        return "SELL", r
    return None, r


def get_closes(pair):
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": pair, "interval": INTERVAL, "outputsize": 120, "apikey": KEY},
        timeout=20,
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
        closes = get_closes(pair)
        if not closes or len(closes) < 60:
            continue
        now, r = signal(closes)
        before, _ = signal(closes[:-1])
        if now and now != before:
            send(f"{now} {pair}\nExpiry: {EXPIRY}\nRSI: {r:.0f}\nEnter on the next candle.")


if __name__ == "__main__":
    main()
