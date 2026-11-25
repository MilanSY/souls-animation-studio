# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate original canonical fixture geometry and metrics, not a model."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backends/inbetweening"))
import humanoid_bench as bench
from backends.motion_process import MotionProcessError


class HumanoidBenchTests(unittest.TestCase):
    def test_half_turn_derivatives_are_invariant_under_quaternion_sign(self):
        q = (0., 1., 0., 0.)
        opposite = tuple(-x for x in q)
        times = (1, 2, 3)
        self.assertEqual(
            bench.derivative_peaks([bench.IDENTITY, q, bench.IDENTITY], times, rotations=True),
            bench.derivative_peaks([bench.IDENTITY, opposite, bench.IDENTITY], times, rotations=True),
        )

    def test_reference_poses_follow_authored_fixed_lengths_and_contacts(self):
        for clip in bench.CLIPS:
            with self.subTest(clip=clip):
                request, reference, targets = bench.fixture(clip)
                self.assertIn(13.5, reference)
                self.assertNotIn(25, request["sample_frames"])
                for pose in reference.values():
                    for j, (_, parent, rest) in enumerate(bench.LAYOUT):
                        if parent is not None:
                            self.assertEqual(pose["translations"][j], rest)
                measured = bench.metrics(reference, reference, targets)
                self.assertEqual(measured["joint_position_rms_m"], 0.)
                for contact in measured["contacts"].values():
                    self.assertLess(contact["max_target_error_m"], 1e-12)
                    self.assertLess(contact.get("max_grip_error_rad", 0), 1e-12)

    def test_cpu_both_modes_preserve_anchors_and_produce_distinct_timings(self):
        for clip in bench.CLIPS:
            with self.subTest(clip=clip):
                request, reference, targets = bench.fixture(clip)
                linear, _ = bench.interpolate(request, "Linear")
                smooth, _ = bench.interpolate(request, "Smooth")
                self.assertNotEqual(linear[3], smooth[3])
                for candidate in (linear, smooth):
                    self.assertEqual(tuple(sorted(candidate)), tuple(reference))
                    for anchor in request["anchors"]:
                        self.assertEqual(anchor, candidate[anchor["frame"]])
                    measured = bench.metrics(reference, candidate, targets)
                    self.assertEqual(measured["anchor_max_position_m"], 0.)
                    self.assertGreater(measured["root_trajectory_rms_m"], 0.)

    def test_cli_artifacts_distinguish_reference_from_baselines(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch) / "bench"
            bench.run(directory)
            for clip_dir in directory.iterdir():
                self.assertEqual({p.stem for p in clip_dir.glob("*.json")},
                                 {"request", "reference", "sparse", "linear", "smooth",
                                  "metrics", "continuity-fixture"})
                report = json.loads((clip_dir / "metrics.json").read_text(encoding="utf-8"))
                self.assertEqual(set(report), {"reference", "Linear", "Smooth"})

    def test_scores_only_the_exact_v1_free_grid_after_json_round_trip(self):
        clip = "two-hand-strike"
        request, reference, _ = bench.fixture(clip)
        job = uuid4().hex
        response = json.loads(json.dumps({
            "schema": "sas.inbetween.response", "version": 1, "request_id": job,
            "samples": [reference[f] for f in request["sample_frames"]],
        }))
        self.assertEqual(bench.score_external(clip, response, request_id=job)["joint_position_rms_m"], 0.)
        samples = response["samples"]
        for bad_samples in (samples[:-1], [samples[1], *samples[1:]],
                            [dict(samples[0], frame=25), *samples[1:]]):
            with self.assertRaises(MotionProcessError):
                bench.score_external(clip, dict(response, samples=bad_samples), request_id=job)
        with self.assertRaises(MotionProcessError):
            bench.score_external(clip, response, request_id=uuid4().hex)
        malformed = deepcopy(response)
        malformed["samples"][0]["rotations"][0] = [2., 0., 0., 0.]
        with self.assertRaises(MotionProcessError):
            bench.score_external(clip, malformed, request_id=job)

    def test_biased_full_humanoid_fixture_preserves_locks_and_reduces_peaks(self):
        for clip in bench.CLIPS:
            with self.subTest(clip=clip):
                request, reference, _ = bench.fixture(clip)
                corrected, report = bench.continuity_fixture(request, reference)
                by_frame = {pose["frame"]: pose for pose in corrected}
                self.assertEqual([by_frame[f] for f in bench.ANCHORS], request["anchors"])
                for signal in ("translation", "rotation"):
                    before, after = (report[key][signal] for key in ("before", "after"))
                    self.assertLessEqual(after["speed"]["peak"], before["speed"]["peak"])
                    self.assertLess(after["acceleration"]["peak"], before["acceleration"]["peak"])


if __name__ == "__main__":
    unittest.main()
