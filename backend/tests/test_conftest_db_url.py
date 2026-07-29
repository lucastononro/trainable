"""Regression test for conftest.py's DATABASE_URL handling.

The setup_db fixture drops all tables after every test, so the suite must
never run against an ambient DATABASE_URL from a developer's shell (which
could point at a real dev/prod database). conftest.py must force in-memory
SQLite unless the explicit TEST_DATABASE_URL opt-in is set (as CI does for
its Postgres service container).
"""

import os


def test_database_url_is_test_url_or_sqlite():
    """After conftest import, DATABASE_URL is either the explicit
    TEST_DATABASE_URL opt-in or the in-memory SQLite default — never an
    ambient value inherited from the shell."""
    expected = os.environ.get("TEST_DATABASE_URL") or "sqlite+aiosqlite://"
    assert os.environ["DATABASE_URL"] == expected
