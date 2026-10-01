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

import bisect
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


def _raw_c_matrix(A: Sequence[int], B: Sequence[int]) -> List[List[int]]:
    return [[a - b for b in B] for a in A]


def pairing_metrics(
    C: Sequence[Sequence[int]], offset: int, tol: int
) -> Tuple[int, int, int]:
    """Lean fixed-offset DP: ``(pair_count, -abs_sum, -max_abs_residual)``.

    Same transitions as :func:`best_pairing_at` (whose fixed-offset optimum is
    proven against full enumeration), but with rolling rows and without parent
    pointers or the index tie-break key: while searching offsets the offset
    itself is the next tie-break, and the canonical matching is re-derived once
    at the winning offset.
    """
    n = len(C)
    m = len(C[0]) if n else 0
    zero = (0, 0, 0)
    prev = [zero] * (m + 1)
    for i in range(1, n + 1):
        row = C[i - 1]
        cur = [zero] * (m + 1)
        for j in range(1, m + 1):
            best = prev[j]  # skip A[i-1]
            v = cur[j - 1]  # skip B[j-1]
            if v > best:
                best = v
            p = prev[j - 1]
            e = row[j - 1] - offset
            if -tol <= e <= tol:
                ae = e if e >= 0 else -e
                cand = (p[0] + 1, p[1] - ae, min(p[2], -ae))
                if cand > best:
                    best = cand
            cur[j] = best
        prev = cur
    return prev[m]


# ---------------------------------------------------------------------------
# Shared-offset review: ONE integer offset must explain two acquisition rounds
# (the same probe pair before and after the calibration-source strength is
# changed).  At a single offset d each round gets its own order-preserving
# one-to-one matching, and the joint lexicographic objective maximises:
#
#     1. min(pair_count_1, pair_count_2)      -- both rounds at once;
#     2. pair_count_1 + pair_count_2;
#     3. -(sum of |residual| over BOTH rounds);
#     4. -(largest |residual| over BOTH rounds);
# then 5. -d (smallest offset wins) and, at the winning offset, each round's
# canonical matching re-derived with the very same fixed-offset DP (and thus
# exactly the same stable index tie-break) as single-round calibration.
#
# The rounds are never calibrated independently and intersected afterwards:
# the shared offset is chosen jointly.
#
# The search is split in two phases.  Each round's pair count is a step
# function of d whose value only changes at feasibility-window boundaries
# c_ij +/- T (and c_ij +/- T + 1); phase 1 evaluates one DP per constant-count
# region and finds the exact optimum of objectives 1-2 together with the union
# W of integer intervals on which that optimum is attained.  Phase 2 only
# visits residual critical values inside W -- matched-edge kinks c_ij and
# midpoints of residual extrema -- so the wide interval is never scanned.
# Extrema for the merged max-residual objective may come from DIFFERENT
# rounds, so midpoint pairs are generated both for co-orderable edges within a
# round and for every cross-round edge pair (rounds share no index ordering).
# ---------------------------------------------------------------------------


def _merge_intervals(
    intervals: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    merged: List[Tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            if hi > merged[-1][1]:
                merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


def _contains(intervals: Sequence[Tuple[int, int]], d: int) -> bool:
    if not intervals:
        return False
    starts = [lo for lo, _ in intervals]
    k = bisect.bisect_right(starts, d) - 1
    return k >= 0 and intervals[k][0] <= d <= intervals[k][1]


def _constant_count_regions(
    edge_rounds: Sequence[Sequence[Tuple[int, int, int]]],
    tol: int,
    lo: int,
    hi: int,
) -> List[Tuple[int, int, int]]:
    """One ``(representative, region_lo, region_hi)`` per integer region on
    which every round's feasibility pattern (and hence pair count) is
    constant: critical points are the window boundaries c +/- T and the first
    infeasible integer c + T + 1 (c - T - 1 is the previous window's +1 point);
    open integer gaps between consecutive critical points are one region each.
    """
    critical = {lo, hi}
    for edges in edge_rounds:
        for _i, _j, cv in edges:
            for d in (cv - tol, cv + tol, cv + tol + 1):
                if lo <= d <= hi:
                    critical.add(d)
    points = sorted(critical)
    regions: List[Tuple[int, int, int]] = [(points[0], points[0], points[0])]
    prev_pt = points[0]
    for pt in points[1:]:
        if pt - prev_pt >= 2:
            rep = prev_pt + 1
            regions.append((rep, rep, pt - 1))
        regions.append((pt, pt, pt))
        prev_pt = pt
    return regions


def _phase1_winning_regions(
    edge_rounds: Sequence[Sequence[Tuple[int, int, int]]],
    matrices: Sequence[List[List[int]]],
    tol: int,
    lo: int,
    hi: int,
) -> Tuple[Tuple[int, int], List[Tuple[int, int]], List[int]]:
    """Best (min-count, total-count), intervals attaining it, region reps."""
    regions = _constant_count_regions(edge_rounds, tol, lo, hi)

    best_key: Optional[Tuple[int, int]] = None
    winning: List[Tuple[int, int]] = []
    for rep, rl, rh in regions:
        k1 = pairing_metrics(matrices[0], rep, tol)[0]
        k2 = pairing_metrics(matrices[1], rep, tol)[0]
        key = (min(k1, k2), k1 + k2)
        if best_key is None or key > best_key:
            best_key = key
            winning = [(rl, rh)]
        elif key == best_key:
            winning.append((rl, rh))
    assert best_key is not None
    reps = [rep for rep, _l, _r in regions]
    return best_key, _merge_intervals(winning), reps


def _shared_candidate_offsets(
    edge_rounds: Sequence[Sequence[Tuple[int, int, int]]],
    winning: Sequence[Tuple[int, int]],
    tol: int,
    lo: int,
    hi: int,
    region_reps: Sequence[int],
) -> List[int]:
    """Residual-phase candidate offsets lying inside a winning-count region."""
    cand: set = set()
    for rep in region_reps:
        if _contains(winning, rep):
            cand.add(rep)

    def in_winning(d: int) -> bool:
        return _contains(winning, d)

    # Kinks and clamped window boundaries of every edge (both rounds).
    for edges in edge_rounds:
        for _i, _j, cv in edges:
            for d in (cv - tol, cv, cv + tol, cv + tol + 1):
                if lo <= d <= hi and in_winning(d):
                    cand.add(d)

    # Midpoints of residual-extremum pairs: co-orderable edges within a round
    # and ANY edge pair across rounds (the rounds' matchings are independent,
    # so cross-round indices impose no ordering restriction).
    for r_a, edges_a in enumerate(edge_rounds):
        for x, (i1, j1, c1) in enumerate(edges_a):
            for r_b, edges_b in enumerate(edge_rounds):
                if r_b < r_a:
                    continue
                start = x + 1 if r_b == r_a else 0
                for y in range(start, len(edges_b)):
                    i2, j2, c2 = edges_b[y]
                    if r_b == r_a and (i2 - i1) * (j2 - j1) <= 0:
                        continue  # same index or inversion within one round
                    if c1 > c2:
                        chi, clo = c1, c2
                    else:
                        chi, clo = c2, c1
                    if chi - clo > 2 * tol:
                        continue
                    total = c1 + c2
                    mid_floor = total // 2
                    mid_ceil = -((-total) // 2)
                    for md in (mid_floor, mid_ceil):
                        if (
                            max(lo, chi - tol) <= md <= min(hi, clo + tol)
                            and in_winning(md)
                        ):
                            cand.add(md)
    return sorted(cand)


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


@dataclass(frozen=True)
class RoundResult:
    pair_count: int
    residual_abs_sum: int
    max_abs_residual: int
    pairs: Tuple[Pair, ...]
    unpaired_a: Tuple[Unpaired, ...]
    unpaired_b: Tuple[Unpaired, ...]

    def to_dict(self) -> dict:
        return {
            "pair_count": self.pair_count,
            "residual_abs_sum": self.residual_abs_sum,
            "max_abs_residual": self.max_abs_residual,
            "pairs": [
                {
                    "index_a": p.index_a,
                    "index_b": p.index_b,
                    "a_time": p.a_time,
                    "corrected_b": p.corrected_b,
                    "residual": p.residual,
                }
                for p in self.pairs
            ],
            "unpaired_a": [
                {"index": u.index, "time": u.time} for u in self.unpaired_a
            ],
            "unpaired_b": [
                {"index": u.index, "time": u.time} for u in self.unpaired_b
            ],
        }


@dataclass(frozen=True)
class SharedCalibrationResult:
    mode: str
    offset: Optional[int]
    min_pair_count: int  # min of the two rounds' pair counts at the offset
    total_pair_count: int
    residual_abs_sum: int
    max_abs_residual: int
    rounds: Tuple[RoundResult, ...]
    min_pairs: int
    sufficient: bool
    reason: Optional[str]

    def to_dict(self) -> dict:
        rounds = [r.to_dict() for r in self.rounds]
        response = {
            "mode": self.mode,
            "offset": self.offset,
            "min_pair_count": self.min_pair_count,
            "total_pair_count": self.total_pair_count,
            "pair_count_1": self.rounds[0].pair_count,
            "pair_count_2": self.rounds[1].pair_count,
            "residual_abs_sum": self.residual_abs_sum,
            "max_abs_residual": self.max_abs_residual,
            "rounds": rounds,
            "min_pairs": self.min_pairs,
            "sufficient": self.sufficient,
            "reason": self.reason,
        }
        if not self.sufficient:
            # Neither round clears the gate at the jointly best offset: no
            # calibration offset is presented.  The best joint alignment is
            # kept only as an explicitly labelled diagnostic.
            response["offset"] = None
            response["diagnostic"] = {
                "note": "两轮未能在同一整数偏移下同时达到最低配对数，"
                        "以下偏移与配对仅为联合最大配对诊断，不是校准结论。",
                "offset": self.offset,
                "min_pair_count": self.min_pair_count,
                "total_pair_count": self.total_pair_count,
                "pair_count_1": self.rounds[0].pair_count,
                "pair_count_2": self.rounds[1].pair_count,
                "residual_abs_sum": self.residual_abs_sum,
                "max_abs_residual": self.max_abs_residual,
                "rounds": rounds,
            }
        return response


def _build_round(
    A: Sequence[int],
    B: Sequence[int],
    offset: int,
    tol: int,
) -> RoundResult:
    c = _residual_matrix(A, B, offset)
    _obj, pairs = best_pairing_at(c, tol)
    pair_objs = tuple(
        Pair(
            index_a=i + 1,
            index_b=j + 1,
            a_time=A[i],
            corrected_b=B[j] + offset,
            residual=A[i] - (B[j] + offset),
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
    return RoundResult(
        pair_count=len(pair_objs),
        residual_abs_sum=sum(abs(p.residual) for p in pair_objs),
        max_abs_residual=max(
            (abs(p.residual) for p in pair_objs), default=0
        ),
        pairs=pair_objs,
        unpaired_a=unpaired_a,
        unpaired_b=unpaired_b,
    )


def solve_shared(
    A1: Sequence[int],
    B1: Sequence[int],
    A2: Sequence[int],
    B2: Sequence[int],
    offset_min: int,
    offset_max: int,
    tolerance: int,
    min_pairs: int,
) -> SharedCalibrationResult:
    """Choose ONE integer offset jointly for two acquisition rounds.

    Inputs are assumed to have been validated by the caller.
    """
    lo, hi = offset_min, offset_max

    matrices = [_raw_c_matrix(A1, B1), _raw_c_matrix(A2, B2)]
    edge_rounds = [
        [(i, j, matrices[0][i][j]) for i in range(len(A1))
         for j in range(len(B1))],
        [(i, j, matrices[1][i][j]) for i in range(len(A2))
         for j in range(len(B2))],
    ]

    # Phase 1: exact optimum of (min count, total count) and the integer
    # intervals W on which it is attained (plus one representative per
    # constant-count region for phase 2).
    best_counts, winning, reps = _phase1_winning_regions(
        edge_rounds, matrices, tolerance, lo, hi
    )

    # Phase 2: residual objectives only inside W.  Region representatives are
    # candidates too, so an interior point of a flat region wins correctly.
    candidates = _shared_candidate_offsets(
        edge_rounds, winning, tolerance, lo, hi, reps
    )

    # Maximise (min-count, total, -abs-sum, -max-abs, -offset).  Every
    # candidate lies in a phase-1 winning region, so the count tuple is
    # constant at best_counts by construction.
    best_score: Optional[Tuple[int, int, int, int, int]] = None
    best_offset = lo
    for d in candidates:
        m1 = pairing_metrics(matrices[0], d, tolerance)
        m2 = pairing_metrics(matrices[1], d, tolerance)
        k1, neg_s1, neg_max1 = m1
        k2, neg_s2, neg_max2 = m2
        assert (min(k1, k2), k1 + k2) == best_counts
        score = (
            min(k1, k2),
            k1 + k2,
            neg_s1 + neg_s2,
            min(neg_max1, neg_max2),
            -d,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_offset = d

    assert best_score is not None
    r1 = _build_round(A1, B1, best_offset, tolerance)
    r2 = _build_round(A2, B2, best_offset, tolerance)
    min_count = min(r1.pair_count, r2.pair_count)
    total = r1.pair_count + r2.pair_count
    abs_sum = r1.residual_abs_sum + r2.residual_abs_sum
    max_abs = max(r1.max_abs_residual, r2.max_abs_residual)

    sufficient = min_count >= min_pairs
    reason: Optional[str] = None
    if not sufficient:
        reason = (
            f"在共同偏移区间 [{offset_min}, {offset_max}] 纳秒、符合容差 "
            f"±{tolerance} 纳秒内，两轮记录无法在同一整数偏移下同时达到最低"
            f"配对数 {min_pairs}：可同时达到的最大较小配对数为 {min_count}，"
            f"第一轮实际 {r1.pair_count} 对、第二轮实际 {r2.pair_count} 对"
            f"（合计 {total} 对）。批间证据不足——两轮各自最优所需偏移不一致，"
            "无法用单一时钟偏移共同解释，故不给出校准偏移。"
        )

    return SharedCalibrationResult(
        mode="shared",
        offset=best_offset,
        min_pair_count=min_count,
        total_pair_count=total,
        residual_abs_sum=abs_sum,
        max_abs_residual=max_abs,
        rounds=(r1, r2),
        min_pairs=min_pairs,
        sufficient=sufficient,
        reason=reason,
    )
