import time
import requests
from iq_signals import signal, send, PAIRS, KEY, INTERVAL

PAYOUT = 0.85
WINDOW = 120


def fetch(pair):
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": pair, "interval": INTERVAL, "outputsize": 5000, "apikey": KEY},
        timeout=60,
    ).json()
    values = resp.get("values")
    if not values:
        print(pair, "no data:", resp.get("message"))
        return None, None
    values = list(reversed(values))[:-1]
    return [float(v["open"]) for v in values], [float(v["close"]) for v in values]


def backtest(opens, closes):
    wins = losses = pushes = 0
    prev = None
    for i in range(WINDOW - 1, len(closes) - 1):
        sig, _ = signal(closes[i - WINDOW + 1 : i + 1])
        if sig and sig != prev:
            entry, exit_ = opens[i + 1], closes[i + 1]
            if exit_ == entry:
                pushes += 1
            elif (sig == "BUY") == (exit_ > entry):
                wins += 1
            else:
                losses += 1
        prev = sig
    return wins, losses, pushes


def main():
    breakeven = 100 / (1 + PAYOUT)
    lines = []
    tw = tl = 0
    for pair in PAIRS:
        opens, closes = fetch(pair)
        time.sleep(10)
        if not closes:
            continue
        w, l, p = backtest(opens, closes)
        n = w + l
        rate = 100 * w / n if n else 0
        net = w * PAYOUT - l
        lines.append(f"{pair}: {n} trades, {rate:.1f}% win, net {net:+.1f} units")
        tw += w
        tl += l
    total = tw + tl
    if total == 0:
        send("Backtest found no trades.")
        return
    rate = 100 * tw / total
    net = tw * PAYOUT - tl
    verdict = (
        "Beats break-even on this sample. Still test on demo."
        if rate > breakeven
        else "Does NOT beat break-even. Don't trade it as is."
    )
    send(
        "BACKTEST (5-min candles, 5-min expiry)\n"
        + "\n".join(lines)
        + f"\n\nALL: {total} trades, {rate:.1f}% win, net {net:+.1f} units (1 unit stake)"
        + f"\nBreak-even at {PAYOUT*100:.0f}% payout: {breakeven:.1f}%"
        + f"\n{verdict}"
    )


if __name__ == "__main__":
    main()


