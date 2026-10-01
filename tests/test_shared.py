"""Tests for the shared-offset review (two acquisition rounds).

The solver must choose ONE integer offset jointly for both rounds:
    1. maximise min(count_1, count_2);
    2. maximise count_1 + count_2;
    3. minimise the merged abs-residual sum over both rounds;
    4. minimise the largest abs residual over both rounds
       (the maximum may be attained by pairs from DIFFERENT rounds);
    5. smallest offset, then the same stable fixed-offset DP index
       tie-break as single-round calibration, applied to each round.

It must NOT calibrate each round independently and intersect afterwards.
Randomised fuzzing compares against a dense nanosecond DP scan, which is the
independent reference implementation.
"""

from __future__ import annotations

import random
import time
import unittest

from app.alignment import pairing_metrics, solve_shared


def make_increasing(rng, n, span):
    cur = rng.randint(-span // 2, span // 2)
    vals = []
    for _ in range(n):
        cur += rng.randint(1, 9)
        vals.append(cur)
    return vals


def raw(A, B):
    return [[a - b for b in B] for a in A]


def dense_scan(rounds, lo, hi, tol):
    """Reference: enumerate every integer offset, run the fixed-offset DP."""
    mats = [raw(A, B) for A, B in rounds]
    best = None
    best_d = None
    for d in range(lo, hi + 1):
        m1 = pairing_metrics(mats[0], d, tol)
        m2 = pairing_metrics(mats[1], d, tol)
        score = (
            min(m1[0], m2[0]),
            m1[0] + m2[0],
            m1[1] + m2[1],
            min(m1[2], m2[2]),
            -d,
        )
        if best is None or score > best:
            best = score
            best_d = d
    return best_d, best


class SharedSolverTests(unittest.TestCase):
    def test_shared_offset_recovers_common_shift(self):
        # Both rounds carry the same true shift; round sizes differ.
        shift = -987_654_321
        A1 = [10**12 + k * 137 for k in range(10)]
        B1 = [a - shift + (k % 3 - 1) * 2 for k, a in enumerate(A1)]
        A2 = [7 * 10**12 + k * 211 for k in range(8)]
        B2 = [a - shift - (k % 2) * 3 for k, a in enumerate(A2)]
        res = solve_shared(A1, B1, A2, B2, -10**9, 10**9, 5, 6)
        self.assertTrue(res.sufficient)
        self.assertEqual(res.offset, shift)
        self.assertEqual(res.rounds[0].pair_count, 10)
        self.assertEqual(res.rounds[1].pair_count, 8)
        self.assertEqual(res.min_pair_count, 8)
        self.assertEqual(res.total_pair_count, 18)
        self.assertLessEqual(res.max_abs_residual, 3)

    def test_batch_drift_below_gate_no_offset(self):
        # Round 1 is explained by d=0; round 2 by d=40. Gaps (200 ns) are far
        # larger than tolerance (3 ns), so no cross-index matching can rescue
        # either round: no single offset reaches 6 pairs in BOTH rounds.
        A1 = [100 + k * 200 for k in range(8)]
        B1 = list(A1)
        A2 = [9000 + k * 211 for k in range(8)]
        B2 = [a - 40 for a in A2]
        res = solve_shared(A1, B1, A2, B2, -100, 100, 3, 6)
        self.assertFalse(res.sufficient)
        self.assertLess(res.min_pair_count, 6)
        self.assertEqual(
            res.min_pair_count,
            min(res.rounds[0].pair_count, res.rounds[1].pair_count),
        )
        self.assertIsNotNone(res.reason)
        self.assertIn("批间证据不足", res.reason)
        # The API shape must hide the offset while reporting the real counts.
        body = res.to_dict()
        self.assertIsNone(body["offset"])
        self.assertIn("min_pair_count", body["diagnostic"])
        self.assertEqual(body["pair_count_1"], res.rounds[0].pair_count)
        self.assertEqual(body["pair_count_2"], res.rounds[1].pair_count)

    def test_one_round_alone_passes_but_shared_gate_fails(self):
        # Round 1 always pairs fully; round 2 can never reach the gate. A
        # naive "calibrate independently then intersect" would happily return
        # round 1's offset; the joint review must refuse.
        A1 = [10 * k for k in range(6)]
        B1 = list(A1)
        A2 = [100 + k * 30 for k in range(6)]
        B2 = [a + 500 for a in A2]  # 500 ns away, outside [-50, 50]
        res = solve_shared(A1, B1, A2, B2, -50, 50, 3, 4)
        self.assertFalse(res.sufficient)
        self.assertEqual(res.rounds[0].pair_count, 6)
        self.assertEqual(res.rounds[1].pair_count, 0)
        self.assertEqual(res.min_pair_count, 0)
        self.assertIsNone(res.to_dict()["offset"])

    def test_cross_round_max_residual_midpoint(self):
        # Round 1 residuals c = 6, round 2 residuals c = -2 at any common
        # offset where both pair (6 fully matched sequences each).  The merged
        # max residual is max(|6 - d|, |-2 - d|) across DIFFERENT rounds, whose
        # integer minimisers are d = 2 (midpoint); d=1 and d=3 give 5 vs 4.
        A1 = [1000 + k * 97 for k in range(6)]
        B1 = [a - 6 for a in A1]
        A2 = [8000 + k * 113 for k in range(6)]
        B2 = [a + 2 for a in A2]
        res = solve_shared(A1, B1, A2, B2, -20, 20, 20, 6)
        self.assertTrue(res.sufficient)
        self.assertEqual(res.offset, 2)
        self.assertEqual(res.max_abs_residual, 4)
        self.assertEqual(res.residual_abs_sum, 6 * 4 + 6 * 4)
        self.assertEqual(res.rounds[0].max_abs_residual, 4)
        self.assertEqual(res.rounds[1].max_abs_residual, 4)

    def test_half_integer_cross_round_midpoint_smaller_offset(self):
        # c = 6 (round 1) and c = -3 (round 2): midpoint 1.5 ties d=1 and d=2
        # for the merged max residual (5 each) and for the abs sum (9 each),
        # so the smaller offset d = 1 wins.
        A1 = [1000 + k * 97 for k in range(6)]
        B1 = [a - 6 for a in A1]
        A2 = [8000 + k * 113 for k in range(6)]
        B2 = [a + 3 for a in A2]
        res = solve_shared(A1, B1, A2, B2, -20, 20, 20, 6)
        self.assertEqual(res.offset, 1)
        self.assertEqual(res.max_abs_residual, 5)

    def test_wide_interval_not_scanned_two_rounds(self):
        shift = 424_242_424
        A1 = [5 * 10**11 + k * 61 for k in range(12)]
        B1 = [a - shift for a in A1]
        A2 = [2 * 10**12 + k * 79 for k in range(9)]
        B2 = [a - shift for a in A2]
        t0 = time.time()
        res = solve_shared(A1, B1, A2, B2, -10**9, 10**9, 3, 8)
        elapsed = time.time() - t0
        self.assertTrue(res.sufficient)
        self.assertEqual(res.offset, shift)
        self.assertEqual(res.rounds[0].pair_count, 12)
        self.assertEqual(res.rounds[1].pair_count, 9)
        self.assertLess(elapsed, 10.0)

    def test_fuzz_small_against_dense_scan(self):
        rng = random.Random(20261001)
        for _ in range(900):
            rounds = [
                (make_increasing(rng, rng.randint(1, 5), 60),
                 make_increasing(rng, rng.randint(1, 5), 60))
                for _ in range(2)
            ]
            lo = rng.randint(-25, 5)
            hi = lo + rng.randint(0, 30)
            tol = rng.randint(0, 10)
            d_exp, score_exp = dense_scan(rounds, lo, hi, tol)
            res = solve_shared(
                rounds[0][0], rounds[0][1],
                rounds[1][0], rounds[1][1],
                lo, hi, tol, 1,
            )
            got = (
                res.min_pair_count,
                res.total_pair_count,
                -res.residual_abs_sum,
                -res.max_abs_residual,
                -res.offset,
            )
            self.assertEqual(
                got, score_exp,
                msg=f"rounds={rounds} lo={lo} hi={hi} tol={tol} "
                    f"exp d={d_exp}",
            )
            self.assertEqual(res.offset, d_exp)

    def test_fuzz_correlated_rounds_with_drift_against_scan(self):
        # Rounds mostly share a shift but the second round may drift slightly,
        # exercising the min-count objective exactly where independent
        # calibration would diverge.
        rng = random.Random(31337)
        for _ in range(400):
            n = rng.randint(2, 6)
            shift = rng.randint(-15, 15)
            drift = rng.choice([0, 0, rng.randint(-8, 8)])
            base1 = make_increasing(rng, n, 80)
            A1 = base1
            B1 = [a - shift + rng.randint(-3, 3) for a in base1]
            base2 = make_increasing(rng, n, 80)
            A2 = base2
            B2 = [a - shift - drift + rng.randint(-3, 3) for a in base2]
            lo, hi, tol = -40, 40, rng.randint(2, 8)
            rounds = [(A1, B1), (A2, B2)]
            d_exp, score_exp = dense_scan(rounds, lo, hi, tol)
            res = solve_shared(A1, B1, A2, B2, lo, hi, tol, 1)
            got = (
                res.min_pair_count,
                res.total_pair_count,
                -res.residual_abs_sum,
                -res.max_abs_residual,
                -res.offset,
            )
            self.assertEqual(
                got, score_exp,
                msg=f"shift={shift} drift={drift} tol={tol} exp d={d_exp}",
            )

    def test_fuzz_wide_window_dense_scan(self):
        # Interval far wider than the data span: dense DP scan over the whole
        # +/-400 window must agree (catches missing residual candidates inside
        # wide constant-count regions and flat minimiser plateaus).
        rng = random.Random(9091)
        for _ in range(300):
            rounds = [
                (make_increasing(rng, rng.randint(1, 6), 60),
                 make_increasing(rng, rng.randint(1, 6), 60))
                for _ in range(2)
            ]
            tol = rng.randint(0, 12)
            d_exp, score_exp = dense_scan(rounds, -400, 400, tol)
            res = solve_shared(
                rounds[0][0], rounds[0][1],
                rounds[1][0], rounds[1][1],
                -400, 400, tol, 1,
            )
            got = (
                res.min_pair_count,
                res.total_pair_count,
                -res.residual_abs_sum,
                -res.max_abs_residual,
                -res.offset,
            )
            self.assertEqual(
                got, score_exp,
                msg=f"rounds={rounds} tol={tol} exp d={d_exp}",
            )
            self.assertEqual(res.offset, d_exp)

    def test_fuzz_validated_sizes_local_dense_scan(self):
        # 6..12 pulses per probe (validated regime); full-interval scanning is
        # infeasible, so scan a neighbourhood guaranteed to contain every
        # critical value (all c_ij and windows live near the data span).
        rng = random.Random(8888)
        for _ in range(60):
            rounds = []
            for _ in range(2):
                n = rng.randint(6, 10)
                m = rng.randint(6, 10)
                A = make_increasing(rng, n, 400)
                shift = rng.randint(-60, 60)
                B = sorted({a - shift + rng.randint(-6, 6) for a in A})
                while len(B) < 6:
                    B.append(B[-1] + rng.randint(1, 5))
                    B.sort()
                B = B[:m] if len(B) >= m else B
                rounds.append((A, B))
            tol = rng.randint(4, 12)
            res = solve_shared(
                rounds[0][0], rounds[0][1],
                rounds[1][0], rounds[1][1],
                -10**6, 10**6, tol, 1,
            )
            d_exp, score_exp = dense_scan(
                rounds, res.offset - 60, res.offset + 60, tol
            )
            self.assertEqual(res.offset, d_exp)
            self.assertEqual(
                (res.min_pair_count, res.total_pair_count,
                 -res.residual_abs_sum, -res.max_abs_residual),
                score_exp[:4],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
