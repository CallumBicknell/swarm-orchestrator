"""
Planner module for the Swarm orchestrator.
Handles LLM adaptation, task planning, validation, and DAG construction.
"""

import json
import re
import asyncio
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import logging
import hashlib

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass
class LLMResponse:
    """Standardized LLM response"""
    content: str
    provider: LLMProvider
    model: str
    usage: Dict[str, int] = field(default_factory=dict)
    raw_response: Any = None


@dataclass
class Task:
    """Represents a task in the orchestrator"""
    id: str
    title: str
    prompt: str
    dependencies: List[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    status: str = "pending"  # pending, starting, running, reviewing, retrying, merging, merged, failed, skipped
    files: List[str] = field(default_factory=list)  # Files this task will modify
    acceptance_criteria: List[str] = field(default_factory=list)
    cost_estimate: float = 0.0
    actual_cost: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "dependencies": self.dependencies,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "status": self.status,
            "files": self.files,
            "acceptance_criteria": self.acceptance_criteria,
            "cost_estimate": self.cost_estimate,
            "actual_cost": self.actual_cost
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Task':
        return cls(
            id=data["id"],
            title=data["title"],
            prompt=data["prompt"],
            dependencies=data.get("dependencies", []),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            status=data.get("status", "pending"),
            files=data.get("files", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            cost_estimate=data.get("cost_estimate", 0.0),
            actual_cost=data.get("actual_cost", 0.0)
        )


@dataclass
class ValidationResult:
    """Result of task validation"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, error: str):
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str):
        self.warnings.append(warning)


class LLMAdapter:
    """Adapter for different LLM providers"""

    def __init__(self, provider: LLMProvider, model: str, api_key: Optional[str] = None):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self._setup_client()

    def _setup_client(self):
        """Initialize the appropriate LLM client"""
        if self.provider == LLMProvider.ANTHROPIC:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                logger.warning("Anthropic package not installed. Using mock client.")
                self.client = None
        elif self.provider == LLMProvider.OPENAI:
            try:
                import openai
                self.client = openai.OpenAI(api_key=self.api_key)
            except ImportError:
                logger.warning("OpenAI package not installed. Using mock client.")
                self.client = None
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def generate_structured_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        max_retries: int = 2
    ) -> LLMResponse:
        """
        Generate a structured JSON response from the LLM.
        Retries on JSON parsing failure.
        """
        # For now, return a mock response since we're in testing mode
        # In production, this would call the actual LLM API
        return await self._mock_generate_response(prompt, system_prompt)

    async def _mock_generate_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None
    ) -> LLMResponse:
        """Mock LLM response for testing"""
        # Simulate processing delay
        await asyncio.sleep(0.1)

        # Return a structured response based on the prompt type
        if "plan" in prompt.lower() or "goal" in prompt.lower():
            # Mock planning response
            mock_response = {
                "tasks": [
                    {
                        "id": "task_1",
                        "title": "Implement user authentication",
                        "prompt": "Create login/logout functionality with secure password handling",
                        "dependencies": [],
                        "files": ["auth/login.py", "auth/logout.py", "auth/models.py"],
                        "acceptance_criteria": [
                            "Users can login with valid credentials",
                            "Users can logout securely",
                            "Passwords are hashed using bcrypt"
                        ]
                    },
                    {
                        "id": "task_2",
                        "title": "Create user registration",
                        "prompt": "Build user registration with email verification",
                        "dependencies": ["task_1"],
                        "files": ["auth/register.py", "email/templates.py"],
                        "acceptance_criteria": [
                            "Users can register with email",
                            "Email verification is sent",
                            "Account is inactive until verified"
                        ]
                    }
                ]
            }
            content = json.dumps(mock_response, indent=2)
        else:
            # Generic response
            mock_response = {
                "analysis": "Task completed successfully",
                "recommendations": ["Consider adding error handling", "Add unit tests"],
                "confidence": 0.9
            }
            content = json.dumps(mock_response, indent=2)

        return LLMResponse(
            content=content,
            provider=self.provider,
            model=self.model,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            raw_response=None
        )

    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from LLM response text"""
        # Try to find JSON object in the text
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # If that fails, try to parse the whole text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response: {text}")
            raise ValueError(f"LLM did not return valid JSON: {e}")


class TaskPlanner:
    """Main planning component that converts goals into task DAGs"""

    def __init__(self, llm_adapter: LLMAdapter):
        self.llm_adapter = llm_adapter

    async def create_plan(
        self,
        goal: str,
        repo_path: Path,
        context: Optional[Dict] = None
    ) -> List[Task]:
        """
        Create a execution plan from a goal.
        Returns a list of Task objects forming a DAG.
        """
        logger.info(f"Creating plan for goal: {goal}")

        # Build planning prompt
        system_prompt = """You are a software planning expert.
        Given a software development goal, break it down into discrete, implementable tasks.

        For each task, provide:
        - id: Unique identifier (use snake_case, e.g., 'auth_login', 'api_user_create')
        - title: Human-readable task title
        - prompt: Detailed prompt for the Claude Code agent to execute this task
        - dependencies: List of task IDs that must be completed before this task can start
        - files: List of files that this task will create or modify
        - acceptance_criteria: List of clear, testable criteria for when the task is done

        Requirements:
        1. Tasks should be self-contained and have clear ownership of files
        2. Dependencies should form a DAG (no cycles)
        3. Each task should have a single, clear responsibility
        4. File ownership should be disjoint between tasks where possible
        5. Return ONLY valid JSON in the format specified

        Format your response as a JSON object with a "tasks" array containing task objects."""

        user_prompt = f"""
        Goal: {goal}

        Repository context:
        - Path: {repo_path}
        - Context: {json.dumps(context or {}, indent=2)}

        Break this goal into implementable tasks. Each task should be small enough
        to be completed by a single Claude Code agent session.
        """

        # Get structured response from LLM
        llm_response = await self.llm_adapter.generate_structured_response(
            prompt=user_prompt,
            system_prompt=system_prompt
        )

        # Parse the response
        try:
            response_data = self.llm_adapter._extract_json(llm_response.content)
            task_dicts = response_data.get("tasks", [])
        except (ValueError, KeyError) as e:
            logger.error(f"Failed to parse LLM response: {e}")
            # Fallback to basic task breakdown
            task_dicts = self._fallback_planning(goal)

        # Convert to Task objects
        tasks = []
        for task_dict in task_dicts:
            try:
                task = Task.from_dict(task_dict)
                # Sanitize task ID
                task.id = self._sanitize_task_id(task.id)
                tasks.append(task)
            except Exception as e:
                logger.warning(f"Skipping invalid task: {task_dict}. Error: {e}")

        # Validate and refine the task DAG
        validation_result = self.validate_tasks(tasks)
        if not validation_result.is_valid:
            logger.error(f"Task validation failed: {validation_result.errors}")
            # Try to fix common issues
            tasks = self._fix_task_issues(tasks)

        # Ensure we have a valid DAG
        tasks = self._ensure_dag(tasks)

        logger.info(f"Created plan with {len(tasks)} tasks")
        for task in tasks:
            logger.debug(f"  - {task.id}: {task.title} (deps: {task.dependencies})")

        return tasks

    def _sanitize_task_id(self, task_id: str) -> str:
        """Sanitize task ID to be safe for use as filenames, etc."""
        # Keep only alphanumeric, underscore, hyphen
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)
        # Ensure it starts with a letter
        if sanitized and not sanitized[0].isalpha():
            sanitized = 'task_' + sanitized
        return sanitized.lower()

    def _fallback_planning(self, goal: str) -> List[Dict]:
        """Fallback planning when LLM fails"""
        logger.warning("Using fallback planning due to LLM failure")
        # Simple heuristic breakdown
        words = goal.lower().split()
        if len(words) <= 3:
            # Very simple goal
            return [{
                "id": "main_task",
                "title": goal.title(),
                "prompt": f"Implement: {goal}",
                "dependencies": [],
                "files": ["main.py"],
                "acceptance_criteria": [f"Goal achieved: {goal}"]
            }]
        else:
            # Break into logical components
            return [
                {
                    "id": "analysis",
                    "title": "Analyze Requirements",
                    "prompt": f"Analyze and document requirements for: {goal}",
                    "dependencies": [],
                    "files": ["requirements.md"],
                    "acceptance_criteria": ["Requirements documented"]
                },
                {
                    "id": "implementation",
                    "title": "Implement Solution",
                    "prompt": f"Implement the solution for: {goal}",
                    "dependencies": ["analysis"],
                    "files": ["solution.py"],
                    "acceptance_criteria": ["Solution implements goal"]
                },
                {
                    "id": "testing",
                    "title": "Test Solution",
                    "prompt": f"Test and verify the solution for: {goal}",
                    "dependencies": ["implementation"],
                    "files": ["test_solution.py"],
                    "acceptance_criteria": ["All tests pass"]
                }
            ]

    def validate_tasks(self, tasks: List[Task]) -> ValidationResult:
        """Validate a list of tasks for correctness"""
        result = ValidationResult(is_valid=True)
        task_ids = {task.id for task in tasks}

        # Check for duplicate IDs
        id_counts = {}
        for task in tasks:
            id_counts[task.id] = id_counts.get(task.id, 0) + 1

        for task_id, count in id_counts.items():
            if count > 1:
                result.add_error(f"Duplicate task ID: {task_id}")

        # Validate each task
        for task in tasks:
            # Check that dependencies exist
            for dep in task.dependencies:
                if dep not in task_ids:
                    result.add_error(f"Task '{task.id}' depends on unknown task: '{dep}'")

            # Check for self-dependency
            if task.id in task.dependencies:
                result.add_error(f"Task '{task.id}' depends on itself")

            # Check that task has essential fields
            if not task.id.strip():
                result.add_error("Task has empty ID")
            if not task.title.strip():
                result.add_warning(f"Task '{task.id}' has empty title")
            if not task.prompt.strip():
                result.add_error(f"Task '{task.id}' has empty prompt")

        return result

    def _fix_task_issues(self, tasks: List[Task]) -> List[Task]:
        """Attempt to fix common task issues"""
        # Fix duplicate IDs by adding suffixes
        seen_ids = {}
        for task in tasks:
            original_id = task.id
            counter = 1
            while task.id in seen_ids:
                task.id = f"{original_id}_{counter}"
                counter += 1
            seen_ids[task.id] = True

        # Remove self-dependencies
        for task in tasks:
            if task.id in task.dependencies:
                task.dependencies.remove(task.id)
                logger.warning(f"Removed self-dependency from task {task.id}")

        # Fix unknown dependencies by removing them
        valid_ids = {task.id for task in tasks}
        for task in tasks:
            original_deps = task.dependencies.copy()
            task.dependencies = [dep for dep in task.dependencies if dep in valid_ids]
            removed = set(original_deps) - set(task.dependencies)
            if removed:
                logger.warning(f"Removed unknown dependencies from task {task.id}: {removed}")

        return tasks

    def _ensure_dag(self, tasks: List[Task]) -> List[Task]:
        """Ensure the task graph is a DAG by removing edges that create cycles"""
        # Build adjacency list
        graph = {task.id: set(task.dependencies) for task in tasks}

        # Detect cycles using DFS
        def has_cycle(node, visited, rec_stack):
            if node not in visited:
                visited.add(node)
                rec_stack.add(node)

                for neighbor in graph.get(node, []):
                    if neighbor not in visited:
                        if has_cycle(neighbor, visited, rec_stack):
                            return True
                    elif neighbor in rec_stack:
                        return True

            rec_stack.remove(node)
            return False

        visited = set()
        rec_stack = set()

        # Check for cycles
        for task_id in graph:
            if task_id not in visited:
                if has_cycle(task_id, visited, rec_stack):
                    logger.warning(f"Cycle detected involving task {task_id}")
                    # Break the cycle by removing one dependency
                    # Simple approach: remove dependencies that point to nodes already in rec_stack
                    for task in tasks:
                        original_deps = task.dependencies.copy()
                        task.dependencies = [
                            dep for dep in task.dependencies
                            if not (dep in rec_stack and dep != task.id)
                        ]
                        removed = set(original_deps) - set(task.dependencies)
                        if removed:
                            logger.info(f"Broke cycle by removing deps {removed} from task {task.id}")
                    # Reset and recheck
                    visited.clear()
                    rec_stack.clear()

        return tasks


class DAGScheduler:
    """Schedules tasks based on dependencies and resource constraints"""

    def __init__(self, max_parallel: int = 3):
        self.max_parallel = max_parallel
        self.running_tasks: set = set()
        self.completed_tasks: set = set()
        self.failed_tasks: set = set()

    def get_ready_tasks(self, tasks: List[Task]) -> List[Task]:
        """Get tasks that are ready to run (dependencies satisfied)"""
        ready = []
        for task in tasks:
            if task.id in self.completed_tasks or task.id in self.running_tasks:
                continue  # Already completed or running

            if task.id in self.failed_tasks:
                continue  # Failed tasks are not ready

            # Check if all dependencies are completed
            deps_completed = all(
                dep in self.completed_tasks
                for dep in task.dependencies
            )

            if deps_completed:
                ready.append(task)

        return ready

    def can_schedule_more(self) -> bool:
        """Check if we can schedule more tasks based on parallel limit"""
        return len(self.running_tasks) < self.max_parallel

    def schedule_tasks(self, tasks: List[Task]) -> List[Task]:
        """Schedule up to max_parallel ready tasks"""
        ready_tasks = self.get_ready_tasks(tasks)
        schedulable = []

        for task in ready_tasks:
            if self.can_schedule_more():
                self.running_tasks.add(task.id)
                schedulable.append(task)
            else:
                break  # Reached parallel limit

        return schedulable

    def task_completed(self, task_id: str, success: bool = True):
        """Mark a task as completed"""
        self.running_tasks.discard(task_id)
        if success:
            self.completed_tasks.add(task_id)
        else:
            self.failed_tasks.add(task_id)

        logger.info(f"Task {task_id} marked as {'completed' if success else 'failed'}")

    def get_progress(self, total_tasks: int) -> Dict[str, int]:
        """Get scheduling progress"""
        return {
            "completed": len(self.completed_tasks),
            "running": len(self.running_tasks),
            "failed": len(self.failed_tasks),
            "ready": len(self.get_ready_tasks([])),  # We'd need to pass tasks here
            "total": total_tasks
        }


class Orchestrator:
    """Main orchestrator that ties everything together"""

    def __init__(self,
                 llm_provider: LLMProvider = LLMProvider.ANTHROPIC,
                 model: str = "claude-3-5-sonnet-20241022",
                 api_key: Optional[str] = None,
                 max_parallel: int = 3):
        self.llm_adapter = LLMAdapter(llm_provider, model, api_key)
        self.planner = TaskPlanner(self.llm_adapter)
        self.scheduler = DAGScheduler(max_parallel=max_parallel)
        self.tasks: Dict[str, Task] = {}
        logger.info(f"Orchestrator initialized with {llm_provider.value} ({model})")

    async def orchestrate(self, goal: str, repo_path: Path) -> bool:
        """
        Main orchestration loop.
        Returns True if goal was achieved successfully.
        """
        logger.info(f"Starting orchestration for goal: {goal}")

        # Phase 1: Planning
        task_list = await self.planner.create_plan(goal, repo_path)
        self.tasks = {task.id: task for task in task_list}

        if not task_list:
            logger.error("No tasks created from planning phase")
            return False

        # Phase 2: Execution loop
        max_rounds = 10  # Prevent infinite loops
        round_num = 0

        while round_num < max_rounds:
            round_num += 1
            logger.info(f"--- Round {round_num} ---")

            # Check if all tasks are done
            if self._all_tasks_done():
                logger.info("All tasks completed!")
                return self._check_goal_achieved(goal)

            # Get tasks ready to run
            ready_tasks = self.scheduler.get_ready_tasks(list(self.tasks.values()))

            if not ready_tasks:
                # No ready tasks - check if we're blocked
                if self._is_blocked():
                    logger.warning("Execution blocked - no ready tasks and no running tasks")
                    return False
                # Wait a bit and try again (in real implementation, we'd wait for events)
                await asyncio.sleep(1)
                continue

            # Schedule tasks to run
            tasks_to_run = self.scheduler.schedule_tasks(ready_tasks)
            logger.info(f"Scheduling {len(tasks_to_run)} tasks: {[t.id for t in tasks_to_run]}")

            # In a real implementation, we would actually run these tasks
            # For now, we'll simulate completion
            for task in tasks_to_run:
                await self._simulate_task_execution(task)

            # Brief pause between rounds
            await asyncio.sleep(0.5)

        logger.warning(f"Orchestration timed out after {max_rounds} rounds")
        return False

    def _all_tasks_done(self) -> bool:
        """Check if all tasks are in a terminal state"""
        for task in self.tasks.values():
            if task.status not in ["merged", "failed", "skipped"]:
                return False
        return True

    def _is_blocked(self) -> bool:
        """Check if execution is blocked (no ready tasks and no running tasks)"""
        ready_count = len(self.scheduler.get_ready_tasks(list(self.tasks.values())))
        running_count = len(self.scheduler.running_tasks)
        return ready_count == 0 and running_count == 0 and not self._all_tasks_done()

    async def _simulate_task_execution(self, task: Task):
        """Simulate task execution (replace with actual ClaudeRunner in real implementation)"""
        logger.info(f"Executing task: {task.id} - {task.title}")

        # Update task status
        task.status = "running"
        task.attempts += 1

        # Simulate work
        await asyncio.sleep(0.2)

        # Simulate success/failure (90% success rate for demo)
        import random
        success = random.random() > 0.1

        if success:
            task.status = "merged"
            task.actual_cost = task.cost_estimate or 0.01
            logger.info(f"Task {task.id} completed successfully")
        else:
            if task.attempts < task.max_attempts:
                task.status = "pending"  # Will be retried
                logger.warning(f"Task {task.id} failed, attempt {task.attempts}/{task.max_attempts}")
            else:
                task.status = "failed"
                logger.error(f"Task {task.id} failed after {task.attempts} attempts")

        # Mark as completed in scheduler
        self.scheduler.task_completed(task.id, success)

    def _check_goal_achieved(self, goal: str) -> bool:
        """Check if the overall goal has been achieved"""
        # In a real implementation, this would involve checking acceptance criteria
        # For now, we'll say it's achieved if all non-skipped tasks succeeded
        successful_tasks = [
            t for t in self.tasks.values()
            if t.status == "merged"
        ]
        failed_tasks = [
            t for t in self.tasks.values()
            if t.status == "failed"
        ]
        skipped_tasks = [
            t for t in self.tasks.values()
            if t.status == "skipped"
        ]

        logger.info(f"Task outcomes: {len(successful_tasks)} succeeded, "
                   f"{len(failed_tasks)} failed, {len(skipped_tasks)} skipped")

        # Goal is achieved if no critical tasks failed
        # For simplicity, we'll consider it achieved if we have some successes
        return len(successful_tasks) > 0 and len(failed_tasks) == 0

    def get_status(self) -> Dict:
        """Get current orchestrator status"""
        task_statuses = {}
        for task_id, task in self.tasks.items():
            task_statuses[task_id] = {
                "status": task.status,
                "attempts": task.attempts,
                "dependencies": task.dependencies
            }

        return {
            "tasks": task_statuses,
            "scheduler": self.scheduler.get_progress(len(self.tasks)),
            "total_tasks": len(self.tasks)
        }


# Convenience functions for easy usage
async def orchestrator_goal(
    goal: str,
    repo_path: Path,
    provider: str = "anthropic",
    model: Optional[str] = None,
    api_key: Optional[str] = None
) -> bool:
    """
    Convenience function to orchestrate a goal.

    Args:
        goal: The goal to achieve
        repo_path: Path to the git repository
        provider: LLM provider ("anthropic" or "openai")
        model: Specific model to use (defaults vary by provider)
        api_key: API key for the LLM provider

    Returns:
        True if goal was achieved successfully
    """
    llm_provider = LLMProvider.ANTHROPIC if provider.lower() == "anthropic" else LLMProvider.OPENAI

    # Set default models
    if model is None:
        if llm_provider == LLMProvider.ANTHROPIC:
            model = "claude-3-5-sonnet-20241022"
        else:
            model = "gpt-4-turbo-preview"

    orchestrator = Orchestrator(
        llm_provider=llm_provider,
        model=model,
        api_key=api_key
    )

    return await orchestrator.orchestrate(goal, repo_path)


if __name__ == "__main__":
    # Simple test
    import asyncio

    async def test():
        orchestrator = Orchestrator()
        result = await orchestrator.orchestrate(
            goal="Create a simple REST API for user management",
            repo_path=Path(".")
        )
        print(f"Orchestration result: {result}")
        print(f"Final status: {orchestrator.get_status()}")

    asyncio.run(test())