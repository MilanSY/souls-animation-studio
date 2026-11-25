# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only continuity comparison for the trained Sprint 4 demo (JSON to stdout)."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

from sprint4_worker import JOINTS, decode, read_json, rotation, vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'addon'))  # use the production CPU module without bpy
from animation.cpu import CPUBackend, Pose, Request

FPS = 24
ANCHORS = (1, 7.5, 16, 25)
SIGNALS = ('root_m', 'tip_head_m', 'local_z_rad')
OFFSETS = [joint['rest_translation'] for joint in JOINTS[1:]]


def track(rows, frames):
    if [row['frame'] for row in rows] != list(frames):
        raise ValueError('unexpected frame list (order, duplicates, or coverage)')
    result = []
    for row in rows:
        root, *offsets = row['translations']
        if offsets != OFFSETS:
            raise ValueError('non-canonical offsets')
        angles = [rotation(list(q)) for q in row['rotations']]
        if len(angles) != len(JOINTS):
            raise ValueError('wrong number of rotations')
        result.append(vector(list(root), 3, 2) + angles)
    return np.asarray(result, dtype=np.float64)


def response_track(response, request):
    if (response['schema'], response['version'], response['request_id']) != (
        'sas.inbetween.response', 1, request['request_id']
    ):
        raise ValueError('response does not match the request')
    return track(response['samples'], request['sample_frames'])


def replay(weights, request):
    with tempfile.TemporaryDirectory(prefix='sas-sprint4-continuity-') as scratch:
        job = Path(scratch)
        (job / 'request.json').write_text(json.dumps(request, allow_nan=False), encoding='utf-8')
        subprocess.run([sys.executable, '-sEB', str(HERE / 'sprint4_worker.py'),
                        '--weights', str(weights.resolve())], cwd=job, check=True, timeout=30)
        return response_track(read_json(job / 'response.json'), request)


def cpu_track(request, mode):
    anchors = tuple(Pose(a['frame'], tuple(a['translations'][0]), tuple(map(tuple, a['rotations'])))
                    for a in request['anchors'])
    rows = CPUBackend().generate(Request(anchors, mode))
    samples = [dict(frame=p.frame, translations=[p.scalars, *OFFSETS], rotations=p.rotations)
               for p in rows]
    return track(samples, request['sample_frames'])


def signals(rows):
    root = rows[:, :3]
    angles = np.unwrap(rows[:, 3:], axis=0)
    a, ab = angles[:, 0], angles[:, 0] + angles[:, 1]
    tip = root + np.column_stack((.4*np.cos(a) + .3*np.cos(ab),
                                  .4*np.sin(a) + .3*np.sin(ab), np.zeros(len(rows))))
    return dict(zip(SIGNALS, (root, tip, angles), strict=True))


def derivatives(frames, points):
    dt = np.diff(frames) / FPS
    velocity = np.diff(points, axis=0) / dt[:, None]
    acceleration = np.diff(velocity, axis=0) / ((dt[:-1] + dt[1:]) / 2)[:, None]
    return velocity, acceleration


def rms(vectors):
    return float(np.sqrt(np.mean(np.sum(vectors**2, axis=1))))


def summary(index, vectors, reference):
    norm = np.linalg.norm(vectors, axis=1)
    peak = int(np.argmax(norm))
    return dict(rms=rms(vectors), peak=float(norm[peak]), peak_at=index[peak],
                error_rms=rms(vectors - reference))


def neighborhoods(frames, points):
    v, a = derivatives(frames, points)
    result = {}
    for frame in ANCHORS:
        i = frames.index(frame)
        left, right = i > 0, i < len(frames)-1
        result[str(frame)] = dict(
            stencil_frames=frames[max(0, i-1):i+2],
            speed_before=float(np.linalg.norm(v[i-1])) if left else None,
            speed_after=float(np.linalg.norm(v[i])) if right else None,
            delta_velocity_norm=float(np.linalg.norm(v[i]-v[i-1])) if left and right else None,
            acceleration_norm=float(np.linalg.norm(a[i-1])) if left and right else None,
        )
    return result


def compare(proof_path, weights):
    proof = read_json(proof_path)
    if (proof['schema'], proof['version']) != ('sas.demo.sprint4.proof', 1):
        raise ValueError('expected Sprint 4 demo proof v1')
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if digest != proof['training']['sha256'] or digest != proof['artifact_sha256']['sprint4-planar.f32']:
        raise ValueError('checkpoint SHA-256 differs from the demo proof')
    request = proof['request']
    decode(request)  # contract, rate, chain, pose validation and integer samples
    anchors = track(request['anchors'], ANCHORS)
    free = request['sample_frames']
    knots = sorted([*ANCHORS, *free])
    anchor_idx = [knots.index(f) for f in ANCHORS]
    free_idx = [knots.index(f) for f in free]
    reference = track(proof['reference_samples'], knots)
    np.testing.assert_allclose(reference[anchor_idx], anchors, rtol=0, atol=2e-6)
    mlp = response_track(proof['response'], request)
    replayed = replay(weights, request)
    np.testing.assert_allclose(replayed, mlp, rtol=0, atol=2e-6)

    reference[anchor_idx] = anchors  # same locked poses, never model predictions
    tracks = {'reference': reference}
    for mode, free_rows in {'MLP': mlp, 'Linear': cpu_track(request, 'Linear'),
                            'Smooth': cpu_track(request, 'Smooth')}.items():
        rows = tracks[mode] = reference.copy()
        rows[free_idx] = free_rows

    grid = list(range(1, 26))
    grid_idx = [knots.index(f) for f in grid]
    ref = signals(reference)
    report = dict(fps=FPS, frames_24hz=grid, knot_frames=knots, anchor_frames=ANCHORS,
                  free_frames=free, checkpoint_sha256=digest,
                  replay_max_abs=dict(root_m=float(np.max(np.abs(replayed[:, :3] - mlp[:, :3]))),
                                      local_z_rad=float(np.max(np.abs(replayed[:, 3:] - mlp[:, 3:])))),
                  modes={})
    for mode, rows in tracks.items():
        metrics = report['modes'][mode] = {}
        for name, points in signals(rows).items():
            position_error = points[free_idx] - ref[name][free_idx]
            if name != 'local_z_rad' and mode in ('MLP', 'Linear'):
                key = 'root_rmse_m' if name == 'root_m' else 'tip_rmse_m'
                label = 'AI / MLP' if mode == 'MLP' else 'CPU / Linear'
                np.testing.assert_allclose(rms(position_error), proof['comparison'][label][key],
                                           rtol=0, atol=2e-6)
            v, a = derivatives(grid, points[grid_idx])
            ref_v, ref_a = derivatives(grid, ref[name][grid_idx])
            metrics[name] = dict(
                free_pose_error_rms=rms(position_error),
                speed_24hz=summary(list(zip(grid[:-1], grid[1:], strict=True)), v, ref_v),
                acceleration_24hz=summary(grid[1:-1], a, ref_a),
                anchor_neighborhoods=neighborhoods(knots, points),
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, default=ROOT / 'demo/sprint4-planar-preuve.json')
    parser.add_argument('--weights', type=Path, default=ROOT / 'demo/sprint4-planar.f32')
    args = parser.parse_args()
    print(json.dumps(compare(args.proof, args.weights), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
