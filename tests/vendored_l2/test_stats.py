import math
import unittest

from gate import CONFIDENCE, FP_UPPER_MAX
from gate.stats import (binom_cdf, lower_bound, max_k_within, min_n_within,
                        pass_probability, upper_bound)

try:
    from scipy.stats import beta as _beta  # optional independent check, not a dependency
except ImportError:
    _beta = None


class ZeroFalsePositives(unittest.TestCase):
    """0 of n: the exact upper bound has the closed form 1 - alpha^(1/n).

    (1 - p)^n = alpha solves P(X <= 0) = alpha (Clopper and Pearson 1934;
    Hanley and Lippman-Hand, JAMA 1983;249:1743, "If nothing goes wrong, is
    everything all right?", whose rule of three approximates it as 3/n).
    """

    def test_closed_form_at_200_400_600(self):
        for n in (200, 400, 600):
            with self.subTest(n=n):
                self.assertAlmostEqual(upper_bound(0, n), 1 - 0.05 ** (1 / n), places=15)

    def test_values_against_the_gate(self):
        self.assertAlmostEqual(upper_bound(0, 200), 0.014867039231272054, places=12)
        self.assertAlmostEqual(upper_bound(0, 400), 0.007461355528799602, places=12)
        self.assertAlmostEqual(upper_bound(0, 600), 0.004980443380361347, places=12)
        for n in (200, 400, 600):
            self.assertLessEqual(upper_bound(0, n), FP_UPPER_MAX)

    def test_rule_of_three_is_close_but_not_the_bound(self):
        for n in (200, 400, 600):
            self.assertLess(upper_bound(0, n), 3 / n)
            self.assertLess(3 / n - upper_bound(0, n), 0.01 * 3 / n)

    def test_smallest_n_for_zero(self):
        # ln(0.05) / ln(0.98) = 148.3, so 149.
        self.assertEqual(min_n_within(0, 0.02), 149)
        self.assertGreater(upper_bound(0, 148), 0.02)


class KnownValues(unittest.TestCase):
    """Reference values from scipy 1.17.1, scipy.stats.beta.ppf, computed on 28 Oct 2026.

    Clopper-Pearson equals beta quantiles: U = Beta^-1(c; k+1, n-k) and
    L = Beta^-1(1-c; k, n-k+1). The DAT-837 report (section 2) and Nova's
    DAT-795 comment ff13daca give the same rounded figures (1.487% for 0/200,
    4.52% for 4/200, 1.93% for 3/400, 1.96% for 6/600, 44.0% for 100/200).
    """

    UPPER = {(1, 200): 0.023498470434950704, (4, 200): 0.045180414994718726,
             (3, 400): 0.019269229283088177, (6, 600): 0.01964133024229399}
    LOWER = {(80, 200): 0.34192505481761676, (100, 200): 0.43964102528276117,
             (140, 200): 0.6421982672395455}

    def test_upper(self):
        for (k, n), want in self.UPPER.items():
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(upper_bound(k, n), want, places=10)

    def test_lower(self):
        for (k, n), want in self.LOWER.items():
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(lower_bound(k, n), want, places=10)

    def test_defining_equation_k1(self):
        # P(X <= 1) = (1-p)^n + n p (1-p)^(n-1) must equal alpha at the bound.
        for n in (200, 400, 600):
            p = upper_bound(1, n)
            self.assertAlmostEqual((1 - p) ** n + n * p * (1 - p) ** (n - 1), 0.05, places=12)

    def test_all_successes_closed_form(self):
        self.assertAlmostEqual(lower_bound(200, 200), 0.05 ** (1 / 200), places=15)
        self.assertEqual(upper_bound(200, 200), 1.0)
        self.assertEqual(lower_bound(0, 200), 0.0)

    def test_two_sided_95_is_one_sided_975(self):
        # 0 of 20, two-sided 95%: upper = 1 - 0.025^(1/20) = 0.1684.
        self.assertAlmostEqual(upper_bound(0, 20, 0.975), 1 - 0.025 ** (1 / 20), places=15)
        self.assertAlmostEqual(upper_bound(0, 20, 0.975), 0.1684, places=4)

    @unittest.skipIf(_beta is None, "scipy not installed (optional cross-check)")
    def test_against_scipy_grid(self):
        for n in (50, 149, 200, 400, 600, 1000):
            for k in sorted({0, 1, 2, 3, 6, n // 10, n // 2, n - 1, n}):
                with self.subTest(k=k, n=n):
                    want_u = 1.0 if k == n else _beta.ppf(CONFIDENCE, k + 1, n - k)
                    want_l = 0.0 if k == 0 else _beta.ppf(1 - CONFIDENCE, k, n - k + 1)
                    self.assertAlmostEqual(upper_bound(k, n), want_u, places=9)
                    self.assertAlmostEqual(lower_bound(k, n), want_l, places=9)


class SampleSize(unittest.TestCase):
    def test_min_n_matches_dat837(self):
        # DAT-837 section 2: 149 with 0, 236 with at most 1, 313 with 2, 386 with 3.
        self.assertEqual([min_n_within(k, 0.02) for k in range(4)], [149, 236, 313, 386])

    def test_allowed_false_positives(self):
        self.assertEqual(max_k_within(200, 0.02), 0)
        self.assertEqual(max_k_within(400, 0.02), 3)
        self.assertEqual(max_k_within(600, 0.02), 6)
        self.assertEqual(max_k_within(100, 0.02), -1)

    def test_pass_probability_matches_dat837(self):
        # DAT-837: n=200 37% / 13%, n=400 86% / 43%, n=600 97% / 61%.
        for n, p05, p1 in ((200, 0.37, 0.13), (400, 0.86, 0.43), (600, 0.97, 0.61)):
            with self.subTest(n=n):
                self.assertEqual(round(pass_probability(n, 0.005, 0.02), 2), p05)
                self.assertEqual(round(pass_probability(n, 0.01, 0.02), 2), p1)


class Basics(unittest.TestCase):
    def test_cdf_edges(self):
        self.assertEqual(binom_cdf(-1, 10, 0.5), 0.0)
        self.assertEqual(binom_cdf(10, 10, 0.5), 1.0)
        self.assertAlmostEqual(binom_cdf(5, 10, 0.5), 638 / 1024, places=14)

    def test_monotone(self):
        ups = [upper_bound(k, 400) for k in range(0, 20)]
        self.assertEqual(ups, sorted(ups))
        self.assertTrue(all(lower_bound(k, 400) <= k / 400 <= upper_bound(k, 400)
                            for k in range(0, 401, 20)))

    def test_bad_input(self):
        for k, n in ((-1, 10), (11, 10), (0, 0)):
            with self.assertRaises(ValueError):
                upper_bound(k, n)
        with self.assertRaises(ValueError):
            upper_bound(0, 10, 1.0)
        self.assertFalse(math.isnan(upper_bound(5, 10_000)))


if __name__ == "__main__":
    unittest.main()
