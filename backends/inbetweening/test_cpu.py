# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the CPU backend, runnable under Python without Blender."""

from math import cos, pi, sin
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon"))
from animation.cpu import CPUBackend, Pose, Request


IDENTITY = (1, 0, 0, 0)


class CPUBackendTests(unittest.TestCase):
    def test_multisegment_interpolation_and_fractional_locks(self):
        anchors = tuple(Pose(frame, (x,), ()) for frame, x in [(-1.5, 0), (2, 7), (5, 16)])
        result = CPUBackend().generate(Request(anchors, "Linear"))
        self.assertEqual([pose.frame for pose in result], [-1, 0, 1, 3, 4])
        for pose, position in zip(result, [1, 3, 5, 10, 13], strict=True):
            self.assertAlmostEqual(pose.scalars[0], position)

    def test_smoothstep_and_shortest_arc_quaternion(self):
        half_angle = pi / 3
        end = (-cos(half_angle), 0, 0, -sin(half_angle))
        anchors = (Pose(0, (0,), (IDENTITY,)), Pose(4, (16,), (end,)))
        for mode, fraction in [("Linear", 1 / 4), ("Smooth", 5 / 32)]:
            sample = CPUBackend().generate(Request(anchors, mode))[0]
            self.assertEqual(sample.scalars[0], 16 * fraction)
            w, x, y, z = sample.rotations[0]
            self.assertAlmostEqual(w, cos(fraction * half_angle))
            self.assertEqual((x, y), (0, 0))
            self.assertAlmostEqual(z, sin(fraction * half_angle))
        self.assertEqual(anchors[-1].rotations[0], end)

    def test_antipodal_quaternions(self):
        anchors = (Pose(0, (), (IDENTITY,)), Pose(2, (), ((-1, 0, 0, 0),)))
        self.assertEqual(CPUBackend().generate(Request(anchors, "Linear"))[0].rotations[0], IDENTITY)

    def test_invalid_requests(self):
        a = Pose(0, (), (IDENTITY,))
        for anchors in [(a,), (a, a), (a, Pose(3, (), ((0, 0, 0, 0),)))]:
            with self.assertRaises(ValueError):
                CPUBackend().generate(Request(anchors, "Linear"))


if __name__ == "__main__":
    unittest.main()
