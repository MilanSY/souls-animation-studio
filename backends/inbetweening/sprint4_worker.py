# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Sprint 4 worker: one deliberately narrow sas.inbetween v1 profile."""

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np


SPAN = 24
SLOTS = 6
CHANNELS = 6  # XYZ root (metres), three local Z hinge angles (radians)
WIDTH = SLOTS * (CHANNELS + 3) + 1
HIDDEN = 96
SHAPES = ((WIDTH, HIDDEN), (HIDDEN,), (HIDDEN, HIDDEN), (HIDDEN,),
          (HIDDEN, CHANNELS), (CHANNELS,))
JOINTS = [
    dict(id=name, parent=parent, rest_translation=offset, rest_rotation=[1, 0, 0, 0])
    for name, parent, offset in [('root', None, [0, 0, 0]),
                                 ('hinge', 0, [0.4, 0, 0]),
                                 ('tip', 1, [0.3, 0, 0])]
]


def features(times, values, mask, query):
    """Batch rows; a zero-padded slot has neither a pose nor a time signal."""
    dt = (times - query[:, None]) * mask
    slots = np.concatenate((values * mask[..., None], (times * mask)[..., None],
                            dt[..., None], mask[..., None]), axis=-1)
    return np.concatenate((slots.reshape(len(query), WIDTH - 1), query[:, None]),
                          axis=-1).astype(np.float32)


def forward(x, weights):
    w1, b1, w2, b2, w3, b3 = weights
    h1 = np.tanh(x @ w1 + b1)
    h2 = np.tanh(h1 @ w2 + b2)
    return h2 @ w3 + b3, h1, h2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact(obj, keys):
    require(type(obj) is dict and obj.keys() == set(keys.split()), f'expected fields: {keys}')


def number(value, bound):
    require(type(value) in (int, float) and -bound <= value <= bound, 'invalid number/range')
    return float(value)


def vector(value, size, bound):
    require(type(value) is list and len(value) == size, f'expected vector of size {size}')
    return [number(x, bound) for x in value]


def rotation(value):
    w, x, y, z = vector(value, 4, 1.000001)
    require(abs(w*w + x*x + y*y + z*z - 1) <= 1e-6, 'non-unit quaternion')
    require(abs(x) <= 1e-7 and abs(y) <= 1e-7, 'only Z hinges supported')
    angle = 2 * math.atan2(z, w)
    return math.remainder(angle, math.tau)


def decode(request):
    exact(request, 'schema version request_id fps joints anchors sample_frames contacts')
    require(request['schema'] == 'sas.inbetween.request' and
            type(request['version']) is int and request['version'] == 1, 'invalid schema/version')
    token = request['request_id']
    require(type(token) is str and 0 < len(token) <= 128 and
            not any(0xD800 <= ord(ch) <= 0xDFFF for ch in token), 'invalid request_id')
    fps = request['fps']
    require(type(fps) is list and len(fps) == 2 and
            all(type(x) is int and 0 < x <= 10**6 for x in fps) and
            fps[0] == 24 * fps[1], 'only 24 fps supported')
    require(type(request['contacts']) is list and not request['contacts'],
            'contacts unsupported: no pose returned')
    joints = request['joints']
    require(type(joints) is list and len(joints) == len(JOINTS), 'canonical chain required')
    for joint, canon in zip(joints, JOINTS, strict=True):
        exact(joint, 'id parent rest_translation rest_rotation')
        require(joint['id'] == canon['id'] and type(joint['parent']) is type(canon['parent']) and
                joint['parent'] == canon['parent'], 'canonical joint ID/order/parent required')
        require(np.allclose(vector(joint['rest_translation'], 3, 1), canon['rest_translation'],
                            atol=1e-7, rtol=0) and abs(rotation(joint['rest_rotation'])) <= 1e-7,
                'canonical rest transforms required')
    anchors = request['anchors']
    require(type(anchors) is list and 2 <= len(anchors) <= SLOTS, 'expected 2–6 anchors')
    frames, poses = [], []
    for anchor in anchors:
        exact(anchor, 'frame translations rotations')
        frames.append(number(anchor['frame'], 10**6))
        translations, rotations = anchor['translations'], anchor['rotations']
        require(all(type(x) is list and len(x) == len(JOINTS) for x in (translations, rotations)),
                'wrong pose shape')
        for j in (1, 2):
            require(np.allclose(vector(translations[j], 3, 1), JOINTS[j]['rest_translation'],
                                atol=1e-7, rtol=0), 'animated non-root translations unsupported')
        angles = [rotation(q) for q in rotations]
        require(max(map(abs, angles)) <= 1.5, 'anchor angle outside supported range')
        poses.append(vector(translations[0], 3, 2) + angles)
    start, end = frames[0], frames[-1]
    require(all(a < b for a, b in zip(frames, frames[1:])) and end - start == SPAN,
            'anchors must be strictly ordered and span 24 frames')
    expected = [f for f in range(math.floor(start)+1, math.ceil(end)) if f not in frames]
    samples = request['sample_frames']
    require(type(samples) is list and len(samples) == len(expected) and
            all(type(f) is int for f in samples) and samples == expected, 'invalid sample_frames')
    times = np.zeros(SLOTS, dtype=np.float32)
    values = np.zeros((SLOTS, CHANNELS), dtype=np.float32)
    mask = np.zeros(SLOTS, dtype=np.float32)
    k = len(frames)
    times[:k] = (np.asarray(frames) - start) / SPAN
    require(np.all(np.diff(times[:k]) > 0), 'anchor times collide at model precision')
    values[:k] = poses
    mask[:k] = 1
    return times, values, mask, np.asarray([(f - start) / SPAN for f in samples], dtype=np.float32)


def pose(frame, values):
    return dict(frame=frame, translations=[list(map(float, values[:3]))] +
                [j['rest_translation'] for j in JOINTS[1:]],
                rotations=[[math.cos(a/2), 0, 0, math.sin(a/2)] for a in values[3:]])


def predict(weights, times, values, mask, query):
    n = len(query)
    x = features(np.broadcast_to(times, (n, SLOTS)),
                 np.broadcast_to(values, (n, SLOTS, CHANNELS)),
                 np.broadcast_to(mask, (n, SLOTS)), query)
    result, _, _ = forward(x, weights)
    require(np.isfinite(result).all(), 'non-finite network output')
    require(np.max(np.abs(result[:, :3])) <= 2 and np.max(np.abs(result[:, 3:])) <= 1.5,
            'prediction outside supported range')
    return result


def read_json(path):
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), 'duplicate JSON field')
        return result

    def invalid_constant(value):
        raise ValueError(f'non-finite JSON constant: {value}')

    with path.open('rb') as stream:
        data = stream.read(65537)
    require(len(data) <= 65536, 'request too large')
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid_constant)


def load_weights(path):
    # Only load caller-provisioned, trusted local artifacts; no pickle or downloads.
    count = sum(math.prod(s) for s in SHAPES)
    with path.open('rb') as stream:
        data = stream.read(count * 4 + 1)
    require(len(data) == count * 4, 'wrong checkpoint size')
    flat = np.frombuffer(data, dtype='<f4')
    require(np.all(np.isfinite(flat)) and np.max(np.abs(flat)) <= 16, 'invalid checkpoint values')
    weights, offset = [], 0
    for shape in SHAPES:
        size = math.prod(shape)
        weights.append(flat[offset:offset + size].reshape(shape))
        offset += size
    return tuple(weights)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', required=True, type=Path)
    args = parser.parse_args()
    out = Path('response.json')
    try:
        require(not out.exists(), 'job directory must not contain a response')
        request = read_json(Path('request.json'))
        decoded = decode(request)
        result = predict(load_weights(args.weights), *decoded)
        response = dict(schema='sas.inbetween.response', version=1, request_id=request['request_id'],
                        samples=[pose(f, row) for f, row in zip(request['sample_frames'], result, strict=True)])
        with Path('response.json.tmp').open('x', encoding='utf-8') as stream:
            json.dump(response, stream, allow_nan=False, ensure_ascii=False)
        Path('response.json.tmp').replace(out)
    except (OSError, ValueError, RecursionError) as exc:
        print(f'sprint4: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
