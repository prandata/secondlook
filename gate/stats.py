"""Exact binomial bounds (Clopper-Pearson, one-sided) with the standard library.

The one-sided upper bound U for k events in n trials at confidence c is the p
that solves P(X <= k; n, p) = 1 - c. The lower bound L solves
P(X >= k; n, p) = 1 - c. Both are found by bisection on the exact binomial
CDF, which is monotone in p, so no beta-quantile routine (scipy) is needed.
Closed forms are used where they exist: U = 1 - (1-c)^(1/n) when k = 0 and
L = (1-c)^(1/n) when k = n (Clopper and Pearson 1934; Hanley and
Lippman-Hand 1983 for the zero-numerator case).

    python -m gate.stats table      # the sample-size table in README.md
"""

from __future__ import annotations

import math
import sys

_ITERATIONS = 200  # bisection on [0, 1]; doubles stop changing well before this


def _check(k: int, n: int, confidence: float) -> None:
    if not isinstance(k, int) or not isinstance(n, int):
        raise TypeError("k and n must be integers")
    if n <= 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k}, n={n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed exactly in log space."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    log_n_fact = math.lgamma(n + 1)
    terms = [
        math.exp(log_n_fact - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                 + i * log_p + (n - i) * log_q)
        for i in range(k + 1)
    ]
    return min(1.0, math.fsum(terms))


def upper_bound(k: int, n: int, confidence: float = 0.95) -> float:
    """One-sided exact upper bound on the true rate, given k of n."""
    _check(k, n, confidence)
    alpha = 1.0 - confidence
    if k == n:
        return 1.0
    if k == 0:
        return 1.0 - alpha ** (1.0 / n)
    lo, hi = k / n, 1.0  # binom_cdf(k; n, p) falls from > alpha to 0 on this range
    for _ in range(_ITERATIONS):
        mid = (lo + hi) / 2.0
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return (lo + hi) / 2.0


def lower_bound(k: int, n: int, confidence: float = 0.95) -> float:
    """One-sided exact lower bound on the true rate, given k of n."""
    _check(k, n, confidence)
    alpha = 1.0 - confidence
    if k == 0:
        return 0.0
    if k == n:
        return alpha ** (1.0 / n)
    lo, hi = 0.0, k / n  # P(X >= k) = 1 - cdf(k-1) rises from 0 on this range
    for _ in range(_ITERATIONS):
        mid = (lo + hi) / 2.0
        if 1.0 - binom_cdf(k - 1, n, mid) < alpha:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return (lo + hi) / 2.0


def max_k_within(n: int, bound: float, confidence: float = 0.95) -> int:
    """Largest k whose upper bound is <= bound at this n; -1 if even k=0 fails."""
    k = -1
    while k + 1 <= n and upper_bound(k + 1, n, confidence) <= bound:
        k += 1
    return k


def min_n_within(k: int, bound: float, confidence: float = 0.95,
                 n_max: int = 100_000) -> int:
    """Smallest n at which k events still give an upper bound <= bound."""
    n = max(k, 1)
    while n <= n_max:
        if upper_bound(k, n, confidence) <= bound:
            return n
        n += 1
    raise ValueError(f"no n <= {n_max} gives an upper bound <= {bound} with k={k}")


def pass_probability(n: int, true_rate: float, bound: float,
                     confidence: float = 0.95) -> float:
    """Chance that a detector with this true false-positive rate shows bound <= bound."""
    return binom_cdf(max_k_within(n, bound, confidence), n, true_rate)


def _table(out=sys.stdout) -> None:
    from gate import CONFIDENCE, FP_UPPER_MAX
    print("| Real images n | Most false positives that still pass | "
          "Upper bound at that count | Pass chance, true FP 0.5% | "
          "Pass chance, true FP 1% |", file=out)
    print("|---|---|---|---|---|", file=out)
    for n in (149, 200, 300, 400, 500, 600, 800, 1000):
        k = max_k_within(n, FP_UPPER_MAX, CONFIDENCE)
        ub = upper_bound(k, n, CONFIDENCE) if k >= 0 else float("nan")
        print(f"| {n} | {k} | {ub:.3%} | "
              f"{pass_probability(n, 0.005, FP_UPPER_MAX, CONFIDENCE):.1%} | "
              f"{pass_probability(n, 0.01, FP_UPPER_MAX, CONFIDENCE):.1%} |", file=out)
    print(file=out)
    mins = ", ".join(f"{min_n_within(k, FP_UPPER_MAX, CONFIDENCE)} for {k}"
                     for k in range(0, 7))
    print(f"Smallest n per allowed false-positive count: {mins}.", file=out)


if __name__ == "__main__":
    if sys.argv[1:] == ["table"]:
        _table()
    else:
        print(__doc__)
        sys.exit(2)
