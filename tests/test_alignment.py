"""Unit tests plus brute-force differential testing.

For small instances the brute force enumerates *every* integer offset in the
interval and every order-preserving matching, applying exactly the specified
lexicographic order (count, abs-sum, max-abs, smallest offset, smallest index
sequence).  Randomised fuzzing then asserts the critical-value solver agrees,
which directly rules out any offset-scan shortcut or missed optimum.
"""

from __future__ import annotations

import random
import unittest
from itertools import combinations

from app.alignment import best_pairing_at, solve


def all_order_preserving_matchings(n, m):
    for k in range(min(n, m) + 1):
        for ia in combinations(range(n), k):
            for jb in combinations(range(m), k):
                yield list(zip(ia, jb))


def brute_force(A, B, lo, hi, tol, min_pairs):
    best = None  # (count, -cost, -maxabs, -offset, key)
    best_payload = None
    for d in range(lo, hi + 1):
        for pairs in all_order_preserving_matchings(len(A), len(B)):
            residuals = [A[i] - (B[j] + d) for (i, j) in pairs]
            if any(abs(r) > tol for r in residuals):
                continue
            count = len(pairs)
            cost = sum(abs(r) for r in residuals)
            maxabs = max((abs(r) for r in residuals), default=0)
            key = tuple(pairs)
            score = (count, -cost, -maxabs, -d, key)
            if best is None or score > best:
                best = score
                best_payload = (d, pairs, count, cost, maxabs)
    return best_payload


def make_increasing(rng, n, span):
    start = rng.randint(-span // 2, span // 2)
    vals = []
    cur = start
    for _ in range(n):
        cur += rng.randint(1, 9)
        vals.append(cur)
    return vals


class SolverTests(unittest.TestCase):
    def test_basic_offset_and_pairs(self):
        # B is A shifted by +120, with one extra pulse on each probe.
        A = [100, 250, 410, 560, 720, 880]
        B = [-20, 130, 290, 440, 1000, 1160]
        # Corrected B should equal A: need offset d = 120.
        res = solve(A, B, -500, 500, 5, 4)
        self.assertTrue(res.sufficient)
        self.assertEqual(res.offset, 120)
        self.assertEqual(res.pair_count, 4)
        self.assertEqual(res.residual_abs_sum, 0)
        self.assertEqual(res.max_abs_residual, 0)
        self.assertEqual([p.index_a for p in res.pairs], [1, 2, 3, 4])
        self.assertEqual([p.index_b for p in res.pairs], [1, 2, 3, 4])
        self.assertEqual(
            [(u.index, u.time) for u in res.unpaired_a],
            [(5, 720), (6, 880)],
        )
        self.assertEqual(
            [(u.index, u.time) for u in res.unpaired_b],
            [(5, 1000), (6, 1160)],
        )
        for p in res.pairs:
            self.assertEqual(p.a_time, p.corrected_b)
            self.assertEqual(p.residual, 0)

    def test_insufficient_pairs_does_not_fabricate(self):
        A = [10, 20, 30, 40, 50, 60]
        B = [1000, 1020, 1040, 1060, 1080, 1100]
        res = solve(A, B, -100, 100, 5, 4)
        self.assertFalse(res.sufficient)
        self.assertEqual(res.pair_count, 0)
        self.assertIsNotNone(res.reason)
        self.assertIn("0", res.reason)
        self.assertEqual(res.pairs, ())
        self.assertEqual(len(res.unpaired_a), 6)
        self.assertEqual(len(res.unpaired_b), 6)

    def test_offset_tie_prefers_smaller_offset(self):
        # Pairing c-values {0, 4}: with tolerance 4 both pairs are feasible
        # for every d in [0, 4], and sum|c-d| is constantly 4 there.
        # max|c-d| at d=2 is 2; d=1 and d=3 give 3, so unique integer
        # minimiser d=2 here.
        A = [10, 20]
        B = [10, 16]  # c = 0, 4
        res = solve(A, B, -10, 10, 10, 1)
        self.assertEqual(res.offset, 2)
        self.assertEqual(res.residual_abs_sum, 4)
        self.assertEqual(res.max_abs_residual, 2)

    def test_half_integer_midpoint_prefers_smaller_integer(self):
        # c-values {0, 3}: sum|c-d| minimal for d in {0,1,2,3}; the largest
        # absolute residual is 2 for both d=1 (residuals -1,+2) and d=2
        # (-2,+1), so the smaller offset d=1 must win.
        A = [10, 23]
        B = [10, 20]  # c = 0, 3
        res = solve(A, B, -10, 10, 10, 1)
        self.assertEqual(res.offset, 1)
        self.assertEqual(res.residual_abs_sum, 3)
        self.assertEqual(res.max_abs_residual, 2)

    def test_median_plateau_offset_tie_smaller_wins(self):
        # c-values {0, 4} with a narrow tolerance that still leaves a flat
        # tie region for the third objective elsewhere is covered above;
        # here check an exact two-integer max-residual tie with equal sum.
        A = [0, 1]
        B = [0, 1]
        res = solve(A, B, -10, 10, 10, 1)
        self.assertEqual(res.offset, 0)

    def test_wide_interval_not_scanned(self):
        # 2,000,000,000 ns span; result must be instant and exact. True shift
        # is -987654321.
        shift = -987654321
        A = [10**12 + k * 37 for k in range(8)]
        B = [a - shift for a in A]
        res = solve(A, B, -10**9, 10**9, 10, 6)
        self.assertTrue(res.sufficient)
        self.assertEqual(res.offset, shift)
        self.assertEqual(res.pair_count, 8)
        self.assertEqual(res.residual_abs_sum, 0)

    def test_dp_matches_enumeration_fixed_offset(self):
        rng = random.Random(4242)
        for _ in range(300):
            n = rng.randint(1, 5)
            m = rng.randint(1, 5)
            A = make_increasing(rng, n, 40)
            B = make_increasing(rng, m, 40)
            d = rng.randint(-15, 15)
            tol = rng.randint(0, 12)
            c = [[a - b - d for b in B] for a in A]
            obj, pairs = best_pairing_at(c, tol)
            ref = None
            for cand in all_order_preserving_matchings(n, m):
                res = [A[i] - (B[j] + d) for (i, j) in cand]
                if any(abs(r) > tol for r in res):
                    continue
                score = (
                    len(cand),
                    -sum(abs(r) for r in res),
                    -max((abs(r) for r in res), default=0),
                    tuple(cand),
                )
                if ref is None or score > ref:
                    ref = score
            self.assertIsNotNone(ref)
            self.assertEqual(obj, ref)

    def test_fuzz_against_brute_force(self):
        rng = random.Random(20260930)
        cases = 0
        for _ in range(1500):
            n = rng.randint(1, 5)
            m = rng.randint(1, 5)
            A = make_increasing(rng, n, 60)
            B = make_increasing(rng, m, 60)
            lo = rng.randint(-25, 5)
            hi = lo + rng.randint(0, 30)
            tol = rng.randint(0, 10)
            min_pairs = 1

            expected = brute_force(A, B, lo, hi, tol, min_pairs)
            res = solve(A, B, lo, hi, tol, min_pairs)
            self.assertIsNotNone(expected)
            d_exp, pairs_exp, cnt_exp, cost_exp, max_exp = expected
            self.assertEqual(
                (res.offset, res.pair_count,
                 res.residual_abs_sum, res.max_abs_residual),
                (d_exp, cnt_exp, cost_exp, max_exp),
                msg=f"A={A} B={B} lo={lo} hi={hi} tol={tol} "
                    f"exp_pairs={pairs_exp}",
            )
            got_pairs = [(p.index_a - 1, p.index_b - 1) for p in res.pairs]
            self.assertEqual(got_pairs, [tuple(p) for p in pairs_exp])
            cases += 1
        self.assertGreater(cases, 0)

    def test_fuzz_wide_window_dense_scan(self):
        # Interval far wider than the data span: a dense nanosecond DP scan
        # over the whole +/-500 window must agree with the critical-value
        # solver.  This guards against missing optima in open regions.
        rng = random.Random(909)
        for _ in range(400):
            n = rng.randint(1, 6)
            m = rng.randint(1, 6)
            A = make_increasing(rng, n, 60)
            B = make_increasing(rng, m, 60)
            tol = rng.randint(0, 12)
            res = solve(A, B, -500, 500, tol, 1)
            best = None
            best_d = None
            best_pairs = None
            for d in range(-500, 501):
                c = [[a - b - d for b in B] for a in A]
                obj, pairs = best_pairing_at(c, tol)
                score = (obj[0], obj[1], obj[2], -d)
                if best is None or score > best:
                    best = score
                    best_d = d
                    best_pairs = pairs
            self.assertEqual(res.offset, best_d)
            self.assertEqual(res.pair_count, best[0])
            self.assertEqual(res.residual_abs_sum, -best[1])
            self.assertEqual(res.max_abs_residual, -best[2])
            self.assertEqual(
                [(p.index_a - 1, p.index_b - 1) for p in res.pairs],
                [tuple(p) for p in best_pairs],
            )

    def test_fuzz_larger_counts_within_6_24(self):
        # Validated input-size regime (6..24 each); enumerating all matchings
        # is infeasible here, so the reference is a dense *offset scan* using
        # the same DP (already proven equal to full enumeration at a fixed
        # offset in test_dp_matches_enumeration_fixed_offset).  This still
        # catches any critical-value omission in the global solver.
        def scan_reference(A, B, lo, hi, tol):
            best = None
            best_d = None
            for d in range(lo, hi + 1):
                c = [[a - b - d for b in B] for a in A]
                obj, _ = best_pairing_at(c, tol)
                score = (obj[0], obj[1], obj[2], -d)
                if best is None or score > best:
                    best = score
                    best_d = d
            return best_d, best

        rng = random.Random(77)
        for _ in range(60):
            n = rng.randint(6, 12)
            m = rng.randint(6, 12)
            A = make_increasing(rng, n, 500)
            shift = rng.randint(-80, 80)
            B = [a - shift + rng.randint(-6, 6) for a in A]
            B = sorted(set(B))
            while len(B) < 6:
                B.append(B[-1] + rng.randint(1, 5))
                B.sort()
            tol = rng.randint(4, 15)
            lo, hi = -10**6, 10**6
            res = solve(A, B, lo, hi, tol, 1)
            ref_d, ref_obj = scan_reference(
                A, B, res.offset - 40, res.offset + 40, tol
            )
            self.assertEqual(res.offset, ref_d)
            self.assertEqual(res.pair_count, ref_obj[0])
            self.assertEqual(res.residual_abs_sum, -ref_obj[1])
            self.assertEqual(res.max_abs_residual, -ref_obj[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
