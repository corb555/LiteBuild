from __future__ import annotations

import sys
from threading import Lock
from typing import TextIO

from LiteBuild.status_message import StatusMessage, to_json


class StatusEmitter:
    """Emit LiteBuild structured status as newline-delimited JSON.

    This transport is intentionally separate from BuildLogger. CLI callers use
    stderr; tests or in-process integrations may supply another text stream.
    """

    def __init__(self, output: TextIO = sys.stderr) -> None:
        self.output = output
        self._lock = Lock()

    def emit(self, message: StatusMessage) -> None:
        """Write one complete NDJSON status record and flush immediately."""
        record = f"{to_json(message)}\n"
        with self._lock:
            self.output.write(record)
            self.output.flush()



def emit_status(
    status_emitter: StatusEmitter | None,
    message: StatusMessage,
) -> None:
    """Emit a status message when structured status output is enabled."""
    if status_emitter is not None:
        status_emitter.emit(message)
