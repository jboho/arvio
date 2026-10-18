"""Small-sample statistics for comparing labels with a judge.

Ported from an earlier eval harness, with two changes: wilson(0, 0) is (0, 1),
since no data rules nothing out, and kappa takes any number of classes.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Hashable, Sequence

Z95 = 1.96
BOOTSTRAP_N = 1000
SEED = 42

Pair = tuple[Hashable, Hashable]


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score range for k successes out of n; sound at small n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def kappa(pairs: Sequence[Pair]) -> float | None:
    """Cohen's kappa over (human class, judge class) pairs.

    None when the judge used fewer than 2 classes: a constant judge leaves no
    variation to correct for chance against.
    """
    n = len(pairs)
    judge = Counter(j for _, j in pairs)
    if n == 0 or len(judge) < 2:
        return None
    human = Counter(h for h, _ in pairs)
    observed = sum(1 for h, j in pairs if h == j) / n
    expected = sum(human[c] * judge[c] for c in judge) / (n * n)
    return (observed - expected) / (1 - expected)


def bootstrap_kappa(
    pairs: Sequence[Pair], resamples: int = BOOTSTRAP_N, seed: int = SEED
) -> tuple[float, float, int] | None:
    """Percentile 95% range for kappa by resampling pairs with replacement.

    Resamples where kappa is undefined are dropped, not counted as 0: a draw
    that happens to be constant says nothing about the data. Returns
    (low, high, resamples used), or None if fewer than 2 were defined.
    """
    n = len(pairs)
    if n == 0:
        return None
    rng = random.Random(seed)
    found = []
    for _ in range(resamples):
        k = kappa([pairs[rng.randrange(n)] for _ in range(n)])
        if k is not None:
            found.append(k)
    if len(found) < 2:
        return None
    found.sort()
    low = found[int(0.025 * len(found))]
    high = found[min(len(found) - 1, int(0.975 * len(found)))]
    return (low, high, len(found))
