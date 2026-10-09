#!/usr/bin/env python3
"""
Tests for the swarm core infrastructure using unittest.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add the swarm directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "swarm"))

from core import EventBus, WorktreeManager, ClaudeRunner, FakeClaudeShim, LogEvent, TaskEvent, OutEvent, EventType


class TestEventBus(unittest.TestCase):
    """Test the event bus functionality"""

    def setUp(self):
        self.event_bus = EventBus()

    def tearDown(self):
        # Clean up any remaining subscribers
        self.event_bus._subscribers.clear()
        self.event_bus._history.clear()

    def test_publish_and_subscribe(self):
        """Test publishing and subscribing to events"""
        async def run_test():
            # Subscribe to events
            queue = asyncio.Queue()
            self.event_bus._subscribers.add(queue)

            try:
                # Publish an event
                test_event = LogEvent(
                    task_id="test",
                    level="info",
                    message="hello world"
                )
                await self.event_bus.publish(test_event.to_event())

                # Receive the event
                received = await asyncio.wait_for(queue.get(), timeout=1.0)
                self.assertEqual(received.type, EventType.LOG)
                self.assertEqual(received.data["message"], "hello world")
            finally:
                self.event_bus._subscribers.discard(queue)

        asyncio.run(run_test())

    def test_history_replay(self):
        """Test that new subscribers get history replayed"""
        async def run_test():
            # Publish some events
            await self.event_bus.publish(LogEvent(
                task_id="test1",
                level="info",
                message="event 1"
            ).to_event())
            await self.event_bus.publish(LogEvent(
                task_id="test2",
                level="info",
                message="event 2"
            ).to_event())

            # Subscribe now - should get history
            events_received = []

            async def collect_events():
                async for event in self.event_bus.subscribe():
                    events_received.append(event)
                    if len(events_received) >= 3:  # 2 from history + 1 new
                        break

            # Start collecting
            collection_task = asyncio.create_task(collect_events())

            # Give it a moment to start and get history
            await asyncio.sleep(0.1)

            # Publish a new event while subscribed
            await self.event_bus.publish(LogEvent(
                task_id="test3",
                level="info",
                message="event 3"
            ).to_event())

            # Wait for collection to complete
            try:
                await asyncio.wait_for(collection_task, timeout=2.0)
            except asyncio.TimeoutError:
                collection_task.cancel()
                raise

            # Verify we got the events in order
            self.assertEqual(len(events_received), 3)

            # Check first event (from history)
            self.assertEqual(events_received[0].type, EventType.LOG)
            self.assertEqual(events_received[0].data["message"], "event 1")
            self.assertEqual(events_received[0].data["task_id"], "test1")

            # Check second event (from history)
            self.assertEqual(events_received[1].type, EventType.LOG)
            self.assertEqual(events_received[1].data["message"], "event 2")
            self.assertEqual(events_received[1].data["task_id"], "test2")

            # Check third event (new)
            self.assertEqual(events_received[2].type, EventType.LOG)
            self.assertEqual(events_received[2].data["message"], "event 3")
            self.assertEqual(events_received[2].data["task_id"], "test3")

        asyncio.run(run_test())


class TestWorktreeManager(unittest.TestCase):
    """Test the worktree manager functionality"""

    def setUp(self):
        # Create a temporary git repository for testing
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name) / "test_repo"
        self.repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_path, check=True)

        # Create initial commit
        (self.repo_path / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        self.worktree_manager = WorktreeManager(self.repo_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_remove_worktree(self):
        """Test creating and removing worktrees"""
        async def run_test():
            # Create a worktree with a unique branch name
            import uuid
            branch_name = f"test-branch-{uuid.uuid4().hex[:8]}"
            worktree_path = self.worktree_manager.repo_path / "test_worktree"
            await self.worktree_manager.create_worktree(branch_name, worktree_path)

            # Verify it exists
            self.assertTrue(worktree_path.exists())

            # List worktrees should show our worktree
            worktrees = await self.worktree_manager.list_worktrees()
            worktree_paths = [wt['path'] for wt in worktrees]
            self.assertIn(worktree_path, worktree_paths)

            # Remove the worktree
            await self.worktree_manager.remove_worktree(worktree_path, force=True)

            # Verify it's gone
            self.assertFalse(worktree_path.exists())

            # List worktrees should not show our worktree anymore
            worktrees = await self.worktree_manager.list_worktrees()
            worktree_paths = [wt['path'] for wt in worktrees]
            self.assertNotIn(worktree_path, worktree_paths)

        asyncio.run(run_test())

    def test_branch_operations(self):
        """Test branch creation and checking"""
        async def run_test():
            # Create a new branch with unique name
            import uuid
            branch_name = f"feature-test-{uuid.uuid4().hex[:8]}"
            await self.worktree_manager.create_branch(branch_name)

            # Check that it exists
            self.assertTrue(await self.worktree_manager.check_branch_exists(branch_name))

            # Check that a non-existent branch doesn't exist
            self.assertFalse(await self.worktree_manager.check_branch_exists("non-existent-branch"))

        asyncio.run(run_test())

    def test_merge_operations(self):
        """Test merging branches"""
        async def run_test():
            # Create and switch to a feature branch
            import uuid
            feature_branch = f"feature-add-readme-{uuid.uuid4().hex[:8]}"
            await self.worktree_manager.create_branch(feature_branch)

            # Make a change in the feature branch
            readme_path = self.worktree_manager.repo_path / "README.md"
            original_content = readme_path.read_text()
            readme_path.write_text(original_content + "\n## Feature Added\n")

            # Commit the change
            subprocess.run(["git", "add", "README.md"], cwd=self.worktree_manager.repo_path, check=True)
            subprocess.run(["git", "commit", "-m", "Add feature documentation"],
                          cwd=self.worktree_manager.repo_path, check=True)

            # Switch back to master
            subprocess.run(["git", "checkout", "master"], cwd=self.worktree_manager.repo_path, check=True)

            # Merge the feature branch
            result = await self.worktree_manager.merge_branch(feature_branch, "master", no_ff=True)
            self.assertTrue(result)  # Merge should succeed

            # Verify the change is present
            self.assertIn("## Feature Added\n", readme_path.read_text())

        asyncio.run(run_test())


class TestFakeClaudeShim(unittest.TestCase):
    """Test the fake claude shim"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name) / "test_repo"
        self.repo_path.mkdir()
        self.fake_claude_shim = FakeClaudeShim(self.repo_path / "fake_claude")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_shim_creation(self):
        """Test that the shim is created correctly"""
        self.assertTrue(self.fake_claude_shim.script_path.exists())
        self.assertTrue(os.access(self.fake_claude_shim.script_path, os.X_OK))

        # Check that it contains expected content
        content = self.fake_claude_shim.script_path.read_text()
        self.assertIn("Fake Claude Code shim", content)
        self.assertIn("--output-format", content)
        self.assertIn("--session-id", content)
        self.assertIn("--resume", content)

    def test_shim_output_format(self):
        """Test that the shim executes and produces expected stream-json output"""
        # Run the shim with a simple prompt
        result = subprocess.run([
            str(self.fake_claude_shim.script_path),
            "-p", "say hello",
            "--output-format", "stream-json"
        ], cwd=self.repo_path, capture_output=True, text=True, timeout=5.0)

        self.assertEqual(result.returncode, 0)
        output = result.stdout

        # Should contain JSON lines
        lines = output.strip().split('\n')
        json_lines = [line for line in lines if line.strip().startswith('{')]
        self.assertGreater(len(json_lines), 0)

        # Should contain a result event
        result_events = [line for line in json_lines if '"type":"result"' in line or '"type": "result"' in line]
        self.assertGreater(len(result_events), 0)

        # Parse the result event
        result_event = json.loads(result_events[0])
        self.assertEqual(result_event["type"], "result")
        self.assertIn("Fake response to:", result_event["result"])

    def test_shim_resume(self):
        """Test that the shim honors --resume flag"""
        # First run to get a session ID
        result1 = subprocess.run([
            str(self.fake_claude_shim.script_path),
            "-p", "first prompt",
            "--output-format", "stream-json"
        ], cwd=self.repo_path, capture_output=True, text=True, timeout=10.0)

        self.assertEqual(result1.returncode, 0)
        output1 = result1.stdout

        # Extract session ID from the init event
        session_id = None
        for line in output1.split('\n'):
            if line.strip() and '"subtype":' in line and 'init' in line:
                try:
                    init_event = json.loads(line)
                    session_id = init_event.get('session_id')
                    if session_id:
                        break
                except json.JSONDecodeError:
                    continue

        self.assertIsNotNone(session_id, "Could not extract session ID from first run")
        self.assertTrue(len(session_id) > 0, "Session ID should not be empty")

        # Second run with --resume should use the same session ID
        result2 = subprocess.run([
            str(self.fake_claude_shim.script_path),
            "-p", "second prompt",
            "--output-format", "stream-json",
            "--resume", session_id
        ], cwd=self.repo_path, capture_output=True, text=True, timeout=10.0)

        self.assertEqual(result2.returncode, 0)
        output2 = result2.stdout

        # Check that the session ID in the second run's init event matches the first
        session_id2 = None
        for line in output2.split('\n'):
            if line.strip() and '"subtype":' in line and 'init' in line:
                try:
                    init_event = json.loads(line)
                    session_id2 = init_event.get('session_id')
                    if session_id2:
                        break
                except json.JSONDecodeError:
                    continue

        self.assertIsNotNone(session_id2, "Could not extract session ID from second run")
        self.assertEqual(session_id, session_id2, "Session ID should match when using --resume")


class TestClaudeRunner(unittest.TestCase):
    """Test the Claude runner functionality"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name) / "test_repo"
        self.repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_path, check=True)

        # Create initial commit
        (self.repo_path / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        self.event_bus = EventBus()
        self.worktree_manager = WorktreeManager(self.repo_path)
        self.claude_runner = ClaudeRunner(self.event_bus, self.worktree_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runner_construction(self):
        """Test that the ClaudeRunner is constructed correctly"""
        self.assertIsInstance(self.claude_runner, ClaudeRunner)
        self.assertIsInstance(self.claude_runner.event_bus, EventBus)
        self.assertIsInstance(self.claude_runner.worktree_manager, WorktreeManager)
        self.assertEqual(self.claude_runner.permission_mode, "acceptEdits")
        self.assertIsNone(self.claude_runner.worker_model)

    def test_runner_command_construction(self):
        """Test that the runner constructs the command correctly (we won't actually run it)"""
        # We'll test by checking that the run_task method returns an async generator
        # and that it doesn't throw an exception when called (we won't iterate it)
        async def run_test():
            # Create a worktree for the task
            import uuid
            branch_name = f"test-branch-{uuid.uuid4().hex[:8]}"
            worktree_path = self.repo_path / "worktree"
            await self.worktree_manager.create_worktree(branch_name, worktree_path)

            try:
                # Get the async generator
                gen = self.claude_runner.run_task(
                    task_id="test-task",
                    prompt="say hello",
                    max_turns=2,
                    worktree_path=worktree_path
                )

                # Check that it's an async generator
                self.assertTrue(hasattr(gen, '__aiter__'))
                self.assertTrue(hasattr(gen, '__anext__'))

                # We don't actually run it because that would require the claude binary
                # but we can check that it was set up correctly by inspecting the internal state?
                # For now, just checking that it returns an async generator is enough.

            finally:
                # Clean up worktree
                await self.worktree_manager.remove_worktree(worktree_path, force=True)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()