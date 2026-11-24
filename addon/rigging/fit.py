# SPDX-License-Identifier: GPL-3.0-or-later
"""Sectional landmarks for symmetric humanoids, in mesh-local coordinates."""

from math import atan2, degrees, isfinite


def estimate(mesh, *, pose="AUTO"):
    """Return Basic Human endpoints (legacy API), with optional pose constraint."""
    return estimate_with_pose(mesh, pose=pose)[0]


def estimate_with_pose(mesh, *, pose="AUTO"):
    """Return Basic Human bone endpoints, or reject an unsupported silhouette.

    This is a sectional fit, not anatomy detection. Proportions seed the
    section positions; cross-sectional extrema supply depth and limb centers.
    """
    if pose not in {"AUTO", "A", "T"}:
        raise ValueError("Pose attendue : AUTO, A ou T.")
    vertices = [tuple(v.co) for v in mesh.vertices]
    if not vertices or not mesh.polygons or not all(
        isfinite(c) for vertex in vertices for c in vertex
    ):
        raise ValueError("Estimation : le maillage doit avoir une surface aux coordonnées finies.")

    low = tuple(map(min, zip(*vertices)))
    high = tuple(map(max, zip(*vertices)))
    width, depth, height = (b - a for a, b in zip(low, high))
    if height <= 1e-6 or not (.53 <= width / height <= 1.35 and .04 <= depth / height <= .48):
        raise ValueError("Estimation : proportions hors limites (humanoïde symétrique, A/T, Z haut, -Y avant).")
    center = (low[0] + high[0]) / 2
    floor = low[2]
    reach = width / 2
    edges = [(vertices[e.vertices[0]], vertices[e.vertices[1]]) for e in mesh.edges]

    def section(axis, position, keep, label):
        points = []
        for a, b in edges:
            if a[axis] == b[axis]:
                continue
            if min(a[axis], b[axis]) <= position < max(a[axis], b[axis]):
                t = (position - a[axis]) / (b[axis] - a[axis])
                p = tuple(x + t * (y - x) for x, y in zip(a, b))
                if keep(p):
                    points.append(p)
        if len(points) < 3:
            raise ValueError(f"Estimation : section introuvable ({label}) ; ajustez le métarig manuellement.")
        return tuple(map(min, zip(*points))), tuple(map(max, zip(*points)))

    def midpoint(lo, hi):
        return tuple((a + b) / 2 for a, b in zip(lo, hi))

    def torso(level, radius=.085):
        z = floor + level * height
        lo, hi = section(2, z, lambda p: abs(p[0] - center) <= radius * height, "tronc/tête")
        return center, (lo[1] + hi[1]) / 2, z

    def paired(axis, positions, filters, label):
        left, right = [
            section(axis, position, keep, label)
            for position, keep in zip(positions, filters)
        ]
        l, r = midpoint(*left), midpoint(*right)
        mirror = (2 * center - r[0], r[1], r[2])
        if (max(abs(a - b) for a, b in zip(l, mirror)) > .045 * height
                or max(abs((a - b) - (c - d)) for a, b, c, d in
                       zip(left[1], left[0], right[1], right[0])) > .055 * height):
            raise ValueError(f"Estimation : sections trop asymétriques ({label}).")
        return midpoint(l, mirror), left, right

    def leg(level, label):
        position = floor + level * height
        return paired(2, (position, position), (
            lambda p: center + .012 * height < p[0] < center + .16 * height,
            lambda p: center - .16 * height < p[0] < center - .012 * height,
        ), label)

    def arm(offset, label, min_level=.49):
        return paired(0, (center + offset, center - offset), (
            lambda p: p[2] >= floor + min_level * height,
        ) * 2, label)

    def with_depth(p, y):
        return p[0], y, p[2]

    def mirror(p):
        return 2 * center - p[0], p[1], p[2]

    hip = leg(.51, "hanche")[0]
    knee = leg(.29, "genou")[0]
    ankle = leg(.052, "cheville")[0]
    if not all(.026 < (p[0] - center) / height < .12 for p in (hip, knee, ankle)):
        raise ValueError("Estimation : jambes séparées attendues de part et d’autre de l’axe X.")
    knee_line = (hip[1] + ankle[1]) / 2
    knee = with_depth(knee, min(knee[1], knee_line - .009 * height))
    _foot, (foot_lo, foot_hi), (other_lo, other_hi) = leg(.023, "pied")
    front, back = (foot_lo[1] + other_lo[1]) / 2, (foot_hi[1] + other_hi[1]) / 2
    if not .015 * height < ankle[1] - front or back - front < .04 * height:
        raise ValueError("Estimation : pieds orientés vers -Y attendus.")
    toe = ankle[0], front + .29 * (back - front), floor + .023 * height
    toe_end = ankle[0], front + .06 * (back - front), toe[2]

    # Shoulder seed tracks the upper torso, not the span (which changes in T).
    upper_z = floor + .70 * height
    torso_lo, torso_hi = section(2, upper_z, lambda p: abs(p[0] - center) < .16 * height, "thorax")
    shoulder_x = min(.39 * reach, max(.23 * reach, .54 * (torso_hi[0] - torso_lo[0])))
    shoulder = arm(shoulder_x, "épaule", .72)[0]
    wrist_sections = []
    for fraction in (.79, .815, .84, .865, .89):
        p, left, right = arm(fraction * reach, "poignet")
        # Y thickness is insensitive to the slope of the arm in the frontal plane.
        thickness = sum(hi[1] - lo[1] for lo, hi in (left, right)) / 2
        wrist_sections.append((thickness + abs(fraction - .84) * .14 * height, p))
    wrist = min(wrist_sections)[1]
    elbow = arm((shoulder_x + wrist[0] - center) / 2, "coude")[0]
    hand = arm(.96 * reach, "main")[0]
    angle = degrees(atan2(shoulder[2] - wrist[2], wrist[0] - shoulder[0]))
    detected = "T" if angle <= 12 else "A"
    if not (floor + .72 * height <= shoulder[2] <= floor + .90 * height
            and -10 <= angle <= 55
            and elbow[2] <= shoulder[2] + .028 * height
            and hand[2] <= wrist[2] + .028 * height):
        raise ValueError("Estimation : bras horizontaux ou descendants attendus (T/A-pose).")
    if pose != "AUTO" and pose != detected:
        raise ValueError(f"Estimation : pose {detected} détectée, pose {pose} demandée ({angle:.0f}°).")
    elbow_line = (shoulder[1] + wrist[1]) / 2
    if abs(elbow[1] - elbow_line) < .008 * height:
        elbow = with_depth(elbow, elbow_line + .008 * height)

    spine = [torso(level, radius) for level, radius in (
        (.50, .10), (.57, .11), (.64, .11), (.73, .11),
        (.82, .059), (.865, .053), (.90, .055), (.985, .045),
    )]
    spine_names = ["spine", *(f"spine.{index:03}" for index in range(1, 7))]
    result = dict(zip(spine_names, zip(spine, spine[1:])))

    heel_half_width = .38 * (foot_hi[0] - foot_lo[0])
    heel_y = back - .06 * (back - front)
    chest_z = floor + .756 * height
    chest_lo, chest_hi = section(
        2, chest_z, lambda p: 0 < p[0] - center < .088 * height, "poitrine"
    )
    breast_x = center + .052 * height
    collar = (center + .009 * height, (chest_lo[1] + chest_hi[1]) / 2, shoulder[2])
    limbs = {
        "pelvis": (spine[0], (hip[0], hip[1] - .025 * height, spine[1][2])),
        "thigh": (hip, knee),
        "shin": (knee, ankle),
        "foot": (ankle, toe),
        "toe": (toe, toe_end),
        "heel.02": ((ankle[0] - heel_half_width, heel_y, floor),
                    (ankle[0] + heel_half_width, heel_y, floor)),
        "shoulder": (collar, shoulder),
        "breast": ((breast_x, (chest_hi[1] + chest_lo[1]) / 2, chest_z),
                   (breast_x, chest_lo[1], chest_z)),
        "upper_arm": (shoulder, elbow),
        "forearm": (elbow, wrist),
        "hand": (wrist, hand),
    }
    for name, (head, tail) in limbs.items():
        result[f"{name}.L"] = (head, tail)
        result[f"{name}.R"] = (mirror(head), mirror(tail))

    if any(sum((a - b) ** 2 for a, b in zip(head, tail)) < (.002 * height) ** 2
           for head, tail in result.values()):
        raise ValueError("Estimation : un repère produit un os trop court ; ajustez le métarig manuellement.")
    return result, detected
