"""Single source of truth for code resources and the (overridable) data root.

CODE_ROOT: application code, static build output and shipped resources — the
repository root this module lives in; never overridden at runtime.
DATA_ROOT: the real ontology/ data tree. WIZ_WORKBENCH_ROOT mounts an isolated
temporary data directory for automated tests only; production leaves it unset.

Core storage modules (projects, workspaces, versions, secrets, catalogs) must
resolve their paths here — never through the demo package, so importing them
never loads demo code and a temp data root can never fall back to real data.
"""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get('WIZ_WORKBENCH_ROOT') or CODE_ROOT)
