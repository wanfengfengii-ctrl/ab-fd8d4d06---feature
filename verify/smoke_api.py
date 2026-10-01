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

    # -- shared-offset review ----------------------------------------------
    # Two consistent rounds (same true shift, different source strengths):
    # one shared integer offset must be recovered exactly over the wide
    # interval, pairing every pulse in both rounds.
    shared_shift = 654_321_098
    A1 = [10**12 + k * 137 + (k % 3) for k in range(10)]
    B1 = [a - shared_shift for a in A1]
    A2 = [5 * 10**12 + k * 91 + (k % 2) for k in range(12)]
    B2 = [a - shared_shift for a in A2]
    shared_ok = {
        "probe_a": A1,
        "probe_b": B1,
        "round2_probe_a": A2,
        "round2_probe_b": B2,
        "shared_offset_review": True,
        "offset_min": -1_000_000_000,
        "offset_max": 1_000_000_000,
        "tolerance": 3,
        "min_pairs": 8,
    }
    status, body = request("POST", "/api/calibrate", shared_ok)
    check(status == 200, f"shared review HTTP 200 (got {status})")
    check(body.get("sufficient") is True, "shared review sufficient")
    check(body.get("shared_offset_review") is True, "shared review flag echoed")
    check(body.get("offset") == shared_shift,
          f"shared exact offset recovered ({shared_shift})")
    check(body.get("round_pair_counts") == [10, 12],
          "both rounds report actual pair counts")
    check(body.get("min_pair_count") == 10, "smaller round count is 10")
    check(body.get("total_pair_count") == 22, "total pair count is 22")
    check(body.get("residual_abs_sum") == 0, "merged residual abs sum is 0")
    check(body.get("max_abs_residual") == 0, "merged max abs residual is 0")
    rounds = body.get("rounds", [])
    check(len(rounds) == 2, "two round blocks returned")
    check([len(r.get("pairs", [])) for r in rounds] == [10, 12],
          "pair details present for both rounds")

    # Between-batch drift: each round alone calibrates (shifts 0 and 200),
    # but no single offset explains both.  The API must refuse an offset and
    # report the simultaneously achievable minimum and both actual counts.
    A = [100 + k * 137 for k in range(10)]
    drift = {
        "probe_a": A,
        "probe_b": list(A),
        "round2_probe_a": A,
        "round2_probe_b": [a - 200 for a in A],
        "shared_offset_review": True,
        "offset_min": -1000,
        "offset_max": 1000,
        "tolerance": 5,
        "min_pairs": 8,
    }
    status, body = request("POST", "/api/calibrate", drift)
    check(status == 200, "drift review HTTP 200")
    check(body.get("sufficient") is False, "drift review not sufficient")
    check(body.get("offset") is None, "no calibration offset on drift")
    check(body.get("round_pair_counts") == [10, 0],
          "both rounds' actual pair counts reported")
    check(body.get("min_pair_count") == 0,
          "simultaneously achievable minimum count reported (0)")
    check(all(r.get("pairs") == [] for r in body.get("rounds", [])),
          "no round presents pairs as a calibration")
    check(all(r.get("offset") is None for r in body.get("rounds", [])),
          "no round leaks an offset")
    check("批间一致性证据不足" in body.get("reason", ""),
          "insufficient between-batch evidence reason given")
    diag = body.get("diagnostic", {})
    check("offset" in diag and len(diag.get("rounds", [])) == 2,
          "diagnostic shared alignment kept separate")

    # Shared review without second-round input is a validation error.
    missing = dict(drift)
    del missing["round2_probe_a"]
    del missing["round2_probe_b"]
    status, body = request("POST", "/api/calibrate", missing)
    check(status == 400, "missing round 2 rejected with 400")
    check(body.get("field", "").startswith("round2_"),
          "error field points at round 2 input")

    # Disabled review stays backward-compatible: round2_* keys are ignored
    # and the single-round response shape is preserved.
    legacy = dict(shared_ok)
    legacy["shared_offset_review"] = False
    status, body = request("POST", "/api/calibrate", legacy)
    check(status == 200, "legacy single-round request HTTP 200")
    check("rounds" not in body and "shared_offset_review" not in body,
          "legacy response shape unchanged")
    check(body.get("offset") == shared_shift and body.get("pair_count") == 10,
          "legacy response calibrates the first round only")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
