"""Fire-and-forget email dispatch that never blocks the event loop.

Blocking `email_service.send*` calls (smtplib) must go through here instead
of being awaited/called inline in `async` routes — a single slow SMTP round
trip would otherwise freeze the worker for every concurrent request.

Failures are logged with traceback (never swallowed) so silent mail loss is
visible in logs.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def _log_result(fut: "asyncio.Future", name: str) -> None:
    try:
        fut.result()
    except Exception:
        logger.exception("Background email failed: %s", name)


def send_email_bg(method, *args, **kwargs) -> None:
    """Schedule a blocking email send in a worker thread. Returns immediately."""
    name = getattr(method, "__name__", repr(method))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No running event loop; dropping background email: %s", name)
        return
    fut = loop.run_in_executor(None, lambda: method(*args, **kwargs))
    fut.add_done_callback(lambda f: _log_result(f, name))
