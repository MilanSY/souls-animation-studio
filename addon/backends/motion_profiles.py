# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in launch profiles, not a discovery mechanism or a list of UI engines."""

from dataclasses import dataclass
from types import MappingProxyType


CHECKPOINT_BYTES = 61_080  # Sprint 4 raw little-endian float32 checkpoint
DEFAULT_PROFILE = 'sprint4-mlp'
FIXTURE_PROFILE = 'test-json'
LEGACY_JSON_LIMIT = 64 * 1024


@dataclass(frozen=True)
class MotionProfile:
    worker_name: str
    devices: tuple[str, ...]
    weights_bytes: int | None
    exchange_format: str
    exchange_version: int
    contact_kinds: tuple[str, ...]
    max_timeout: int
    request_bytes: int
    response_bytes: int
    max_joints: int
    max_anchors: int
    max_samples: int
    max_contacts: int
    max_contact_samples: int  # per contact
    strict_samples: bool


PROFILES = MappingProxyType({
    DEFAULT_PROFILE: MotionProfile(
        'sprint4_worker.py', ('cpu',), CHECKPOINT_BYTES, 'sas.inbetween', 1, (),
        max_timeout=3600, request_bytes=LEGACY_JSON_LIMIT, response_bytes=LEGACY_JSON_LIMIT,
        max_joints=3, max_anchors=6, max_samples=24, max_contacts=0, max_contact_samples=0,
        strict_samples=False,  # The existing MLP bake checks exact poses before applying.
    ),
    # Synthetic transport fixture only (including contact payloads; no solver).
    # 77 joints * 300 poses * 7 values * ~26 B (number + JSON punctuation)
    # ~= 4.2 MB for a dense pose array. Allow framing in 5 MiB for the reply.
    # For input, 16 * 300 contacts at ~400 B add ~1.9 MB; 8 MiB leaves room
    # for rest joints, frame arrays, and envelopes. Byte caps still apply even
    # when legal dimensions contain long numeric/string representations.
    FIXTURE_PROFILE: MotionProfile(
        'motion_fixture_worker.py', ('cpu',), None, 'sas.inbetween', 1, ('position', 'pose'),
        max_timeout=120, request_bytes=8 * 1024**2, response_bytes=5 * 1024**2,
        max_joints=77, max_anchors=300, max_samples=300, max_contacts=16, max_contact_samples=300,
        strict_samples=True,
    ),
})
