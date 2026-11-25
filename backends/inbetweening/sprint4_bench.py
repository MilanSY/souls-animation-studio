# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux CPU qualification: run actual v1 subprocesses on unseen synthetic clips."""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import resource
import statistics
import subprocess
import sys
from time import perf_counter

import numpy as np

from sprint4_train import draw, trajectory
from sprint4_worker import JOINTS, SPAN, decode, load_weights, pose, predict, rotation


HERE = Path(__file__).resolve().parent
BENCH_SEED = 4403
sys.path.insert(0, str(HERE.parents[1] / 'addon'))
from animation.cpu import CPUBackend, Pose, Request


def make_case(rng, count, token):
    start = -2.5
    candidates = np.arange(1, SPAN*2) / 2
    frames = [start, *(start + np.sort(rng.choice(candidates, count-2, replace=False))), start+SPAN]
    sample_frames = [f for f in range(math.floor(start)+1, math.ceil(frames[-1])) if f not in frames]
    times = (np.asarray(frames + sample_frames, np.float32)[None, :] - start) / SPAN
    track = trajectory(draw(rng, 1), times)[0]
    request = dict(schema='sas.inbetween.request', version=1, request_id=token, fps=[24, 1],
                   joints=JOINTS, contacts=[], sample_frames=sample_frames,
                   anchors=[pose(float(f), v) for f, v in zip(frames, track[:count], strict=True)])
    return request, track[count:]


def call_worker(folder, weights, payload, success=True):
    folder.mkdir()
    (folder / 'request.json').write_text(payload, encoding='utf-8')
    start = perf_counter()
    run = subprocess.run([sys.executable, '-sEB', str(HERE / 'sprint4_worker.py'), '--weights', str(weights)],
                         cwd=folder, capture_output=True, timeout=10, check=False)
    elapsed = perf_counter()-start
    out = folder / 'response.json'
    if not success:
        assert run.returncode == 2 and not out.exists(), (run.returncode, run.stderr)
        return run.stderr.decode().strip()
    assert run.returncode == 0, run.stderr
    return out.read_bytes(), elapsed


def unpack(raw, request):
    obj = json.loads(raw)
    assert obj.keys() == {'schema', 'version', 'request_id', 'samples'}
    assert obj['schema'] == 'sas.inbetween.response' and obj['version'] == 1
    assert obj['request_id'] == request['request_id']
    rows = obj['samples']
    assert [r['frame'] for r in rows] == request['sample_frames']
    assert not {r['frame'] for r in rows} & {r['frame'] for r in request['anchors']}
    for row in rows:
        assert row.keys() == {'frame', 'translations', 'rotations'}
        assert row['translations'][1:] == [j['rest_translation'] for j in JOINTS[1:]]
    t = np.asarray([r['translations'] for r in rows])
    q = np.asarray([r['rotations'] for r in rows])
    assert t.shape == (len(rows), 3, 3) and q.shape == (len(rows), 3, 4)
    assert np.isfinite(t).all() and np.isfinite(q).all()
    assert np.max(np.abs(np.linalg.norm(q, axis=-1)-1)) < 1e-7
    return np.column_stack([t[:, 0], [[rotation(r) for r in row['rotations']] for row in rows]])


def cpu_request(request, mode):
    return Request(tuple(Pose(a['frame'], tuple(a['translations'][0]), tuple(map(tuple, a['rotations'])))
                         for a in request['anchors']), mode)


def cpu_array(rows, request):
    assert [r.frame for r in rows] == request['sample_frames']
    return np.asarray([r.scalars + tuple(rotation(list(q)) for q in r.rotations) for r in rows])


def baseline(request, mode):
    return cpu_array(CPUBackend().generate(cpu_request(request, mode)), request)


def tip_xyz(rows):
    a, ab = rows[:, 3], rows[:, 3]+rows[:, 4]
    return rows[:, :3] + np.column_stack([0.4*np.cos(a)+0.3*np.cos(ab),
                                          0.4*np.sin(a)+0.3*np.sin(ab), np.zeros(len(rows))])


def errors(rows, target):
    return np.column_stack([np.sum((rows[:, :3]-target[:, :3])**2, axis=1),
                            np.mean((rows[:, 3:]-target[:, 3:])**2, axis=1),
                            np.sum((tip_xyz(rows)-tip_xyz(target))**2, axis=1)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('output', type=Path, help='new directory for proof artifacts')
    args = parser.parse_args()
    root, weights = args.output.resolve(), args.weights.resolve(strict=True)
    root.mkdir()
    rng = np.random.default_rng(BENCH_SEED)
    modes = ('MLP', 'Linear', 'Smooth')
    scores = {mode: [] for mode in modes}
    durations = {mode: [] for mode in modes}
    by_count = {}
    worker_elapsed = []
    for count in range(2, 7):
        block_scores = {mode: [] for mode in modes}
        for i in range(8):
            token = f'k{count}-{i:02d}'
            request, target = make_case(rng, count, token)
            raw, elapsed = call_worker(root / token, weights, json.dumps(request))
            worker_elapsed.append(elapsed)
            rows = unpack(raw, request)
            times, values, mask, query = decode(request)
            # Compare numerical kernels separately from startup/JSON.
            checkpoint = load_weights(weights)
            start = perf_counter()
            predicted = predict(checkpoint, times, values, mask, query)
            durations['MLP'].append(perf_counter()-start)
            np.testing.assert_allclose(rows, predicted, atol=1e-7, rtol=0)
            results = {'MLP': rows}
            for mode in modes[1:]:
                cpu_job = cpu_request(request, mode)
                start = perf_counter()
                cpu_rows = CPUBackend().generate(cpu_job)
                durations[mode].append(perf_counter()-start)
                results[mode] = cpu_array(cpu_rows, request)
            if token == 'k4-00':
                folder = root / token
                for name, track in [('truth', target), *[(m, results[m]) for m in modes[1:]]]:
                    samples = [pose(f, v) for f, v in zip(request['sample_frames'], track, strict=True)]
                    (folder / f'{name}.json').write_text(json.dumps(samples, allow_nan=False), encoding='utf-8')
            for mode, result in results.items():
                metric = errors(result, target)
                scores[mode].append(metric)
                block_scores[mode].append(metric)
        by_count[count] = {mode: np.sqrt(np.concatenate(m).mean(0)).tolist()
                           for mode, m in block_scores.items()}
    rss_kib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    # Dedicated case: four anchors, including one fractional interior and one integer interior.
    frames = [0, 6.5, 15, 24]
    values = np.array([[0.1, 0, 0, 0, 0, 0], [0.2, 0.3, 0.1, 0.15, -0.2, 0.3],
                       [0.64, 0.13, -0.17, 0.39, 0.13, 0.26], [0.6, 0, 0, 0, 0, 0]])
    request = dict(schema='sas.inbetween.request', version=1, request_id='demo-s4', fps=[24, 1],
                   joints=JOINTS, contacts=[], sample_frames=[f for f in range(1, 24) if f != 15],
                   anchors=[pose(f, v) for f, v in zip(frames, values, strict=True)])
    payload = json.dumps(request)
    raw, _ = call_worker(root / 'demo', weights, payload)
    repeat, _ = call_worker(root / 'repeat', weights, payload)
    assert raw == repeat
    rows = unpack(raw, request)
    changed = copy.deepcopy(request)
    changed['request_id'] = 'counterfactual-s4'
    changed['anchors'][1]['translations'][0][1] -= 0.4
    perturbed, _ = call_worker(root / 'counterfactual', weights, json.dumps(changed))
    delta = float(np.max(np.abs(rows[:, 1]-unpack(perturbed, changed)[:, 1])))
    against = {mode: float(np.max(np.linalg.norm(rows[:, :3]-baseline(request, mode)[:, :3], axis=1)))
               for mode in modes[1:]}
    assert delta > 0.02 and min(against.values()) > 0.01

    bad_cases = {
        'extra-key': dict(request, seed=1),
        'bad-version': dict(request, version=True),
        'bad-frames': dict(request, sample_frames=frames),
        'wrong-rig': dict(request, joints=[dict(JOINTS[0], id='other'), *JOINTS[1:]]),
        'duplicate-anchor': dict(request, anchors=[request['anchors'][0], *request['anchors']]),
    }
    for name, position in [('moving-contact', [0.9, 0.2, 0]), ('anchor-conflict', [1.2, 0.5, 0])]:
        contact = dict(joint='tip', kind='position', samples=[
            dict(frame=f, position=p, rotation=None, position_tolerance=0.001, angle_tolerance=None)
            for f, p in [(0, [0.8, 0, 0] if name == 'moving-contact' else position), (5, position)]])
        bad_cases[name] = dict(request, contacts=[contact])
    bad_cases['bad-norm'] = copy.deepcopy(request)
    bad_cases['bad-norm']['anchors'][0]['rotations'][0] = [0, 0, 0, 0]
    invalid = {name: json.dumps(req) for name, req in bad_cases.items()}
    invalid['duplicate-key'] = '{"schema":"x",' + payload[1:]
    invalid['nonfinite'] = payload.replace('"frame": 6.5', '"frame": 1e999')
    invalid['nan'] = payload.replace('"frame": 6.5', '"frame": NaN')
    rejected = {name: call_worker(root / name, weights, text, success=False) for name, text in invalid.items()}
    corrupt = root / 'corrupt.f32'
    corrupt.write_bytes(weights.read_bytes()[:-4])
    rejected['bad-weights'] = call_worker(root / 'bad-weights', corrupt, payload, success=False)
    report = dict(bench_seed=BENCH_SEED, inference_seed=None, clips=len(worker_elapsed),
                  translations_shape=[len(rows), 3, 3], rotations_shape=[len(rows), 3, 4],
                  finite=True, anchor_frames=frames, repeat_byte_identical=True,
                  demo_sha256=hashlib.sha256(raw).hexdigest(), interior_anchor_delta_m=delta,
                  demo_max_root_distance_from_cpu_m=against, worker_peak_rss_kib=rss_kib,
                  worker_wall_median_ms=1000*statistics.median(worker_elapsed),
                  worker_wall_min_max_ms=[1000*min(worker_elapsed), 1000*max(worker_elapsed)],
                  kernel_wall_median_ms={k: 1000*statistics.median(v) for k, v in durations.items()},
                  rms_names=['root_m', 'local_angle_rad', 'tip_fk_m'],
                  rms={k: np.sqrt(np.concatenate(v).mean(0)).tolist() for k, v in scores.items()},
                  rms_by_anchor_count=by_count, rejected=rejected)
    text = json.dumps(report, indent=2, allow_nan=False)
    (root / 'metrics.json').write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
