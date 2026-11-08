"""Deterministic synthetic bars for offline tests."""
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "_lib"))
import tadawal as T  # noqa: E402

DAY = 86400
T0 = 1704205800  # 2024-01-02 14:30 UTC


def from_closes(closes, symbol="TEST", tf="D1", spread=0.01, seed=1, vol=1_000_000):
    rnd = random.Random(seed)
    t, o, h, l, c, v = [], [], [], [], [], []
    prev = closes[0]
    for k, cl in enumerate(closes):
        op = prev
        hi = max(op, cl) * (1 + spread * rnd.random())
        lo = min(op, cl) * (1 - spread * rnd.random())
        t.append(T0 + k * DAY); o.append(op); h.append(hi); l.append(lo); c.append(cl)
        v.append(vol * (0.7 + 0.6 * rnd.random()))
        prev = cl
    return T.Bars(symbol, tf, t, o, h, l, c, v, "synthetic")


def walk(n=500, start=100.0, drift=0.0005, sigma=0.015, seed=7, **kw):
    rnd = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(drift + sigma * rnd.gauss(0, 1))
        out.append(px)
    return from_closes(out, seed=seed, **kw)


def path(points, steps=10, **kw):
    """Piecewise-linear path through `points`, `steps` bars per segment."""
    out = []
    for a, b in zip(points, points[1:]):
        for s in range(steps):
            out.append(a + (b - a) * s / steps)
    out.append(points[-1])
    return from_closes(out, spread=0.002, **kw)
