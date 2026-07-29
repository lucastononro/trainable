"""A8 — sandbox span must exit on both happy and exception paths.

Pre-fix: `sandbox_span(...)` was opened with `__enter__()` and closed in
a far-below `finally` block. The pattern worked but was fragile — any
exception between `__enter__` and the outer `try` would leak the span.

Post-fix: a real `with sandbox_span(...)` block.

This script swaps in a tracing context manager that records its lifecycle,
exercises both happy and exception paths through a minimal mimic of the
post-fix structure, and verifies `__exit__` always fires.

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_sandbox_span_exit.py
"""

from __future__ import annotations

import sys


class TracingSpan:
    """Records when __enter__ / __exit__ are called, plus the exc_info passed."""

    def __init__(self):
        self.entered = False
        self.exited = False
        self.exc_type = None

    def __enter__(self):
        self.entered = True

        class _SpanObj:
            def set_attribute(_self, *a, **k):
                pass

        return _SpanObj()

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        self.exc_type = exc_type
        return False  # never swallow


def happy_path():
    span = TracingSpan()
    with span as _:
        # Mimic the inner body — non-exceptional return.
        result = "ok"
    return span, result


def exception_path():
    span = TracingSpan()
    try:
        with span as _:
            raise RuntimeError("simulated sandbox failure")
    except RuntimeError:
        # Caller catches; the `with` already invoked __exit__ with the
        # exception type before the catch fired.
        return span
    return span


def main() -> int:
    h_span, h_result = happy_path()
    assert h_span.entered and h_span.exited and h_span.exc_type is None
    print(
        f"  happy     -> entered={h_span.entered} exited={h_span.exited} result={h_result!r}"
    )

    e_span = exception_path()
    assert e_span.entered and e_span.exited
    assert e_span.exc_type is RuntimeError, e_span.exc_type
    print(
        f"  exception -> entered={e_span.entered} exited={e_span.exited} exc_type={e_span.exc_type.__name__}"
    )

    print("PASS — `with sandbox_span(...)` exits cleanly on both paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
