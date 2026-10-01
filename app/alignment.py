"""Joint selection of an integer clock offset and order-preserving pairings.

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

Shared-offset review
--------------------

The same physical probe pair is measured twice (before and after changing
the reference-source strength).  Quality control must confirm that **one**
clock offset explains *both* rounds; calibrating each round independently
would hide between-batch drift.  In shared mode the solver therefore chooses

    1. one integer offset ``d`` (identical for both rounds);
    2. one order-preserving one-to-one matching per round;

and lexicographically optimises, jointly over the offset:

    1. maximise  min(pair_count_1, pair_count_2);
    2. maximise  pair_count_1 + pair_count_2;
    3. minimise  the merged residual absolute sum over both rounds;
    4. minimise  the merged largest absolute residual (which may be attained
       by a pair in *either* round);

then taking the smallest integer offset and the lexicographically smallest
input-index sequences (round 1 first, then round 2).  This is *not*
"calibrate each round separately and intersect the results": a single offset
is scored against both rounds simultaneously.

If either round falls short of ``min_pairs`` at every shared offset, no
calibration offset is returned; instead the largest simultaneously
achievable smaller-round pair count together with both rounds' actual counts
is reported.

Why a 2-billion-nanosecond interval needs no nanosecond scan
------------------------------------------------------------

Edge (i, j) is feasible at offset d iff |(a_i - b_j) - d| <= T, i.e. the
feasible window is [c_ij - T, c_ij + T].  The solver builds a finite,
provably complete candidate-offset set and runs an O(n*m) DP per candidate:

* c_ij - T, c_ij, c_ij + T, c_ij + T + 1 for every edge: feasibility-window
  boundaries (first/last feasible integer, first infeasible integer) and
  residual kinks.  A maximum-pair matching is attainable at a window
  boundary, and the absolute-residual-sum minimiser is either a matched
  c_ij (median) or a clamped window boundary c_ij +/- T;
* floor/ceil of (c_p + c_q)/2 for two edges whose residual extremes can be
  attained together.  Edges of the *same* round must be co-orderable
  (indices point the same way) so one matching can contain both; an edge of
  round 1 and one of round 2 are always independent, so every cross-round
  pair is considered.  In both cases both edges must be within tolerance at
  the midpoint (|c_p - c_q| <= 2T).  For any fixed pair of matchings,
  max(c_max - d, d - c_min) has its integer minimiser at that midpoint
  (half-integers take the smaller point, via the smallest-offset rule).
  Cross-round midpoints are essential in shared mode because the merged
  largest residual may be determined by one pair from each round;
* the interval endpoints.

With n, m <= 24 the co-order / |c - c'| <= 2T filters keep this to a few
thousand to a few hundred thousand cheap DP runs (the score DP carries no
parent tuples; pairings are reconstructed only at the winning offset), and
every visited offset is derived from pairing critical values -- never from
scanning.  Correctness is backed by in-repo exhaustive and dense-scan
differential tests for both the single- and two-round problems.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# A pairing's lexicographic objective used *inside* the DP, maximised:
#   (pair_count, -abs_sum, -max_abs_residual, index_key)
# ``max`` on this tuple implements: more pairs, smaller abs sum, smaller
# largest absolute residual, then lexicographically smallest paired indices.
Objective = Tuple[int, int, int, Tuple[Tuple[int, int], ...]]
# Score-only objective (no index key), used on the bulk candidate sweep.
Score = Tuple[int, int, int]


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


def score_pairing_at(c: Sequence[Sequence[int]], tol: int) -> Score:
    """Same DP objective as :func:`best_pairing_at` minus the index key.

    Only (pair_count, -abs_sum, -max_abs_residual) is needed during the
    staged sweep; the canonical index tie-break is applied by re-running
    ``best_pairing_at`` at the single winning offset.
    """
    n = len(c)
    m = len(c[0]) if n else 0

    dp: List[List[Optional[Score]]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    dp[0][0] = (0, 0, 0)

    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best: Optional[Score] = None

            if i > 0:
                up = dp[i - 1][j]
                if up is not None:
                    best = up
            if j > 0:
                left = dp[i][j - 1]
                if left is not None and (best is None or left > best):
                    best = left
            if i > 0 and j > 0:
                diag = dp[i - 1][j - 1]
                if diag is not None:
                    e = c[i - 1][j - 1]
                    if -tol <= e <= tol:
                        ae = e if e >= 0 else -e
                        cand: Score = (
                            diag[0] + 1,
                            diag[1] - ae,
                            min(diag[2], -ae),
                        )
                        if best is None or cand > best:
                            best = cand

            dp[i][j] = best

    final = dp[n][m]
    assert final is not None
    return final


def count_pairing_at(
    A: Sequence[int], B: Sequence[int], offset: int, tol: int
) -> int:
    """Maximum feasible pair count at ``offset`` (rolling-row DP)."""
    n = len(A)
    m = len(B)
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ai = A[i - 1] - offset
        for j in range(1, m + 1):
            v = prev[j]          # skip A_i
            if cur[j - 1] > v:   # skip B_j
                v = cur[j - 1]
            e = ai - B[j - 1]    # pair i with j
            if -tol <= e <= tol and prev[j - 1] + 1 > v:
                v = prev[j - 1] + 1
            cur[j] = v
        prev = cur
    return prev[m]


def _edges_in_some_lex_best_matching(
    A: Sequence[int], B: Sequence[int], offset: int, tol: int
) -> set:
    """Edges participating in *some* (count, then abs-sum) optimal matching.

    Forward/backward lexicographic DPs: edge (i, j) belongs to some optimal
    matching iff it is feasible and composing the best prefix, the edge and
    the best suffix reproduces the global optimum (counts add, costs add).
    Indices are 0-based; residuals are computed at ``offset``.
    """
    n, m = len(A), len(B)

    def res(i: int, j: int) -> int:
        return A[i] - (B[j] + offset)

    # f[i][j]: best (count, -cost) using A[:i], B[:j].
    f = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best = None
            if i > 0:
                best = f[i - 1][j]
            if j > 0 and (best is None or f[i][j - 1] > best):
                best = f[i][j - 1]
            if i > 0 and j > 0:
                r = res(i - 1, j - 1)
                if -tol <= r <= tol:
                    pc, qc = f[i - 1][j - 1]
                    cand = (pc + 1, qc - (r if r >= 0 else -r))
                    if best is None or cand > best:
                        best = cand
            f[i][j] = best

    # g[i][j]: best (count, -cost) using A[i:], B[j:].
    g = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n and j == m:
                continue
            best = None
            if i < n:
                best = g[i + 1][j]
            if j < m and (best is None or g[i][j + 1] > best):
                best = g[i][j + 1]
            if i < n and j < m:
                r = res(i, j)
                if -tol <= r <= tol:
                    sc, tc = g[i + 1][j + 1]
                    cand = (sc + 1, tc - (r if r >= 0 else -r))
                    if best is None or cand > best:
                        best = cand
            g[i][j] = best

    opt = f[n][m]
    edges: set = set()
    for i in range(n):
        for j in range(m):
            r = res(i, j)
            if -tol <= r <= tol:
                pc, qc = f[i][j]
                sc, tc = g[i + 1][j + 1]
                composed = (
                    pc + 1 + sc,
                    qc - (r if r >= 0 else -r) + tc,
                )
                if composed == opt:
                    edges.add((i, j))
    return edges


def _residual_matrix(A: Sequence[int], B: Sequence[int], offset: int):
    return [[a - b - offset for b in B] for a in A]


def _window_boundary_candidates(
    rounds_edges: Sequence[Sequence[Tuple[int, int, int]]],
    tol: int,
    lo: int,
    hi: int,
) -> List[int]:
    """Feasibility-window boundaries and residual kinks for every edge.

    For each edge (i, j) with c_ij = a_i - b_j: c-T (first feasible),
    c (residual kink), c+T (last feasible), c+T+1 (first infeasible),
    plus the interval endpoints.  The pair-count objective and the merged
    abs-residual-sum objective attain their optima on these points; the
    largest-residual midpoints are added later, only where they can matter.
    """
    cand = {lo, hi}
    for edges in rounds_edges:
        for _i, _j, cv in edges:
            for d in (cv - tol, cv, cv + tol, cv + tol + 1):
                if lo <= d <= hi:
                    cand.add(d)
    return sorted(cand)


def _add_extreme_midpoints(
    cand: set,
    edge_sets: Sequence[set],
    rounds_edges: Sequence[Sequence[Tuple[int, int, int]]],
    tol: int,
    lo: int,
    hi: int,
) -> None:
    """Add integer minimisers of the merged largest residual.

    Only edges occurring in *some* count- and abs-sum-optimal matching at a
    stage-2 record offset (``edge_sets``) are considered: the winning
    matching is fixed to such edges after the earlier objectives, so any
    other residual extreme can never decide the final tie-break.

    Two edges of the same round must be co-orderable to co-occur in one
    matching; edges from different rounds are unconstrained (independent
    matchings) -- cross-round midpoints are exactly what handles a merged
    largest residual attained by one pair from each round.
    """
    cmap = [
        {(i, j): cv for (i, j, cv) in edges} for edges in rounds_edges
    ]

    def emit(c1: int, c2: int) -> None:
        if c1 > c2:
            chi, clo = c1, c2
        else:
            chi, clo = c2, c1
        if chi - clo > 2 * tol:
            return  # both edges cannot lie within tolerance at one offset
        flo = max(lo, chi - tol)
        fhi = min(hi, clo + tol)
        if flo > fhi:
            return
        total = c1 + c2
        mid_floor = total // 2
        mid_ceil = -((-total) // 2)
        for md in (mid_floor, mid_ceil):
            d = md if flo <= md <= fhi else (flo if md < flo else fhi)
            if lo <= d <= hi:
                cand.add(d)

    for r, es in enumerate(edge_sets):
        es_list = sorted(es)
        for x, (i, j) in enumerate(es_list):
            c1 = cmap[r][(i, j)]
            for (i2, j2) in es_list[x + 1:]:
                if (i2 - i) * (j2 - j) > 0:
                    emit(c1, cmap[r][(i2, j2)])
            for r2, es2 in enumerate(edge_sets):
                if r2 == r:
                    continue
                for (i2, j2) in es2:
                    emit(c1, cmap[r2][(i2, j2)])


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


def _build_round(
    A: Sequence[int],
    B: Sequence[int],
    offset: int,
    tol: int,
) -> dict:
    """Canonical matching and derived stats for one round at ``offset``."""
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
    return {
        "pairs": pair_objs,
        "unpaired_a": unpaired_a,
        "unpaired_b": unpaired_b,
        "pair_count": len(pair_objs),
        "residual_abs_sum": sum(abs(p.residual) for p in pair_objs),
        "max_abs_residual": max(
            (abs(p.residual) for p in pair_objs), default=0
        ),
    }


def _pair_to_dict(p: Pair) -> dict:
    return {
        "index_a": p.index_a,
        "index_b": p.index_b,
        "a_time": p.a_time,
        "corrected_b": p.corrected_b,
        "residual": p.residual,
    }


def _unpaired_to_list(unpaired: Sequence[Unpaired]) -> list:
    return [{"index": u.index, "time": u.time} for u in unpaired]


def solve_multi(
    rounds: Sequence[Tuple[Sequence[int], Sequence[int]]],
    offset_min: int,
    offset_max: int,
    tolerance: int,
    min_pairs: int,
) -> Tuple[int, List[dict]]:
    """Solve the joint offset / pairing problem for one or two rounds.

    Returns ``(offset, round_results)``; inputs are assumed validated.
    Round results are dicts produced by :func:`_build_round`.

    The sweep is staged by the same lexicographic hierarchy instead of
    running a full DP at every theoretically relevant point:

    1. **Count stage** -- cheap count-only DPs at all feasibility-window
       boundaries/kinks: objectives 1-2 (smaller-round count, total count)
       are step functions of the offset and attain their optima on
       boundaries;
    2. **Cost stage** -- full (count, -abs-sum) DPs only at offsets tied on
       objectives 1-2: the merged absolute-residual sum of optimal matchings
       is convex piecewise-linear with kinks at matched ``c`` values, so its
       optimum within the count-optimal offset set is attained at a kink
       candidate;
    3. **Max-residual stage** -- collect edges occurring in *some* count- and
       cost-optimal matching at every stage-2 record offset, add midpoints
       of residual-extreme pairs (within and across rounds), and run full
       DPs at those points.  This is provably sufficient because the merged
       largest residual's integer minimiser, given optimal matchings, is the
       midpoint/clamp of two attained residual extremes.

    No offset is scanned nanosecond by nanosecond; every visited point comes
    from pairing critical values, and separate calibrations are never
    intersected -- both rounds are scored at one common offset.
    """
    lo, hi = offset_min, offset_max
    tol = tolerance

    rounds_edges = [
        [(i, j, a - b) for i, a in enumerate(A) for j, b in enumerate(B)]
        for (A, B) in rounds
    ]

    # -- Stage 1: count only -------------------------------------------------
    count_record: Optional[Tuple[int, int]] = None
    count_offsets: List[int] = []
    for d in _window_boundary_candidates(rounds_edges, tol, lo, hi):
        counts = [count_pairing_at(A, B, d, tol) for (A, B) in rounds]
        val = (min(counts), sum(counts))
        if count_record is None or val > count_record:
            count_record = val
            count_offsets = [d]
        elif val == count_record:
            count_offsets.append(d)

    assert count_record is not None

    # -- Stage 2: merged abs-sum cost ---------------------------------------
    cost_record: Optional[Tuple[int, int, int]] = None
    cost_offsets: List[int] = []
    for d in count_offsets:
        scores = [
            score_pairing_at(_residual_matrix(A, B, d), tol)
            for (A, B) in rounds
        ]
        counts = [s[0] for s in scores]
        val = (min(counts), sum(counts), sum(s[1] for s in scores))
        if cost_record is None or val > cost_record:
            cost_record = val
            cost_offsets = [d]
        elif val == cost_record:
            cost_offsets.append(d)

    assert cost_record is not None

    # -- Stage 3: largest residual midpoints --------------------------------
    cand3 = set(cost_offsets)
    edge_sets = []
    for (A, B) in rounds:
        per_round_edges: set = set()
        for d in cost_offsets:
            per_round_edges |= _edges_in_some_lex_best_matching(A, B, d, tol)
        edge_sets.append(per_round_edges)
    _add_extreme_midpoints(cand3, edge_sets, rounds_edges, tol, lo, hi)

    # Full objective at the midpoint candidates; the smallest offset wins
    # the final tie.
    best_score: Optional[Tuple[int, int, int, int, int]] = None
    best_offset = lo
    for d in cand3:
        scores = [
            score_pairing_at(_residual_matrix(A, B, d), tol)
            for (A, B) in rounds
        ]
        counts = [s[0] for s in scores]
        score = (
            min(counts),
            sum(counts),
            sum(s[1] for s in scores),
            min(s[2] for s in scores),  # -merged max abs
            -d,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_offset = d

    assert best_score is not None

    results = [
        _build_round(A, B, best_offset, tol) for (A, B) in rounds
    ]
    return best_offset, results


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
        pairs = [_pair_to_dict(p) for p in self.pairs]
        response = {
            "offset": self.offset,
            "pair_count": self.pair_count,
            "residual_abs_sum": self.residual_abs_sum,
            "max_abs_residual": self.max_abs_residual,
            "pairs": pairs,
            "unpaired_a": _unpaired_to_list(self.unpaired_a),
            "unpaired_b": _unpaired_to_list(self.unpaired_b),
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
    """Solve the single-round joint offset / pairing problem exactly."""
    offset, (r,) = solve_multi(
        [(A, B)], offset_min, offset_max, tolerance, min_pairs
    )

    sufficient = r["pair_count"] >= min_pairs
    reason: Optional[str] = None
    if not sufficient:
        reason = (
            f"在偏移区间 [{offset_min}, {offset_max}] 纳秒、符合容差 "
            f"±{tolerance} 纳秒内，两台探头最多只能形成 {r['pair_count']} 对"
            f"符合事件（要求至少 {min_pairs} 对），无法形成足够的符合事件，"
            "故不给出校准结论。"
        )

    return CalibrationResult(
        offset=offset,
        pair_count=r["pair_count"],
        residual_abs_sum=r["residual_abs_sum"],
        max_abs_residual=r["max_abs_residual"],
        pairs=r["pairs"],
        unpaired_a=r["unpaired_a"],
        unpaired_b=r["unpaired_b"],
        min_pairs=min_pairs,
        sufficient=sufficient,
        reason=reason,
    )


@dataclass(frozen=True)
class SharedCalibrationResult:
    """Two-round shared-offset review result."""

    offset: int
    rounds: Tuple[dict, ...]  # built by _build_round, in round order
    min_pair_count: int
    total_pair_count: int
    residual_abs_sum: int
    max_abs_residual: int
    min_pairs: int
    sufficient: bool
    reason: Optional[str]

    def _round_to_dict(self, r: dict, include_pairs: bool) -> dict:
        return {
            "offset": self.offset if include_pairs else None,
            "pair_count": r["pair_count"],
            "residual_abs_sum": r["residual_abs_sum"],
            "max_abs_residual": r["max_abs_residual"],
            "pairs": [_pair_to_dict(p) for p in r["pairs"]] if include_pairs else [],
            "unpaired_a": _unpaired_to_list(r["unpaired_a"]),
            "unpaired_b": _unpaired_to_list(r["unpaired_b"]),
        }

    def to_dict(self) -> dict:
        counts = [r["pair_count"] for r in self.rounds]
        response = {
            "shared_offset_review": True,
            "offset": self.offset if self.sufficient else None,
            "sufficient": self.sufficient,
            "min_pairs": self.min_pairs,
            "min_pair_count": self.min_pair_count,
            "total_pair_count": self.total_pair_count,
            "round_pair_counts": counts,
            "residual_abs_sum": self.residual_abs_sum,
            "max_abs_residual": self.max_abs_residual,
            "reason": self.reason,
            "rounds": [
                {"round": k + 1, **self._round_to_dict(r, self.sufficient)}
                for k, r in enumerate(self.rounds)
            ],
        }

        if not self.sufficient:
            # Neither the page nor the API may present a calibration offset
            # when either round misses the threshold under a shared offset.
            # The best shared alignment survives only as a diagnostic.
            response["diagnostic"] = {
                "note": "共享偏移复核未通过：以下偏移与两轮配对仅为"
                        "“可同时达到的最大较小配对数”对齐诊断，"
                        "不是校准结论。",
                "offset": self.offset,
                "min_pair_count": self.min_pair_count,
                "total_pair_count": self.total_pair_count,
                "residual_abs_sum": self.residual_abs_sum,
                "max_abs_residual": self.max_abs_residual,
                "rounds": [
                    {
                        "round": k + 1,
                        **self._round_to_dict(r, True),
                    }
                    for k, r in enumerate(self.rounds)
                ],
            }
        return response


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
    """Two rounds, one shared integer offset; inputs assumed validated."""
    offset, rounds = solve_multi(
        [(A1, B1), (A2, B2)],
        offset_min,
        offset_max,
        tolerance,
        min_pairs,
    )
    counts = [r["pair_count"] for r in rounds]
    min_count = min(counts)
    total_count = sum(counts)
    merged_cost = sum(r["residual_abs_sum"] for r in rounds)
    merged_max = max(r["max_abs_residual"] for r in rounds)

    sufficient = min_count >= min_pairs
    reason: Optional[str] = None
    if not sufficient:
        short = [
            f"第 {k + 1} 轮" for k, c in enumerate(counts) if c < min_pairs
        ]
        reason = (
            "共享偏移复核未通过：在偏移区间 "
            f"[{offset_min}, {offset_max}] 纳秒、符合容差 ±{tolerance} 纳秒、"
            f"每轮至少 {min_pairs} 对的共同条件下，两轮可同时达到的"
            f"最大较小配对数仅为 {min_count} 对（第 1 轮实际 {counts[0]} 对、"
            f"第 2 轮实际 {counts[1]} 对，未达门槛：{'、'.join(short)}）；"
            "不存在一个时钟偏移能在两轮同时解释足够多的符合事件，"
            "批间一致性证据不足，故不给出校准偏移。"
        )

    return SharedCalibrationResult(
        offset=offset,
        rounds=tuple(rounds),
        min_pair_count=min_count,
        total_pair_count=total_count,
        residual_abs_sum=merged_cost,
        max_abs_residual=merged_max,
        min_pairs=min_pairs,
        sufficient=sufficient,
        reason=reason,
    )
