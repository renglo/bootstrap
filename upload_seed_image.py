#!/usr/bin/env python3
"""Build and push the backend seed image to ECR.

Run AFTER deploying <env>-stack-a and BEFORE deploying <env>-stack-b.

Usage:
    cd <infra-installer>
    python bootstrap/upload_seed_image.py \\
        --env-name <env> \\
        --aws-profile <profile> \\
        [--aws-region us-east-1]
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_LAUNCHER_SCRIPT = Path(__file__).resolve().parents[1] / "launcher" / "scripts" / "upload_seed_image.py"

if not _LAUNCHER_SCRIPT.is_file():
    print(f"ERROR: launcher upload script not found: {_LAUNCHER_SCRIPT}", file=sys.stderr)
    sys.exit(1)

runpy.run_path(str(_LAUNCHER_SCRIPT), run_name="__main__")
