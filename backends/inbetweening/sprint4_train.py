# SPDX-License-Identifier: GPL-3.0-or-later
"""Train Sprint 4 locally, from scratch, without external data or model assets."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np

from sprint4_worker import CHANNELS, SHAPES, SLOTS, features, forward


TRAIN_SEED = 4072026
TEST_SEED = 42024
STEPS = 10000
BATCH = 192


def draw(rng, n):
    """Original parametric clips: drift, a broad swing, and independent recoil."""
    scale = np.array([0.8, 0.45, 0.28, 0.75, 0.6, 0.65], dtype=np.float32)
    coefficients = rng.uniform(-1, 1, (n, 4, CHANNELS)).astype(np.float32)
    return coefficients * scale * np.array([0.35, 0.5, 0.85, 0.23], np.float32)[None, :, None]


def trajectory(coefficients, times):
    t = times[..., None]
    offset, drift, swing, recoil = (coefficients[:, None, j] for j in range(4))
    return (offset + drift * (2*t - 1) + swing * np.sin(np.pi*t) +
            recoil * np.sin(2*np.pi*t) * (0.65 + 0.35*t))


def batch(rng, n):
    clip = draw(rng, n)
    k = rng.integers(2, SLOTS + 1, size=n)
    slots = np.arange(SLOTS)[None, :]
    mask = slots < k[:, None]
    # Jitter interior anchors inside disjoint bins: continuous times include subframes.
    times = (slots - 0.5 + rng.random((n, SLOTS))) / np.maximum(k[:, None]-2, 1)
    times = np.where(slots == 0, 0, np.where(slots == k[:, None]-1, 1, times))
    times = np.where(mask, times, 0).astype(np.float32)
    query = rng.random(n, dtype=np.float32)
    return features(times, trajectory(clip, times), mask, query), trajectory(clip, query[:, None])[:, 0]


def train():
    rng = np.random.default_rng(TRAIN_SEED)
    weights = [((rng.standard_normal(s) / math.sqrt(s[0])).astype(np.float32)
                if len(s) == 2 else np.zeros(s, np.float32)) for s in SHAPES]
    m = [np.zeros_like(w) for w in weights]
    v = [np.zeros_like(w) for w in weights]
    probe, target = batch(np.random.default_rng(TEST_SEED), 8192)
    before = float(np.mean((forward(probe, weights)[0] - target)**2))
    for step in range(1, STEPS + 1):
        x, y = batch(rng, BATCH)
        pred, h1, h2 = forward(x, weights)
        dy = (pred - y) * (2 / y.size)
        dh2 = (dy @ weights[4].T) * (1 - h2*h2)
        dh1 = (dh2 @ weights[2].T) * (1 - h1*h1)
        grads = (x.T @ dh1, dh1.sum(0), h1.T @ dh2, dh2.sum(0), h2.T @ dy, dy.sum(0))
        lr = (0.0014 * (0.25 + 0.75 * (1-step/STEPS)))
        for w, a, b, g in zip(weights, m, v, grads, strict=True):
            a *= 0.9
            a += 0.1 * g
            b *= 0.999
            b += 0.001 * g*g
            w -= (lr / (1 - 0.9**step)) * a / (np.sqrt(b / (1 - 0.999**step)) + 1e-8)
    after = float(np.mean((forward(probe, weights)[0] - target)**2))
    if not math.isfinite(after) or after >= before * 0.2:
        raise RuntimeError('training did not improve held-out error enough')
    return weights, before, after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path, help='new output file, raw little-endian float32')
    args = parser.parse_args()
    start = perf_counter()
    weights, before, after = train()
    data = b''.join(w.astype('<f4').tobytes() for w in weights)
    with args.weights.open('xb') as stream:
        stream.write(data)
    print(json.dumps(dict(train_seed=TRAIN_SEED, test_seed=TEST_SEED, steps=STEPS, batch=BATCH,
                          heldout_mse_initial=before, heldout_mse_final=after,
                          parameters=len(data)//4, sha256=hashlib.sha256(data).hexdigest(),
                          seconds=perf_counter()-start), allow_nan=False))


if __name__ == '__main__':
    main()
