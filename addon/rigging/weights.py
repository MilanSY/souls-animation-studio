# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded diffusion over mesh edges, followed by deterministic top-four pruning."""


def _normalize(row):
    total = sum(row.values())
    return {name: weight / total for name, weight in row.items()}


def limit_weights(row):
    return _normalize(dict(sorted(row.items(), key=lambda p: (-p[1], p[0]))[:4]))


def smooth_weights(rows, edges):
    """Two light Jacobi passes; never diffuse across disconnected surfaces."""
    neighbors = [[] for _ in rows]
    for edge in edges:
        a, b = edge.vertices
        neighbors[a].append(b)
        neighbors[b].append(a)
    result = [_normalize(row) for row in rows]
    for _ in range(2):
        previous = result
        result = []
        for row, adjacent in zip(previous, neighbors):
            if not adjacent:
                result.append(row)
                continue
            mixed = {name: .8 * weight for name, weight in row.items()}
            factor = .2 / len(adjacent)
            for i in adjacent:
                for name, weight in previous[i].items():
                    mixed[name] = mixed.get(name, 0.0) + factor * weight
            result.append(mixed)
    return [limit_weights(row) for row in result]
