import time
import requests
from iq_signals import send, PAIRS, KEY, INTERVAL

PAYOUT = 0.85
MIN_TEST_TRADES = 150
PASS_RATE = 55.0


def fetch(pair):
    for attempt in range(2):
        try:
            print("fetching", pair, flush=True)
            resp = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": pair, "interval": INTERVAL, "outputsize": 5000, "apikey": KEY},
                timeout=(10, 60),
            ).json()
            values = resp.get("values")
            if not values:
                print(pair, "no data:", resp.get("message"), flush=True)
                return None
            values = list(reversed(values))[:-1]
            print(pair, "got", len(values), "candles", flush=True)
            return {
                "o": [float(v["open"]) for v in values],
                "c": [float(v["close"]) for v in values],
                "h": [int(v["datetime"][11:13]) if len(v["datetime"]) > 10 else 0 for v in values],
            }
        except Exception as e:
            print(pair, "attempt failed:", e, flush=True)
    return None


def ema_series(v, n):
    k = 2 / (n + 1)
    e = v[0]
    out = [e]
    for x in v[1:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(v, n=14):
    out = [50.0] * len(v)
    if len(v) <= n:
        return out
    g = [max(v[i] - v[i - 1], 0) for i in range(1, len(v))]
    l = [max(v[i - 1] - v[i], 0) for i in range(1, len(v))]
    ag = sum(g[:n]) / n
    al = sum(l[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def boll(v, n=20, k=2.0):
    lo = [float("-inf")] * len(v)
    hi = [float("inf")] * len(v)
    for i in range(n - 1, len(v)):
        w = v[i - n + 1 : i + 1]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        lo[i] = m - k * sd
        hi[i] = m + k * sd
    return lo, hi


def build_signals(d):
    c = d["c"]
    e9, e21, e50 = ema_series(c, 9), ema_series(c, 21), ema_series(c, 50)
    r = rsi_series(c)
    lo, hi = boll(c)
    n = len(c)
    sig = {"trend": [None] * n, "rsi": [None] * n, "boll": [None] * n}
    for i in range(60, n):
        if e9[i] > e21[i] > e50[i] and 50 <= r[i] <= 68 and c[i] > e9[i] and c[i] > c[i - 1]:
            sig["trend"][i] = "BUY"
        elif e9[i] < e21[i] < e50[i] and 32 <= r[i] <= 50 and c[i] < e9[i] and c[i] < c[i - 1]:
            sig["trend"][i] = "SELL"
        if r[i] < 25:
            sig["rsi"][i] = "BUY"
        elif r[i] > 75:
            sig["rsi"][i] = "SELL"
        if c[i] < lo[i] and r[i] < 35:
            sig["boll"][i] = "BUY"
        elif c[i] > hi[i] and r[i] > 65:
            sig["boll"][i] = "SELL"
    return sig


def main():
    breakeven = 100 / (1 + PAYOUT)
    variants = {}
    for strat in ("trend", "rsi", "boll"):
        for k in (1, 3):
            for hrs in ("all", "7-17"):
                variants[(strat, k, hrs)] = [0, 0, 0, 0]

    for pair in PAIRS:
        d = fetch(pair)
        time.sleep(10)
        if not d:
            continue
        sigs = build_signals(d)
        n = len(d["c"])
        split = int(n * 0.7)
        for (strat, k, hrs), res in variants.items():
            s = sigs[strat]
            for i in range(61, n - k):
                if not s[i] or s[i] == s[i - 1]:
                    continue
                if hrs == "7-17" and not (7 <= d["h"][i] < 17):
                    continue
                entry, exit_ = d["o"][i + 1], d["c"][i + k]
                if exit_ == entry:
                    continue
                win = (s[i] == "BUY") == (exit_ > entry)
                idx = 0 if i < split else 2
                res[idx if win else idx + 1] += 1

    rows = []
    for (strat, k, hrs), (tw, tl, vw, vl) in variants.items():
        trn, tst = tw + tl, vw + vl
        tr = 100 * tw / trn if trn else 0
        ts = 100 * vw / tst if tst else 0
        rows.append((ts, tr, trn, tst, f"{strat} {k * 5}m {hrs}"))
    rows.sort(reverse=True)

    lines = [
        f"{name}: train {tr:.1f}% ({trn}) | test {ts:.1f}% ({tst})"
        for ts, tr, trn, tst, name in rows
    ]
    passing = [r for r in rows if r[0] >= PASS_RATE and r[1] >= PASS_RATE and r[3] >= MIN_TEST_TRADES]
    if passing:
        best = passing[0]
        verdict = f"CANDIDATE: {best[4]} held up on unseen data. Demo test it before real money."
    else:
        verdict = "NONE of these beat break-even on both train and unseen test data. Do not trade any of them."
    send(
        "VARIANT TEST (7 pairs, win rate vs unseen data)\n"
        + "\n".join(lines)
        + f"\n\nBreak-even at {PAYOUT * 100:.0f}% payout: {breakeven:.1f}%"
        + f"\n{verdict}"
    )


if __name__ == "__main__":
    main()


