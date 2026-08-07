"""Console I/O fix-ups for process entry points.

Every process entry point that prints to stdout/stderr should call
:func:`configure_stdio_encoding` first, before anything else runs — mirroring
``factoryai.shared.asyncio_compat``'s "call this before the first thing that needs it"
rule, just for console encoding instead of the event loop.
"""

from __future__ import annotations

import sys


def configure_stdio_encoding() -> None:
    """Force UTF-8 on stdout and stderr, where the stream supports reconfiguring it.

    Windows' default console codepage (cp1252) cannot encode the emoji MLflow's own
    client writes when it logs a completed run's URL
    (``mlflow.tracking._tracking_service.client.TrackingServiceClient._log_url``). Without
    this, that single `sys.stdout.write` raises ``UnicodeEncodeError`` and crashes an
    otherwise fully successful training run at its very last step — after the model has
    already been fit, evaluated and is sitting in memory, discarded because nothing wrote
    it down. A no-op on a stream that has already been reconfigured, or one that never
    supports it (e.g. output redirected to certain pipes).
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
