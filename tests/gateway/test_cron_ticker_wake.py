"""Tests for the cron ticker wake file mechanism in gateway/run.py."""

import os
import pytest
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestCronTickerWakeFile:
    """Verify that the cron ticker responds to wake files within ~1s."""

    def test_wake_file_triggers_early_wakeup(self, tmp_path, monkeypatch):
        """If a wake file is created, the ticker should break out of its
        sleep loop within ~1 second instead of waiting the full interval."""
        import gateway.run as run_module

        wake_file = tmp_path / ".cron.wake"
        stop_event = threading.Event()
        tick_count = [0]

        def mock_cron_tick(verbose=False, adapters=None, loop=None):
            tick_count[0] += 1

        # Directly patch module-level _hermes_home before the function reads it.
        # The ticker constructs: _wake_file = _hermes_home / "cron" / ".cron.wake"
        original_hermes_home = run_module._hermes_home
        run_module._hermes_home = tmp_path
        # Ensure the cron subdirectory exists (the ticker expects it)
        (tmp_path / "cron").mkdir(exist_ok=True)
        wake_file = tmp_path / "cron" / ".cron.wake"

        with patch("cron.scheduler.tick", side_effect=mock_cron_tick):
            # Run in a thread
            ticker_thread = threading.Thread(
                target=run_module._start_cron_ticker,
                args=(stop_event,),
                kwargs={"adapters": {}, "loop": None, "interval": 60},
            )
            ticker_thread.start()

            # Wait for the first tick
            time.sleep(1.5)
            first_tick = tick_count[0]

            # Touch the wake file at the correct path
            wake_file.touch()

            # Should wake up within ~1.5s (1s poll + buffer)
            time.sleep(2)

            second_tick = tick_count[0]

            stop_event.set()
            ticker_thread.join(timeout=5)

            # Restore
            run_module._hermes_home = original_hermes_home

            # Should have at least 2 ticks (initial + wake-triggered)
            assert second_tick >= first_tick + 1, \
                f"Wake file did not trigger early wakeup: {first_tick} → {second_tick}"

    def test_no_wake_file_waits_full_interval(self, tmp_path):
        """Without a wake file, the ticker should tick once per interval."""
        stop_event = threading.Event()
        tick_count = [0]

        def mock_cron_tick(verbose=False, adapters=None, loop=None):
            tick_count[0] += 1

        with patch("cron.scheduler.tick", side_effect=mock_cron_tick):
            with patch("gateway.run._hermes_home", tmp_path):
                from gateway.run import _start_cron_ticker

                ticker_thread = threading.Thread(
                    target=_start_cron_ticker,
                    args=(stop_event,),
                    kwargs={"adapters": {}, "loop": None, "interval": 3},
                )
                ticker_thread.start()

                # After 1 second, should have exactly 1 tick
                time.sleep(1)
                assert tick_count[0] == 1, \
                    f"Expected 1 tick after 1s, got {tick_count[0]}"

                # After 4 seconds (1 + 3), should have 2 ticks
                time.sleep(3)
                assert tick_count[0] == 2, \
                    f"Expected 2 ticks after 4s, got {tick_count[0]}"

                stop_event.set()
                ticker_thread.join(timeout=5)

    def test_stop_event_breaks_loop(self, tmp_path):
        """Setting stop_event should break the ticker loop."""
        stop_event = threading.Event()
        tick_count = [0]

        def mock_cron_tick(verbose=False, adapters=None, loop=None):
            tick_count[0] += 1

        with patch("cron.scheduler.tick", side_effect=mock_cron_tick):
            with patch("gateway.run._hermes_home", tmp_path):
                from gateway.run import _start_cron_ticker

                ticker_thread = threading.Thread(
                    target=_start_cron_ticker,
                    args=(stop_event,),
                    kwargs={"adapters": {}, "loop": None, "interval": 60},
                )
                ticker_thread.start()

                time.sleep(0.5)
                stop_event.set()
                ticker_thread.join(timeout=5)

                assert not ticker_thread.is_alive(), "Ticker thread did not stop"
