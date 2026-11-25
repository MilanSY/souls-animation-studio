# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic Blender-free tests of the humanoid continuity pass."""

from copy import deepcopy
from math import cos, hypot, pi, sin
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addon'))
from animation.humanoid_continuity import continuity_report, correct


OFFSETS = ((0.13, -0.2, 0.51), (-0.18, 0.26, 0.08))
IDENTITY = [1, 0, 0, 0]


def q(angle, axis=(0, 0, 1)):
    scale = sin(angle / 2) / hypot(*axis)
    return [cos(angle / 2), *(scale * x for x in axis)]


def pose(frame, root=(0, 0, 0), rotations=(IDENTITY, IDENTITY, IDENTITY)):
    return dict(frame=frame, translations=[list(root), *map(list, OFFSETS)],
                rotations=list(map(list, rotations)))


def fixture():
    def make(frame, bias=0):
        # Deliberately place the strongest bias on a non-root channel and
        # reverse quaternion signs on some samples without changing orientation.
        rotations = [q(.03 * frame, (1, 2, 3)), q(-.045 * frame + .21 * bias, (0, 1, 1)),
                     q(.02 * frame, (1, 0, 0))]
        if frame in (4, 4.5, 6):
            rotations[1] = [-x for x in rotations[1]]
        return pose(frame, (.012 * frame + .048 * bias, -.005 * frame - .026 * bias, .93),
                    rotations)

    frames = [1, 2, 3, 4, 4.5, 5, 6, 7, 8, 9]
    profile = [0, .04, .48, .95, 1, .95, .48, .04, 0, 0]
    return [make(frame) for frame in (1, 4.5, 9)], list(map(make, frames, profile))


class HumanoidContinuityTests(unittest.TestCase):
    def test_exact_fractional_lock_and_peak_reduction(self):
        anchors, raw = fixture()
        source = deepcopy((anchors, raw))
        fixed = correct(anchors, raw)
        self.assertEqual([r['frame'] for r in fixed], [r['frame'] for r in raw])
        self.assertEqual(fixed[4], anchors[1])
        self.assertIsNot(fixed[4]['rotations'][1], anchors[1]['rotations'][1])
        self.assertEqual(fixed[-2], raw[-2])  # zero-bias neighborhood of end anchor
        self.assertEqual((anchors, raw), source)
        for before, after in zip(raw, fixed, strict=True):
            self.assertEqual(after['translations'][1:], before['translations'][1:])
            for rotation in after['rotations']:
                self.assertAlmostEqual(hypot(*rotation), 1)
        report = continuity_report(anchors, raw, fixed)
        self.assertEqual(report['frames'][4], 4.5)
        for signal in ('translation', 'rotation'):
            before, after = (report[stage][signal] for stage in ('before', 'after'))
            for derivative in ('speed', 'acceleration'):
                self.assertLess(after[derivative]['peak'], .45 * before[derivative]['peak'])
            nearby = before['anchor_neighborhoods'][4.5]
            self.assertEqual(nearby['stencil_frames'], (4, 4.5, 5))
            self.assertEqual(before['acceleration']['peak_at'], 4.5)
            self.assertEqual(before['acceleration']['peak_joint'], int(signal == 'rotation'))
            self.assertIsNone(before['anchor_neighborhoods'][1]['acceleration_norm'])

    def test_free_only_response_uses_separate_exact_anchor_predictions(self):
        anchors, raw = fixture()
        locked_frames = {p['frame'] for p in anchors}
        free = [p for p in raw if p['frame'] not in locked_frames]
        aux = [p for p in raw if p['frame'] in locked_frames]
        fixed = correct(anchors, free, anchor_predictions=aux)
        inline = correct(anchors, raw)
        self.assertEqual(fixed, tuple(p for p in inline if p['frame'] not in locked_frames))
        self.assertEqual(continuity_report(anchors, free, fixed),
                         continuity_report(anchors, raw, inline))
        with self.assertRaisesRegex(ValueError, 'exact anchor'):
            correct(anchors, free)
        with self.assertRaisesRegex(ValueError, 'exact anchor'):
            correct(anchors, free, anchor_predictions=[aux[0], dict(aux[1], frame=4), aux[2]])

    def test_nonuniform_stencils_use_vector_not_speed_difference(self):
        anchors = [pose(0), pose(.5, (1, 0, 0), (q(.2, (1, 0, 0)), IDENTITY, IDENTITY)),
                   pose(2, (1, 0, 0), (q(-.4, (1, 0, 0)), IDENTITY, IDENTITY))]
        report = continuity_report(anchors, (), (), fps=(2, 1))['before']
        linear = report['translation']
        self.assertEqual(linear['speed']['peak_at'], (0, .5))
        self.assertEqual(linear['speed']['peak'], 4)
        self.assertEqual(linear['speed']['min'], 0)
        self.assertEqual(linear['acceleration']['peak'], 8)
        angular = report['rotation']['anchor_neighborhoods'][.5]
        self.assertAlmostEqual(angular['speed_before'][0], .8)
        self.assertAlmostEqual(angular['speed_after'][0], .8)
        self.assertAlmostEqual(angular['delta_velocity_norm'][0], 1.6)
        self.assertAlmostEqual(angular['acceleration_norm'][0], 3.2)

    def test_shortest_arc_through_pi_and_sign_invariant_correction(self):
        def rotations(angle, sign=1):
            return (IDENTITY, IDENTITY, [sign * x for x in q(angle, (0, 1, 0))])

        anchors = [pose(0, rotations=rotations(pi - .2)),
                   pose(2, rotations=rotations(pi + .2, -1))]
        # Measured directly from two lock-only samples, the path is .4 rad,
        # never 2*pi-.4 even though the second orientation changes WXYZ sign.
        report = continuity_report(anchors, (), (), fps=(1, 1))['before']['rotation']
        self.assertAlmostEqual(report['speed']['peak'], .2)
        self.assertIsNone(report['acceleration']['peak'])
        raw = [pose(0, rotations=rotations(pi + .1)), pose(1, rotations=rotations(pi)),
               pose(2, rotations=rotations(pi + .2))]
        opposite = deepcopy(raw)
        opposite[0]['rotations'][2] = [-x for x in opposite[0]['rotations'][2]]
        self.assertEqual(correct(anchors, raw), correct(anchors, opposite))

    def test_overlapping_weights_do_not_double_a_constant_bias(self):
        anchors = [pose(0), pose(1.5), pose(3)]
        raw = [pose(f, (2, 0, 0), (q(.4), IDENTITY, IDENTITY))
               for f in (0, 1, 1.5, 2, 3)]
        fixed = correct(anchors, raw, radius=6)
        for row in fixed:
            self.assertAlmostEqual(row['translations'][0][0], 0)
            self.assertAlmostEqual(row['rotations'][0][3], 0)

    def test_noncommuting_rotation_uses_parent_frame_and_compact_radius(self):
        # From X to Y*X, the correction is a parent-frame Y rotation that
        # must be pre-multiplied. At a distance of radius/2 its weight is .5.
        def xy_rotation(y):
            c, s = cos(.2), sin(.2)
            a, b = cos(y/2), sin(y/2)
            return [a*c, a*s, b*c, -b*s]

        anchors = [pose(0, rotations=(xy_rotation(.6), IDENTITY, IDENTITY)), pose(8)]
        raw = [pose(f, rotations=(xy_rotation(0), IDENTITY, IDENTITY)) if f < 8 else pose(f)
               for f in (0, 2, 4, 5, 8)]
        fixed = correct(anchors, raw, radius=4)
        for value, expected in zip(fixed[1]['rotations'][0], xy_rotation(.3), strict=True):
            self.assertAlmostEqual(value, expected)
        self.assertEqual(fixed[2:4], tuple(raw[2:4]))

    def test_reject_offsets_changed_anchors_mismatched_grid_and_bad_radius(self):
        anchors, raw = fixture()
        fixed = correct(anchors, raw)
        for radius in (0, -3, float('inf'), True):
            with self.assertRaisesRegex(ValueError, 'radius'):
                correct(anchors, raw, radius=radius)
        with self.assertRaisesRegex(ValueError, 'same frames'):
            continuity_report(anchors, raw, fixed[:-1])
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            continuity_report(anchors, raw, raw)
        raw[4]['translations'][1][0] += .01
        with self.assertRaisesRegex(ValueError, 'fixed offsets'):
            correct(anchors, raw)


if __name__ == '__main__':
    unittest.main()
