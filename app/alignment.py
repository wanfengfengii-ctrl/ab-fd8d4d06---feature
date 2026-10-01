"""Joint selection of an integer clock offset and an order-preserving pairing.

Two probes record pulse times ``A`` and ``B`` (strictly increasing integers,
nanoseconds).  We must choose a *single* integer offset ``d`` from a closed
interval ``[offset_min, offset_max]`` and a one-to-one order-preserving
matching between the two sequences, lexicographically optimising:

    1. maximise the number of pairs;
    2. minimise  sum |a_i - (b_j + d)|   over the pairs;
    3. minimise  max |a_i - (b_j + d)|   over the pairs;

then breaking ties by the smallest offset and by the lexicographically
smallest sequence of input indices.

A pair (i, j) is admissible at offset d iff |c_ij - d| <= tolerance, where
c_ij = a_i - b_j, i.e. d lies in the integer window [c_ij - T, c_ij + T].

The offset interval may span 2,000,000,000 nanoseconds, so we never scan it
nanosecond by nanosecond.  Instead we build a finite, provably sufficient set
of candidate offsets and run the O(n*m) matching DP once per candidate:

* c_ij - T, c_ij, c_ij + T and c_ij + T + 1 for every edge (i, j):
  feasibility-window boundaries (first/last feasible integer and first
  infeasible one) and residual kinks.  A maximum-pair matching feasible at
  any offset is also feasible at its window's boundary, and the absolute-
  residual sum's minimiser is either a median matched c_ij or, when the
  median plateau lies outside the feasible window, a clamped boundary
  c_ij +/- T -- so every optimum of objectives 1 and 2 lies on these points.
* floor/ceil of (c_p + c_q) / 2 for every pair of *co-orderable* edges
  (i,j),(i',j') that can occur together in an order-preserving matching --
  strictly i<i' and j<j', or vice versa -- restricted to offsets at which
  both edges are within tolerance (|c_p - c_q| <= 2T).  For any fixed
  matching, its largest absolute residual is max(c_max - d, d - c_min), whose
  integer minimiser is the (possibly half-integer) midpoint of two matched
  edges, so every objective-3 optimum is one of these points.
* the interval endpoints.

There are at most 4*n*m + 2 + O((n*m)^2) such values before filtering; with
n, m <= 24 the co-order and |c - c'| <= 2T filters keep only a few thousand
candidates (worst measured case under one second), and every visited offset is
derived from pairing critical values -- never from scanning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# A pairing's lexicographic objective used *inside* the DP, maximised:
#   (pair_count, -abs_sum, -max_abs_residual, index_key)
# ``max`` on this tuple implements: more pairs, smaller abs sum, smaller
# largest absolute residual, then lexicographically smallest paired indices.
Objective = Tuple[int, int, int, Tuple[Tuple[int, int], ...]]


def _empty_objective() -> Objective:
    return (0, 0, 0, ())


def best_pairing_at(
    c: Sequence[Sequence[int]], tol: int
) -> Tuple[Objective, List[Tuple[int, int]]]:
    """Optimal order-preserving matching for a fixed offset.

    ``c[i][j] = a_i - b_j - offset`` is the signed residual that pairing
    (i, j) would have at this offset.  Standard LCS-style DP with three
    incoming edges (skip A_i, skip B_j, pair i with j); indices start at 0.
    """
    n = len(c)
    m = len(c[0]) if n else 0

    dp: List[List[Optional[Objective]]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    parent: List[List[Optional[Tuple[int, int, bool]]]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    dp[0][0] = _empty_objective()

    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best: Optional[Tuple[Objective, Tuple[int, int, bool]]] = None

            if i > 0 and dp[i - 1][j] is not None:
                best = (dp[i - 1][j], (i - 1, j, False))
            if j > 0 and dp[i][j - 1] is not None:
                cand = (dp[i][j - 1], (i, j - 1, False))
                if best is None or cand[0] > best[0]:
                    best = cand
            if i > 0 and j > 0 and dp[i - 1][j - 1] is not None:
                e = c[i - 1][j - 1]
                if -tol <= e <= tol:
                    p = dp[i - 1][j - 1]
                    assert p is not None
                    ae = e if e >= 0 else -e
                    cand_obj: Objective = (
                        p[0] + 1,
                        p[1] - ae,
                        min(p[2], -ae),
                        p[3] + ((i - 1, j - 1),),
                    )
                    cand = (cand_obj, (i - 1, j - 1, True))
                    if best is None or cand[0] > best[0]:
                        best = cand

            if best is not None:
                dp[i][j] = best[0]
                parent[i][j] = best[1]

    final = dp[n][m]
    assert final is not None

    pairs: List[Tuple[int, int]] = []
    i, j = n, m
    while i > 0 or j > 0:
        edge = parent[i][j]
        assert edge is not None
        pi, pj, used = edge
        if used:
            pairs.append((i - 1, j - 1))
        i, j = pi, pj
    pairs.reverse()
    return final, pairs


def _residual_matrix(A: Sequence[int], B: Sequence[int], offset: int):
    return [[a - b - offset for b in B] for a in A]


def _candidate_offsets(
    A: Sequence[int], B: Sequence[int], tol: int, lo: int, hi: int
) -> List[int]:
    """Finite, provably sufficient set of offsets to evaluate.

    See the module docstring for why the global optimum must lie in this set.
    """
    # Every edge with its raw c_ij = a_i - b_j.
    edges: List[Tuple[int, int, int]] = [
        (i, j, a - b)
        for i, a in enumerate(A)
        for j, b in enumerate(B)
    ]

    cand = {lo, hi}
    # Feasibility boundaries (c-T first feasible, c+T last feasible,
    # c+T+1 first infeasible) and residual kink (c) for each edge.
    for _i, _j, cv in edges:
        for d in (cv - tol, cv, cv + tol, cv + tol + 1):
            if lo <= d <= hi:
                cand.add(d)

    # Objective-3 optima: midpoint of the two residual extrema of some
    # matching, i.e. of two edges the matching can contain simultaneously.
    # Edges (i,j) and (i',j') co-occur only when their index order agrees;
    # at the midpoint both must lie inside tolerance, which requires
    # |c - c'| <= 2*tol.
    for x in range(len(edges)):
        i, j, c1 = edges[x]
        for y in range(x + 1, len(edges)):
            i2, j2, c2 = edges[y]
            if (i2 - i) * (j2 - j) <= 0:
                continue  # same index or an inversion -> never in one matching
            if c1 > c2:
                chi, clo = c1, c2
            else:
                chi, clo = c2, c1
            if chi - clo > 2 * tol:
                continue
            # Both edges' joint feasible window at the midpoint.
            flo = max(lo, chi - tol)
            fhi = min(hi, clo + tol)
            if flo > fhi:
                continue
            total = c1 + c2
            mid_floor = total // 2
            mid_ceil = -((-total) // 2)
            for md in (mid_floor, mid_ceil):
                d = md if flo <= md <= fhi else (flo if md < flo else fhi)
                if lo <= d <= hi:
                    cand.add(d)

    return sorted(cand)


@dataclass(frozen=True)
class Pair:
    index_a: int  # 1-based input index
    index_b: int
    a_time: int
    corrected_b: int  # b + offset
    residual: int  # signed: a - (b + offset)


@dataclass(frozen=True)
class Unpaired:
    index: int  # 1-based input index
    time: int


@dataclass(frozen=True)
class CalibrationResult:
    offset: int
    pair_count: int
    residual_abs_sum: int
    max_abs_residual: int
    pairs: Tuple[Pair, ...]
    unpaired_a: Tuple[Unpaired, ...]
    unpaired_b: Tuple[Unpaired, ...]
    min_pairs: int
    sufficient: bool
    reason: Optional[str]

    def to_dict(self) -> dict:
        pairs = [
            {
                "index_a": p.index_a,
                "index_b": p.index_b,
                "a_time": p.a_time,
                "corrected_b": p.corrected_b,
                "residual": p.residual,
            }
            for p in self.pairs
        ]
        response = {
            "offset": self.offset,
            "pair_count": self.pair_count,
            "residual_abs_sum": self.residual_abs_sum,
            "max_abs_residual": self.max_abs_residual,
            "pairs": pairs,
            "unpaired_a": [
                {"index": u.index, "time": u.time} for u in self.unpaired_a
            ],
            "unpaired_b": [
                {"index": u.index, "time": u.time} for u in self.unpaired_b
            ],
            "min_pairs": self.min_pairs,
            "sufficient": self.sufficient,
            "reason": self.reason,
        }
        if not self.sufficient:
            # No calibration value may be presented as a conclusion.  The best
            # count-aligned offset and its pairs survive only as an explicitly
            # labelled diagnostic, useful for explaining the shortfall.
            response["offset"] = None
            response["pairs"] = []
            response["diagnostic"] = {
                "note": "未达到最低配对数，以下偏移与配对仅为最大配对数对齐诊断，"
                        "不是校准结论。",
                "offset": self.offset,
                "residual_abs_sum": self.residual_abs_sum,
                "max_abs_residual": self.max_abs_residual,
                "pairs": pairs,
            }
        return response


def solve(
    A: Sequence[int],
    B: Sequence[int],
    offset_min: int,
    offset_max: int,
    tolerance: int,
    min_pairs: int,
) -> CalibrationResult:
    """Solve the joint offset / pairing problem exactly.

    Inputs are assumed to have been validated by the caller.
    """
    lo, hi = offset_min, offset_max

    # Global lexicographic record: (count, -cost, -max_abs, -offset)
    # maximised; the matching itself is re-derived canonically at the winning
    # offset so the index tie-break is applied at exactly that offset.
    best_score: Optional[Tuple[int, int, int, int]] = None
    best_offset = lo

    for d in _candidate_offsets(A, B, tolerance, lo, hi):
        c = [[a - b - d for b in B] for a in A]
        obj, _pairs = best_pairing_at(c, tolerance)
        count, neg_cost, neg_max_abs, _key = obj

        score = (count, neg_cost, neg_max_abs, -d)
        if best_score is None or score > best_score:
            best_score = score
            best_offset = d

    assert best_score is not None

    # Re-derive the canonical matching at the winning offset.
    c = _residual_matrix(A, B, best_offset)
    obj, pairs = best_pairing_at(c, tolerance)

    pair_objs = tuple(
        Pair(
            index_a=i + 1,
            index_b=j + 1,
            a_time=A[i],
            corrected_b=B[j] + best_offset,
            residual=A[i] - (B[j] + best_offset),
        )
        for (i, j) in pairs
    )
    used_a = {i for (i, _) in pairs}
    used_b = {j for (_, j) in pairs}
    unpaired_a = tuple(
        Unpaired(index=i + 1, time=A[i])
        for i in range(len(A))
        if i not in used_a
    )
    unpaired_b = tuple(
        Unpaired(index=j + 1, time=B[j])
        for j in range(len(B))
        if j not in used_b
    )

    count = len(pairs)
    abs_sum = sum(abs(p.residual) for p in pair_objs)
    max_abs = max((abs(p.residual) for p in pair_objs), default=0)

    sufficient = count >= min_pairs
    reason: Optional[str] = None
    if not sufficient:
        reason = (
            f"在偏移区间 [{offset_min}, {offset_max}] 纳秒、符合容差 "
            f"±{tolerance} 纳秒内，两台探头最多只能形成 {count} 对符合事件"
            f"（要求至少 {min_pairs} 对），无法形成足够的符合事件，"
            "故不给出校准结论。"
        )

    return CalibrationResult(
        offset=best_offset,
        pair_count=count,
        residual_abs_sum=abs_sum,
        max_abs_residual=max_abs,
        pairs=pair_objs,
        unpaired_a=unpaired_a,
        unpaired_b=unpaired_b,
        min_pairs=min_pairs,
        sufficient=sufficient,
        reason=reason,
    )
