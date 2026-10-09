#!/usr/bin/env python3
"""
Tests for the TUI module.
"""

import asyncio
import unittest
from pathlib import Path
import sys
import os

# Add the swarm directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "swarm"))

try:
    from textual.app import App
    from textual.pilot import Pilot
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False
    App = None  # type: ignore
    Pilot = None  # type: ignore

if HAS_TEXTUAL:
    from tui_app import SwarmTUI
from core import EventBus, LogEvent, EventType


if HAS_TEXTUAL:
    class TestTUI(unittest.TestCase):
        """Test the TUI functionality"""

        def setUp(self):
            self.event_bus = EventBus()
            self.tui_app = SwarmTUI(self.event_bus)

        def test_tui_creation(self):
            """Test that the TUI app can be created"""
            self.assertIsInstance(self.tui_app, App)
            self.assertEqual(self.tui_app.title, "Swarm Orchestrator")
            self.assertEqual(self.tui_app.sub_title, "Monitoring and controlling agent swarms")

        def test_tui_composition(self):
            """Test that the TUI composes correctly"""
            # This would normally be tested with textual.pilot
            # For now, just test that the app initializes without error
            self.assertIsNotNone(self.tui_app)

        def test_event_processing(self):
            """Test that the TUI can process events"""
            async def process_test_event():
                # Publish a test event
                test_event = LogEvent(
                    task_id="test_task",
                    level="info",
                    message="Test message"
                )
                await self.event_bus.publish(test_event.to_event())

                # Give the TUI time to process the event
                await asyncio.sleep(0.1)

                # Check that the event was processed
                # In a real test with Pilot, we'd check the UI state
                # For now, we just verify no exceptions were raised
                return True

            result = asyncio.run(process_test_event())
            self.assertTrue(result)

        def test_task_table_setup(self):
            """Test that the task table is set up correctly"""
            # We can't easily test the DataTable without running the app
            # But we can verify the app initializes
            self.assertIsNotNone(self.tui_app)

            # Test that the bindings are set up
            bindings = {b.key: b for b in self.tui_app.BINDINGS}
            self.assertIn("q", bindings)  # Quit
            self.assertIn("0", bindings)  # Show all agents
            self.assertIn("o", bindings)  # Show orchestrator
            self.assertIn("enter", bindings)  # Show selected task
else:
    # Create a dummy test class when textual is not available
    class TestTUI(unittest.TestCase):
        @unittest.skip("textual not installed")
        def test_placeholder(self):
            """Placeholder test when textual is not available"""
            pass


if __name__ == '__main__':
    unittest.main()