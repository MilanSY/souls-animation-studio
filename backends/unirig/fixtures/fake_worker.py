# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic process fixture; not a backend, no model or inference."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main(mode):
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert "SAS_TEST_PROJECT_PATH" not in os.environ
    Path("progress").write_text("4", encoding="ascii")
    if mode == "fail":
        sys.exit("synthetic CUDA failure")
    if mode == "missing":
        return
    if mode == "wait":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path("child.pid").write_text(str(child.pid), encoding="ascii")
        time.sleep(60)
        return
    req = json.loads(Path("request.json").read_text(encoding="utf-8"))
    resp = json.loads(Path(__file__).with_name("response.json").read_text(encoding="utf-8"))
    assert req["mesh_id"] == resp["mesh_id"]
    Path("response.json").write_text(json.dumps(resp), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
