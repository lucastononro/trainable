"""Tests for services/agent.py — helper functions and abort logic."""

import asyncio

import pytest

from tools.execute_code import _code_counter, _extract_slug, _script_filename

# ---------------------------------------------------------------------------
# _extract_slug
# ---------------------------------------------------------------------------


class TestExtractSlug:
    def test_top_comment(self):
        code = "# Load and clean the data\nimport pandas as pd"
        slug = _extract_slug(code)
        assert slug == "load_and_clean_the_data"

    def test_function_def(self):
        code = "def train_model():\n    pass"
        assert _extract_slug(code) == "train_model"

    def test_class_def(self):
        code = "class MyClassifier:\n    pass"
        assert _extract_slug(code) == "MyClassifier"

    def test_import_fallback(self):
        code = "import pandas as pd\ndf = pd.read_csv('data.csv')"
        assert _extract_slug(code) == "pd"

    def test_from_import_fallback(self):
        code = "from sklearn.model_selection import train_test_split"
        assert _extract_slug(code) == "train_test_split"

    def test_empty_code(self):
        assert _extract_slug("") == "code"

    def test_shebang_ignored(self):
        code = "#!/usr/bin/env python\nimport numpy"
        # shebang starts with #! so it's skipped
        assert _extract_slug(code) == "numpy"

    def test_short_comment_skipped(self):
        code = "# Hi\ndef process():\n    pass"
        # "Hi" has len <= 3, so it's skipped
        assert _extract_slug(code) == "process"

    def test_slug_truncated_to_40(self):
        code = "# This is a really long comment that should be truncated to forty characters max"
        slug = _extract_slug(code)
        assert len(slug) <= 40

    def test_special_chars_cleaned(self):
        code = "# Load data (v2) — clean & transform!"
        slug = _extract_slug(code)
        assert all(c.isalnum() or c == "_" for c in slug)


# ---------------------------------------------------------------------------
# _script_filename
# ---------------------------------------------------------------------------


class TestScriptFilename:
    def setup_method(self):
        _code_counter.clear()

    def test_sequential_numbering(self):
        name1 = _script_filename("import pandas", "sess-1")
        name2 = _script_filename("import numpy", "sess-1")
        assert name1.startswith("step_01_")
        assert name2.startswith("step_02_")

    def test_different_sessions_independent(self):
        _script_filename("import pandas", "sess-a")
        _script_filename("import pandas", "sess-b")
        name_a = _script_filename("import numpy", "sess-a")
        name_b = _script_filename("import numpy", "sess-b")
        assert name_a.startswith("step_02_")
        assert name_b.startswith("step_02_")

    def test_includes_slug(self):
        name = _script_filename("# Data cleaning\nimport pandas", "sess-1")
        assert "data_cleaning" in name

    def test_ends_with_py(self):
        name = _script_filename("print('hello')", "sess-1")
        assert name.endswith(".py")


# ---------------------------------------------------------------------------
# abort_agent
# ---------------------------------------------------------------------------


class TestAbortAgent:
    @pytest.mark.asyncio
    async def test_abort_no_running_task(self):
        from services.agent import abort_agent
        from services.agent.tasks import _running_tasks

        _running_tasks.pop("test-session", None)
        result = await abort_agent("test-session")
        assert result is False

    @pytest.mark.asyncio
    async def test_abort_done_task(self):
        from services.agent import abort_agent
        from services.agent.tasks import _running_tasks

        task = asyncio.ensure_future(asyncio.sleep(0))
        await task  # let it complete
        _running_tasks["test-session"] = task

        result = await abort_agent("test-session")
        assert result is False
        _running_tasks.pop("test-session", None)

    @pytest.mark.asyncio
    async def test_abort_running_task(self):
        from services.agent import abort_agent
        from services.agent.tasks import _running_tasks

        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        _running_tasks["test-session"] = task

        result = await abort_agent("test-session")
        assert result is True
        assert task.cancelled() or task.done()
        _running_tasks.pop("test-session", None)

    @pytest.mark.asyncio
    async def test_abort_silent_flag(self):
        from services.agent import abort_agent
        from services.agent.tasks import _running_tasks, _silent_aborts

        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        _running_tasks["test-session"] = task

        result = await abort_agent("test-session", silent=True)
        assert result is True
        assert "test-session" in _silent_aborts
        _silent_aborts.discard("test-session")
        _running_tasks.pop("test-session", None)
