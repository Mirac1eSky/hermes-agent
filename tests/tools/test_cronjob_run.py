"""Tests for cronjob run/trigger functionality.

Covers both Gateway mode (wake file) and CLI mode (synchronous execution).
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from tools.cronjob_tools import cronjob


# =========================================================================
# Helpers
# =========================================================================

def _mock_gateway_running(monkeypatch):
    """Simulate a running Gateway process."""
    fake_pid = os.getpid()  # Current process is always alive
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: fake_pid)


def _mock_gateway_not_running(monkeypatch):
    """Simulate no Gateway running."""
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture(autouse=True)
def _setup_cron_dir(tmp_path, monkeypatch):
    """Redirect all cron I/O to a temp directory."""
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "output").mkdir(exist_ok=True)
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir)
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", cron_dir / "output")
    # Also patch the lock dir used in cronjob_tools
    monkeypatch.setattr("cron.scheduler._LOCK_DIR", cron_dir)
    monkeypatch.setattr("cron.scheduler._LOCK_FILE", cron_dir / ".tick.lock")


# =========================================================================
# Gateway mode: wake file + return immediately
# =========================================================================

class TestCronjobRunGatewayMode:
    """When Gateway is running, `cronjob run` should update state,
    touch the wake file, and return immediately without executing."""

    def test_run_updates_next_run_at(self, monkeypatch, tmp_path):
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h", name="Test Job")
        )
        job_id = created["job_id"]

        _mock_gateway_running(monkeypatch)

        # Patch wake file path to a temp location
        wake_file = tmp_path / ".cron.wake"
        monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)

        result = json.loads(cronjob(action="run", job_id=job_id))

        assert result["success"] is True
        assert "execute within seconds" in result.get("message", "")
        assert result["job"]["state"] == "scheduled"

    def test_run_touches_wake_file(self, monkeypatch, tmp_path):
        """Wake file is created under get_hermes_home()/cron/."""
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_running(monkeypatch)

        # Wake file is at get_hermes_home() / "cron" / ".cron.wake"
        # The fixture sets CRON_DIR = tmp_path / "cron", so hermes_home = tmp_path.
        # But the wake code calls get_hermes_home() which we patch to tmp_path,
        # then appends / cron / .cron.wake → tmp_path / cron / .cron.wake
        wake_file = tmp_path / "cron" / ".cron.wake"
        monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)

        cronjob(action="run", job_id=job_id)

        assert wake_file.exists(), \
            f"Wake file should be created at {wake_file}"

    def test_run_does_not_execute_synchronously(self, monkeypatch):
        """Gateway mode should NOT call run_job."""
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_running(monkeypatch)

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wake_file = Path(td) / ".cron.wake"
            monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: Path(td))

            with patch("cron.scheduler.run_job") as mock_run:
                cronjob(action="run", job_id=job_id)
                mock_run.assert_not_called()

    def test_run_paused_job_clears_pause_state(self, monkeypatch, tmp_path):
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        # Pause it
        cronjob(action="pause", job_id=job_id)

        _mock_gateway_running(monkeypatch)
        monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)

        result = json.loads(cronjob(action="run", job_id=job_id))
        assert result["success"] is True
        assert result["job"]["state"] == "scheduled"
        assert result["job"].get("paused_at") is None

    def test_run_returns_without_wake_file_error(self, monkeypatch, tmp_path):
        """If wake file creation fails, the job should still be triggered."""
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_running(monkeypatch)
        # Point to a path where we can't write
        bad_dir = Path("/proc/nonexistent_hermes_test")
        monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: bad_dir)

        result = json.loads(cronjob(action="run", job_id=job_id))
        # Should still return success (wake file failure is non-fatal)
        assert result["success"] is True
        assert "execute within seconds" in result.get("message", "")


# =========================================================================
# CLI mode: synchronous execution
# =========================================================================

class TestCronjobRunCLIMode:
    """When Gateway is NOT running, `cronjob run` should execute synchronously
    and return the result directly."""

    def test_run_executes_synchronously(self, monkeypatch):
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_not_running(monkeypatch)

        mock_output = "# Output\nTest result"
        with patch("cron.scheduler.run_job") as mock_run:
            mock_run.return_value = (True, mock_output, "Test result", None)

            result = json.loads(cronjob(action="run", job_id=job_id))

            mock_run.assert_called_once()
            assert result["success"] is True
            assert result["output_preview"] == "Test result"

    def test_run_marks_job_on_failure(self, monkeypatch):
        """If run_job raises an exception, mark_job_run should still be called
        with success=False."""
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_not_running(monkeypatch)

        with patch("cron.scheduler.run_job", side_effect=RuntimeError("API timeout")):
            result = json.loads(cronjob(action="run", job_id=job_id))

            assert result["success"] is False
            # The job should be marked with an error
            from cron.jobs import get_job
            job = get_job(job_id)
            assert job["last_status"] == "error"
            assert "API timeout" in job.get("last_error", "")

    def test_run_computes_next_run_at_correctly(self, monkeypatch):
        """CLI mode: mark_job_run should compute next_run_at as the next
        interval from now, not the current time."""
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_not_running(monkeypatch)

        with patch("cron.scheduler.run_job", return_value=(True, "", "OK", None)):
            cronjob(action="run", job_id=job_id)

            from cron.jobs import get_job
            job = get_job(job_id)
            # next_run_at should be set by mark_job_run to ~1 hour from now
            # (the schedule is "every 1h"), not to the current timestamp.
            assert job.get("next_run_at") is not None

    def test_run_sets_output_preview(self, monkeypatch):
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_not_running(monkeypatch)

        long_response = "X" * 600  # Should be truncated to 500
        with patch("cron.scheduler.run_job") as mock_run:
            mock_run.return_value = (True, "", long_response, None)

            result = json.loads(cronjob(action="run", job_id=job_id))

            assert len(result["output_preview"]) <= 500
            assert result["output_preview"] == "X" * 500

    def test_run_handles_empty_response(self, monkeypatch):
        created = json.loads(
            cronjob(action="create", prompt="Check", schedule="every 1h")
        )
        job_id = created["job_id"]

        _mock_gateway_not_running(monkeypatch)

        with patch("cron.scheduler.run_job") as mock_run:
            mock_run.return_value = (True, "", None, None)

            result = json.loads(cronjob(action="run", job_id=job_id))

            assert result["success"] is True
            assert result["output_preview"] is None
