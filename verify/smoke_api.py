"""Wide-offset-interval API smoke test for the one-shot verify service."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("APP_BASE_URL", "http://app:8080").rstrip("/")


def request(method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check(cond: bool, message: str) -> None:
    if not cond:
        print(f"SMOKE FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)
    print(f"  ok: {message}")


def main() -> int:
    print(f"target: {BASE_URL}")

    status, body = request("GET", "/healthz")
    check(status == 200 and body.get("status") == "ok", "health check is ok")

    # Wide 2,000,000,000 ns interval; the true shift is a large nonzero
    # integer that a nanosecond scan could never reach in time.
    shift = -765_432_198
    A = [10**12 + k * 137 + (k % 3) for k in range(10)]
    B = [a - shift for a in A]
    payload = {
        "probe_a": A,
        "probe_b": B,
        "offset_min": -1_000_000_000,
        "offset_max": 1_000_000_000,
        "tolerance": 3,
        "min_pairs": 8,
    }
    status, body = request("POST", "/api/calibrate", payload)
    check(status == 200, f"wide-interval calibrate HTTP 200 (got {status})")
    check(body.get("sufficient") is True, "wide-interval result sufficient")
    check(body.get("offset") == shift, f"exact offset recovered ({shift})")
    check(body.get("pair_count") == 10, "all 10 pulses paired")
    check(body.get("residual_abs_sum") == 0, "residual abs sum is 0")
    check(body.get("max_abs_residual") == 0, "max abs residual is 0")
    check(len(body.get("pairs", [])) == 10, "10 pair records returned")
    for p in body["pairs"]:
        check(
            p["corrected_b"] == p["a_time"] and p["residual"] == 0,
            f"pair A#{p['index_a']} corrected time matches",
        )

    # Insufficient coincidences: B is ~2e9 ns away from A, outside the whole
    # +/-1e9 offset interval, so zero pairs are possible. The API must report
    # the real maximum count and a reason, and must not present an offset as
    # calibration.
    bad = {
        "probe_a": [1, 2, 3, 4, 5, 6],
        "probe_b": [2_000_000_000 + k * 10 for k in range(6)],
        "offset_min": -1_000_000_000,
        "offset_max": 1_000_000_000,
        "tolerance": 5,
        "min_pairs": 4,
    }
    status, body = request("POST", "/api/calibrate", bad)
    check(status == 200, "insufficient case HTTP 200")
    check(body.get("sufficient") is False, "insufficient flag set")
    check(body.get("pair_count") == 0, "actual maximum pair count reported (0)")
    check(body.get("offset") is None, "no fabricated calibration offset")
    check(body.get("pairs") == [], "no pairs presented as a calibration")
    check(bool(body.get("reason")), "reason for shortfall provided")
    check(
        "diagnostic" in body and "offset" in body["diagnostic"],
        "diagnostic alignment kept separate",
    )

    # Validation error path.
    bad_input = dict(bad)
    bad_input["probe_a"] = [1, 2, 2, 4, 5, 6]
    status, body = request("POST", "/api/calibrate", bad_input)
    check(status == 400, "non-strict input rejected with 400")
    check("严格递增" in body.get("error", ""), "validation message returned")

    # -- shared-offset review smoke --------------------------------------
    # Two acquisition rounds share one true large shift; the shared offset
    # must explain both jointly (the API must not calibrate per round).
    shared_shift = 555_555_555
    A1 = [10**12 + k * 137 for k in range(9)]
    B1 = [a - shared_shift + (k % 2) * 2 for k, a in enumerate(A1)]
    A2 = [4 * 10**12 + k * 211 for k in range(7)]
    B2 = [a - shared_shift - (k % 2) for k, a in enumerate(A2)]
    shared = {
        "probe_a": A1, "probe_b": B1,
        "probe_a_2": A2, "probe_b_2": B2,
        "offset_min": -1_000_000_000,
        "offset_max": 1_000_000_000,
        "tolerance": 3, "min_pairs": 6,
        "shared_review": True,
    }
    status, body = request("POST", "/api/calibrate", shared)
    check(status == 200, f"shared review HTTP 200 (got {status})")
    check(body.get("mode") == "shared", "shared review mode reported")
    check(body.get("sufficient") is True, "shared review sufficient")
    check(body.get("offset") == shared_shift,
          f"shared offset recovered for both rounds ({shared_shift})")
    check(body.get("pair_count_1") == 9, "round 1 pairs 9")
    check(body.get("pair_count_2") == 7, "round 2 pairs 7")
    check(body.get("min_pair_count") == 7, "joint min pair count is 7")
    check(len(body.get("rounds", [])) == 2, "two round records returned")
    check("diagnostic" not in body, "no diagnostic block on success")

    # Batch drift: round 1 needs d=0, round 2 needs d=300; with ±3 ns
    # tolerance and wide gaps no single offset clears 6 pairs in both rounds.
    # The API must not present an offset and must report the jointly
    # attainable min count plus both rounds' actual counts and a reason.
    drift = {
        "probe_a": [k * 200 for k in range(1, 9)],
        "probe_b": [k * 200 for k in range(1, 9)],
        "probe_a_2": [10_000 + k * 211 for k in range(8)],
        "probe_b_2": [10_000 + k * 211 - 300 for k in range(8)],
        "offset_min": -1_000_000_000,
        "offset_max": 1_000_000_000,
        "tolerance": 3, "min_pairs": 6,
        "shared_review": True,
    }
    status, body = request("POST", "/api/calibrate", drift)
    check(status == 200, "shared drift case HTTP 200")
    check(body.get("sufficient") is False, "shared drift flagged insufficient")
    check(body.get("offset") is None, "no calibration offset on drift")
    check(
        body.get("min_pair_count")
        == min(body.get("pair_count_1"), body.get("pair_count_2")),
        "reported min count matches both rounds' counts",
    )
    check(body.get("min_pair_count", 99) < 6, "joint min count below gate")
    check(
        "批间证据不足" in body.get("reason", ""),
        "batch-evidence reason provided",
    )
    check(
        body.get("diagnostic", {}).get("rounds")
        and len(body["diagnostic"]["rounds"]) == 2,
        "two-round diagnostic kept separate from the conclusion",
    )

    # Second-round validation error path.
    bad_shared = dict(shared)
    bad_shared["probe_a_2"] = [1, 2, 3, 4, 5, 5]
    status, body = request("POST", "/api/calibrate", bad_shared)
    check(status == 400, "non-strict second-round input rejected with 400")
    check(body.get("field") == "probe_a_2", "error field identifies round 2 A")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
