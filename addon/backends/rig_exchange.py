# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict, Blender-independent SAS rig interchange v1."""

from collections import Counter, defaultdict
from hashlib import sha256
from itertools import product
import json
import math


MAX_VERTICES = 50_000
MAX_TRIANGLES = 50_000
MAX_BONES = 512
MAX_JSON_BYTES = 24 * 1024 * 1024
POSITION_TOLERANCE = 1e-5
WEIGHT_TOLERANCE = 1e-5
SPACE = "normalized-blender-z-up"


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(token):
    raise ValueError(f"Non-finite JSON literal: {token}")


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or invalid exchange file: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("Rig exchange JSON exceeds the size limit.")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_fields,
                          parse_constant=_reject_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid rig exchange JSON.") from exc


def write_json(path, data):
    path.write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def _fields(data, *keys):
    if type(data) is not dict or data.keys() != set(keys):
        raise ValueError(f"Expected exactly these fields: {', '.join(keys)}")


def _index(value, count):
    return type(value) is int and 0 <= value < count


def _number(value, bound):
    return type(value) in (int, float) and -bound <= value <= bound


def _vector(value, bound):
    if type(value) is not list or len(value) != 3 or not all(_number(x, bound) for x in value):
        raise ValueError("Expected three finite coordinates within the permitted bounds.")


def _geometry(vertices, triangles):
    if type(vertices) is not list or not 3 <= len(vertices) <= MAX_VERTICES:
        raise ValueError(f"Expected 3–{MAX_VERTICES} vertices.")
    for vertex in vertices:
        _vector(vertex, 1.0001)
    if type(triangles) is not list or not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise ValueError(f"Expected 1–{MAX_TRIANGLES} triangles.")
    used = set()
    for face in triangles:
        if (type(face) is not list or len(face) != 3
                or not all(_index(i, len(vertices)) for i in face) or len(set(face)) != 3):
            raise ValueError("Invalid triangle in rig exchange.")
        a, b, c = (vertices[i] for i in face)
        u, v = ([p[k] - a[k] for k in range(3)] for p in (b, c))
        if not any(u[j] * v[k] - u[k] * v[j] for j, k in ((0, 1), (1, 2), (2, 0))):
            raise ValueError("Degenerate triangle in rig exchange.")
        used.update(face)
    if len(used) != len(vertices):
        raise ValueError("Rig exchange does not support loose vertices.")


def _mesh_id(vertices, triangles):
    payload = json.dumps([vertices, triangles], allow_nan=False, separators=(",", ":"))
    return sha256(payload.encode("ascii")).hexdigest()


def _header(data, schema):
    if data["schema"] != schema or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported rig exchange schema/version.")
    if data["space"] != SPACE:
        raise ValueError("Unsupported rig exchange coordinate space.")


def make_request(vertices, triangles):
    _geometry(vertices, triangles)
    return dict(schema="sas.rig.request", version=1, space=SPACE,
                mesh_id=_mesh_id(vertices, triangles), vertices=vertices, triangles=triangles)


def validate_request(data):
    _fields(data, "schema", "version", "space", "mesh_id", "vertices", "triangles")
    _header(data, "sas.rig.request")
    _geometry(data["vertices"], data["triangles"])
    if data["mesh_id"] != _mesh_id(data["vertices"], data["triangles"]):
        raise ValueError("Request mesh fingerprint does not match its geometry.")


_NEIGHBORS = tuple(product((-1, 0, 1), repeat=3))


def _cell(vertex):
    return tuple(math.floor(x / POSITION_TOLERANCE) for x in vertex)


def vertex_mapping(source, returned):
    """Return result row -> source index; fail closed on ambiguous coordinates."""
    if len(source) != len(returned):
        raise ValueError("Backend changed the vertex count.")
    grid = defaultdict(list)
    for i, vertex in enumerate(source):
        grid[_cell(vertex)].append(i)
    used = set()
    mapping = []
    for vertex in returned:
        x, y, z = _cell(vertex)
        candidates = []
        for dx, dy, dz in _NEIGHBORS:
            for i in grid.get((x + dx, y + dy, z + dz), ()):
                if math.dist(vertex, source[i]) <= POSITION_TOLERANCE:
                    candidates.append(i)
                    if len(candidates) == 2:
                        raise ValueError("Ambiguous vertex correspondence (coincident or close vertices).")
        if not candidates or candidates[0] in used:
            raise ValueError("Backend vertices do not map bijectively onto the source mesh.")
        mapping.append(candidates[0])
        used.add(candidates[0])
    return mapping


def _bones(bones):
    if type(bones) is not list or not 1 <= len(bones) <= MAX_BONES:
        raise ValueError(f"Expected 1–{MAX_BONES} bones.")
    names = set()
    for i, bone in enumerate(bones):
        _fields(bone, "name", "parent", "head", "tail", "z_axis")
        name = bone["name"]
        try:
            valid_name = (type(name) is str and 0 < len(name.encode("utf-8")) <= 63
                          and name == name.strip() and name.isprintable() and name not in names)
        except UnicodeError:
            valid_name = False
        if not valid_name:
            raise ValueError("Bone names must be unique, printable and at most 63 UTF-8 bytes.")
        names.add(name)
        parent = bone["parent"]
        if not (parent is None if i == 0 else _index(parent, i)):
            raise ValueError("Expected one root at index 0 and parents before children.")
        head, tail, axis = (bone[key] for key in ("head", "tail", "z_axis"))
        _vector(head, 16)
        _vector(tail, 16)
        _vector(axis, 1.0001)
        length = math.dist(tail, head)
        dot = sum((t - h) * z for t, h, z in zip(tail, head, axis))
        if (length < 1e-5 or abs(math.hypot(*axis) - 1) > 1e-4
                or abs(dot) > 1e-4 * length):
            raise ValueError(f"Invalid rest length or roll axis for bone {name}.")


def validate_response(request, response):
    """Return bones and validated weight rows in *source* vertex order."""
    validate_request(request)
    _fields(response, "schema", "version", "space", "mesh_id", "vertices", "triangles",
            "bones", "weights")
    _header(response, "sas.rig.response")
    if response["mesh_id"] != request["mesh_id"]:
        raise ValueError("Rig response belongs to a different source mesh.")
    _geometry(response["vertices"], response["triangles"])
    mapping = vertex_mapping(request["vertices"], response["vertices"])
    # Compare triangle incidence, ignoring winding and face order, not just positions.
    if Counter(tuple(sorted(mapping[i] for i in face)) for face in response["triangles"]) != Counter(
        tuple(sorted(face)) for face in request["triangles"]
    ):
        raise ValueError("Backend changed the triangulated topology.")
    bones, weights = response["bones"], response["weights"]
    _bones(bones)
    if type(weights) is not list or len(weights) != len(mapping):
        raise ValueError("Expected one weight row per returned vertex.")
    ordered = [None] * len(mapping)
    for source_index, row in zip(mapping, weights):
        if type(row) is not list or not 1 <= len(row) <= min(4, len(bones)):
            raise ValueError("Expected 1–4 influences per vertex.")
        seen = set()
        for entry in row:
            if (type(entry) is not list or len(entry) != 2 or not _index(entry[0], len(bones))
                    or entry[0] in seen or not _number(entry[1], 1) or entry[1] <= 0):
                raise ValueError("Invalid, unknown or duplicate bone influence.")
            seen.add(entry[0])
        if abs(math.fsum(value for _, value in row) - 1) > WEIGHT_TOLERANCE:
            raise ValueError("Skin weights must sum to 1 at every vertex.")
        ordered[source_index] = row
    return bones, ordered
