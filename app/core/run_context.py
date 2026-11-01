"""
Run context — the orchestrator run the current code is executing in.

The orchestrator sets ``current_run_id`` for the duration of a run, so
records written by agents (audit logs, recommendations) are tagged with
the run without threading a run id through every call.  Outside a run it
is None.
"""

from contextvars import ContextVar
from typing import Optional

current_run_id: ContextVar[Optional[int]] = ContextVar("current_run_id", default=None)
