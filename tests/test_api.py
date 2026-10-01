"""HTTP/API tests run against the real server in a background thread."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from urllib.request import Request

from app.server import build_server


class ServerHarness:
    def __init__(self):
        # Port 0 -> the OS assigns a free port.
        self.server = build_server(0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            f"http://127.0.0.1:{self.port}/api/calibrate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=10
            ) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers


class ApiTests(unittest.TestCase):
    def test_healthz(self):
        with ServerHarness() as h:
            status, body, _ = h.get("/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"status": "ok"})

    def test_index_served(self):
        with ServerHarness() as h:
            status, body, headers = h.get("/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("双探头".encode(), body)

    def test_calibrate_success_wide_interval(self):
        with ServerHarness() as h:
            shift = 424242424
            A = [1000 + k * 53 for k in range(8)]
            B = [a - shift for a in A]
            status, body = h.post({
                "probe_a": A,
                "probe_b": B,
                "offset_min": -1_000_000_000,
                "offset_max": 1_000_000_000,
                "tolerance": 5,
                "min_pairs": 6,
            })
            self.assertEqual(status, 200, body)
            self.assertTrue(body["sufficient"])
            self.assertEqual(body["offset"], shift)
            self.assertEqual(body["pair_count"], 8)
            for p in body["pairs"]:
                self.assertEqual(p["a_time"], p["corrected_b"])
                self.assertEqual(p["residual"], 0)
            self.assertEqual(body["unpaired_a"], [])
            self.assertEqual(body["unpaired_b"], [])

    def test_insufficient_reports_actual_count_no_fabrication(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [100, 102, 104, 106, 108, 110],
                "offset_min": -50,
                "offset_max": 50,
                "tolerance": 3,
                "min_pairs": 4,
            })
            self.assertEqual(status, 200)
            self.assertFalse(body["sufficient"])
            self.assertEqual(body["pair_count"], 0)
            self.assertIsNone(body["offset"])
            self.assertEqual(body["pairs"], [])
            self.assertIn("diagnostic", body)
            self.assertIn("无法形成足够的符合事件", body["reason"])
            self.assertIn("0", body["reason"])

    def test_boundary_min_pairs_exactly_met(self):
        with ServerHarness() as h:
            # Exactly 4 pairable, 2 noise pulses on each side.
            A = [10, 20, 30, 40, 1000, 1010]
            B = [10, 20, 30, 40, 2000, 2010]
            status, body = h.post({
                "probe_a": A, "probe_b": B,
                "offset_min": -5, "offset_max": 5,
                "tolerance": 0, "min_pairs": 4,
            })
            self.assertEqual(status, 200)
            self.assertTrue(body["sufficient"])
            self.assertEqual(body["pair_count"], 4)
            self.assertEqual(body["offset"], 0)

    def test_string_inputs_accepted(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": "1, 2, 3, 4, 5, 6",
                "probe_b": "1 2 3 4 5 6",
                "offset_min": "-2",
                "offset_max": "2",
                "tolerance": "0",
                "min_pairs": "6",
            })
            self.assertEqual(status, 200, body)
            self.assertTrue(body["sufficient"])
            self.assertEqual(body["offset"], 0)

    def test_non_strictly_increasing_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 2, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "offset_min": 0, "offset_max": 0,
                "tolerance": 0, "min_pairs": 1,
            })
            self.assertEqual(status, 400)
            self.assertIn("严格递增", body["error"])
            self.assertEqual(body["field"], "probe_a")

    def test_wrong_pulse_count_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "offset_min": 0, "offset_max": 0,
                "tolerance": 0, "min_pairs": 1,
            })
            self.assertEqual(status, 400)
            self.assertIn("6–24", body["error"])

    def test_bad_interval_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "offset_min": 10, "offset_max": 1,
                "tolerance": 0, "min_pairs": 1,
            })
            self.assertEqual(status, 400)
            self.assertIn("下限不能大于上限", body["error"])

    def test_non_integer_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "offset_min": 0.5, "offset_max": 1,
                "tolerance": 0, "min_pairs": 1,
            })
            self.assertEqual(status, 400)
            self.assertIn("整数", body["error"])

    def test_unknown_route(self):
        with ServerHarness() as h:
            status, _, _ = h.get("/nope")
            self.assertEqual(status, 404)

    # -- shared-offset review ------------------------------------------------
    def test_shared_review_consistent_rounds(self):
        with ServerHarness() as h:
            shift = -543_210_987
            A1 = [10**12 + k * 71 for k in range(8)]
            B1 = [a - shift for a in A1]
            A2 = [2 * 10**12 + k * 53 for k in range(10)]
            B2 = [a - shift for a in A2]
            status, body = h.post({
                "probe_a": A1,
                "probe_b": B1,
                "round2_probe_a": A2,
                "round2_probe_b": B2,
                "shared_offset_review": True,
                "offset_min": -1_000_000_000,
                "offset_max": 1_000_000_000,
                "tolerance": 3,
                "min_pairs": 6,
            })
            self.assertEqual(status, 200, body)
            self.assertTrue(body["sufficient"])
            self.assertTrue(body["shared_offset_review"])
            self.assertEqual(body["offset"], shift)
            self.assertEqual(body["round_pair_counts"], [8, 10])
            self.assertEqual(body["min_pair_count"], 8)
            self.assertEqual(body["total_pair_count"], 18)
            self.assertEqual(len(body["rounds"]), 2)
            self.assertEqual(
                [len(r["pairs"]) for r in body["rounds"]], [8, 10]
            )
            for r in body["rounds"]:
                self.assertEqual(r["offset"], shift)

    def test_shared_review_drift_no_offset(self):
        with ServerHarness() as h:
            A = [100 + k * 137 for k in range(10)]
            B1 = list(A)
            B2 = [a - 200 for a in A]
            status, body = h.post({
                "probe_a": A,
                "probe_b": B1,
                "round2_probe_a": A,
                "round2_probe_b": B2,
                "shared_offset_review": True,
                "offset_min": -1000,
                "offset_max": 1000,
                "tolerance": 5,
                "min_pairs": 8,
            })
            self.assertEqual(status, 200, body)
            self.assertFalse(body["sufficient"])
            self.assertIsNone(body["offset"])
            self.assertEqual(body["round_pair_counts"], [10, 0])
            self.assertEqual(body["min_pair_count"], 0)
            for r in body["rounds"]:
                self.assertEqual(r["pairs"], [])
                self.assertIsNone(r["offset"])
            self.assertIn("批间一致性证据不足", body["reason"])
            self.assertIn("10", body["reason"])
            diag = body["diagnostic"]
            self.assertIn("offset", diag)
            self.assertEqual(len(diag["rounds"]), 2)

    def test_shared_review_missing_round2_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "shared_offset_review": True,
                "offset_min": -5,
                "offset_max": 5,
                "tolerance": 0,
                "min_pairs": 4,
            })
            self.assertEqual(status, 400)
            self.assertEqual(body["field"], "round2_probe_a")

    def test_shared_review_round2_bad_count_rejected(self):
        with ServerHarness() as h:
            status, body = h.post({
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "round2_probe_a": [1, 2, 3, 4, 5],
                "round2_probe_b": [1, 2, 3, 4, 5, 6],
                "shared_offset_review": True,
                "offset_min": -5,
                "offset_max": 5,
                "tolerance": 0,
                "min_pairs": 4,
            })
            self.assertEqual(status, 400)
            self.assertEqual(body["field"], "round2_probe_a")
            self.assertIn("6–24", body["error"])

    def test_shared_flag_must_be_boolean(self):
        with ServerHarness() as h:
            payload = {
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "shared_offset_review": "true",
                "offset_min": -5,
                "offset_max": 5,
                "tolerance": 0,
                "min_pairs": 4,
            }
            status, body = h.post(payload)
            self.assertEqual(status, 400)
            self.assertEqual(body["field"], "shared_offset_review")

    def test_round2_ignored_when_review_disabled(self):
        # Backward compatibility: without the flag the request/response stay
        # exactly the single-round shape, even if round2_* keys are present.
        with ServerHarness() as h:
            payload = {
                "probe_a": [1, 2, 3, 4, 5, 6],
                "probe_b": [1, 2, 3, 4, 5, 6],
                "round2_probe_a": [1, 2, 3, 4, 5],  # invalid but ignored
                "offset_min": -5,
                "offset_max": 5,
                "tolerance": 0,
                "min_pairs": 6,
            }
            status, body = h.post(payload)
            self.assertEqual(status, 200, body)
            self.assertTrue(body["sufficient"])
            self.assertNotIn("shared_offset_review", body)
            self.assertNotIn("rounds", body)
            self.assertEqual(body["offset"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
