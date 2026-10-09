#!/usr/bin/env python3
"""
Tests for the planner module.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
import sys
import os

# Add the swarm directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "swarm"))

from planner import (
    LLMAdapter, LLMProvider, TaskPlanner, Task,
    ValidationResult, Orchestrator, DAGScheduler
)
from orchestrator import SwarmOrchestrator, OrchestratorRun, RunStatus


class TestLLMAdapter(unittest.TestCase):
    """Test the LLM adapter"""

    def test_llm_adapter_creation(self):
        """Test that LLM adapters can be created"""
        adapter_anthropic = LLMAdapter(LLMProvider.ANTHROPIC, "claude-3-sonnet")
        adapter_openai = LLMAdapter(LLMProvider.OPENAI, "gpt-4")

        self.assertEqual(adapter_anthropic.provider, LLMProvider.ANTHROPIC)
        self.assertEqual(adapter_openai.provider, LLMProvider.OPENAI)

    def test_extract_json(self):
        """Test JSON extraction from text"""
        adapter = LLMAdapter(LLMProvider.ANTHROPIC, "test")

        # Test normal JSON
        text = '{"key": "value", "number": 42}'
        result = adapter._extract_json(text)
        self.assertEqual(result, {"key": "value", "number": 42})

        # Test JSON embedded in text
        text = 'Here is the result: {"status": "success"} and more text'
        result = adapter._extract_json(text)
        self.assertEqual(result, {"status": "success"})

        # Test invalid JSON
        text = 'This is not JSON'
        with self.assertRaises(ValueError):
            adapter._extract_json(text)


class TestTaskPlanner(unittest.TestCase):
    """Test the task planner"""

    def setUp(self):
        self.adapter = LLMAdapter(LLMProvider.ANTHROPIC, "test")
        self.planner = TaskPlanner(self.adapter)

    def test_task_creation(self):
        """Test creating a task from dict"""
        task_dict = {
            "id": "test_task",
            "title": "Test Task",
            "prompt": "Do something",
            "dependencies": ["dep1"],
            "files": ["file1.py"],
            "acceptance_criteria": ["Criteria 1"],
            "cost_estimate": 0.01
        }

        task = Task.from_dict(task_dict)
        self.assertEqual(task.id, "test_task")
        self.assertEqual(task.title, "Test Task")
        self.assertEqual(task.prompt, "Do something")
        self.assertEqual(task.dependencies, ["dep1"])
        self.assertEqual(task.files, ["file1.py"])
        self.assertEqual(task.acceptance_criteria, ["Criteria 1"])
        self.assertEqual(task.cost_estimate, 0.01)

    def test_task_to_dict(self):
        """Test converting task to dict"""
        task = Task(
            id="test_task",
            title="Test Task",
            prompt="Do something",
            dependencies=["dep1"],
            files=["file1.py"],
            acceptance_criteria=["Criteria 1"],
            cost_estimate=0.01
        )

        task_dict = task.to_dict()
        self.assertEqual(task_dict["id"], "test_task")
        self.assertEqual(task_dict["title"], "Test Task")
        self.assertEqual(task_dict["prompt"], "Do something")
        self.assertEqual(task_dict["dependencies"], ["dep1"])
        self.assertEqual(task_dict["files"], ["file1.py"])
        self.assertEqual(task_dict["acceptance_criteria"], ["Criteria 1"])
        self.assertEqual(task_dict["cost_estimate"], 0.01)

    def test_sanitize_task_id(self):
        """Test task ID sanitization"""
        planner = self.planner

        # Normal ID
        self.assertEqual(planner._sanitize_task_id("task_name"), "task_name")

        # With special characters
        self.assertEqual(planner._sanitize_task_id("task@name!"), "task_name_")

        # Starting with number
        self.assertEqual(planner._sanitize_task_id("123task"), "task_123task")

        # Empty ID
        self.assertEqual(planner._sanitize_task_id(""), "")

    def test_validate_tasks(self):
        """Test task validation"""
        # Valid tasks
        tasks = [
            Task("task1", "Task 1", "Do task 1", []),
            Task("task2", "Task 2", "Do task 2", ["task1"]),
        ]

        result = self.planner.validate_tasks(tasks)
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)

        # Invalid task - missing dependency
        tasks_invalid = [
            Task("task1", "Task 1", "Do task 1", []),
            Task("task2", "Task 2", "Do task 2", ["nonexistent"]),
        ]

        result = self.planner.validate_tasks(tasks_invalid)
        self.assertFalse(result.is_valid)
        self.assertGreater(len(result.errors), 0)

        # Self dependency
        tasks_self = [
            Task("task1", "Task 1", "Do task 1", ["task1"]),
        ]

        result = self.planner.validate_tasks(tasks_self)
        self.assertFalse(result.is_valid)
        self.assertIn("depends on itself", str(result.errors))

    def test_fallback_planning(self):
        """Test fallback planning when LLM fails"""
        # Create a planner that will fail (we'll test the fallback logic directly)
        planner = TaskPlanner(self.adapter)

        # Test simple goal
        tasks_dict = planner._fallback_planning("simple goal")
        self.assertEqual(len(tasks_dict), 1)
        self.assertEqual(tasks_dict[0]["id"], "main_task")
        self.assertEqual(tasks_dict[0]["title"], "Simple Goal")

        # Test complex goal
        tasks_dict = planner._fallback_planning("implement user authentication system")
        self.assertEqual(len(tasks_dict), 3)
        self.assertEqual(tasks_dict[0]["id"], "analysis")
        self.assertEqual(tasks_dict[1]["id"], "implementation")
        self.assertEqual(tasks_dict[2]["id"], "testing")
        self.assertIn("analysis", tasks_dict[1]["dependencies"])
        self.assertIn("implementation", tasks_dict[2]["dependencies"])


class TestDAGScheduler(unittest.TestCase):
    """Test the DAG scheduler"""

    def setUp(self):
        self.scheduler = DAGScheduler(max_parallel=2)

    def test_get_ready_tasks(self):
        """Test getting ready tasks"""
        tasks = [
            Task("task1", "Task 1", "Do task 1", []),
            Task("task2", "Task 2", "Do task 2", ["task1"]),
            Task("task3", "Task 3", "Do task 3", ["task1", "task2"]),
        ]

        # Initially, only task1 should be ready
        ready = self.scheduler.get_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "task1")

        # Mark task1 as completed
        self.scheduler.task_completed("task1", True)

        # Now task2 should be ready
        ready = self.scheduler.get_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "task2")

        # Mark task2 as completed
        self.scheduler.task_completed("task2", True)

        # Now task3 should be ready
        ready = self.scheduler.get_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "task3")

    def test_can_schedule_more(self):
        """Test parallel scheduling limits"""
        self.assertTrue(self.scheduler.can_schedule_more())  # 0 < 2

        self.scheduler.running_tasks.add("task1")
        self.assertTrue(self.scheduler.can_schedule_more())  # 1 < 2

        self.scheduler.running_tasks.add("task2")
        self.assertFalse(self.scheduler.can_schedule_more())  # 2 == 2

        self.scheduler.running_tasks.add("task3")
        self.assertFalse(self.scheduler.can_schedule_more())  # 3 > 2

    def test_schedule_tasks(self):
        """Test task scheduling"""
        tasks = [
            Task("task1", "Task 1", "Do task 1", []),
            Task("task2", "Task 2", "Do task 2", []),
            Task("task3", "Task 3", "Do task 3", []),
            Task("task4", "Task 4", "Do task 4", []),
        ]

        # Should be able to schedule 2 tasks (max_parallel=2)
        scheduled = self.scheduler.schedule_tasks(tasks)
        self.assertEqual(len(scheduled), 2)
        self.assertIn("task1", [t.id for t in scheduled])
        self.assertIn("task2", [t.id for t in scheduled])

        # After scheduling, running_tasks should have 2 items
        self.assertEqual(len(self.scheduler.running_tasks), 2)

        # Complete one task
        self.scheduler.task_completed("task1", True)

        # Should now be able to schedule one more
        scheduled = self.scheduler.schedule_tasks(tasks)
        self.assertEqual(len(scheduled), 1)
        # task3 or task4 should be scheduled (task1 is done, task2 is still running)


class TestOrchestratorComponents(unittest.TestCase):
    """Test orchestrator components"""

    def test_orchestrator_creation(self):
        """Test creating an orchestrator"""
        orchestrator = Orchestrator()
        self.assertIsNotNone(orchestrator.llm_adapter)
        self.assertIsNotNone(orchestrator.planner)
        self.assertIsNotNone(orchestrator.scheduler)

    def test_orchestrator_run_creation(self):
        """Test creating an orchestrator run"""
        run = OrchestratorRun(
            run_id="test-run",
            goal="Test goal",
            repo_path=Path("/tmp/test")
        )

        self.assertEqual(run.run_id, "test-run")
        self.assertEqual(run.goal, "Test goal")
        self.assertEqual(run.repo_path, Path("/tmp/test"))
        self.assertEqual(run.status, RunStatus.PENDING)

    def test_swarm_orchestrator_creation(self):
        """Test creating a swarm orchestrator"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()

            # Initialize basic git repo
            os.system(f"cd {repo_path} && git init >/dev/null 2>&1")
            os.system(f"cd {repo_path} && git config user.name 'Test' >/dev/null 2>&1")
            os.system(f"cd {repo_path} && git config user.email 'test@test.com' >/dev/null 2>&1")

            orchestrator = SwarmOrchestrator(
                repo_path=repo_path,
                llm_provider="anthropic",
                use_fake_claude=True
            )

            self.assertIsNotNone(orchestrator.event_bus)
            self.assertIsNotNone(orchestrator.worktree_manager)
            self.assertIsNone(orchestrator.current_run)  # No run started yet


if __name__ == "__main__":
    unittest.main()