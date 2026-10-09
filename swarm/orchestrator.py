"""
Main orchestrator for the Swarm system.
Coordinates planning, execution, monitoring, and task management.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Callable, Any
import logging
import signal
import shutil
import os

# Handle imports for both package and direct execution
try:
    # When used as part of the swarm package
    from .planner import Orchestrator, Task, LLMProvider, TaskPlanner, DAGScheduler
    from .git_utils import WorktreeManager
    from .core import EventBus, LogEvent, EventType, ClaudeRunner, FakeClaudeShim
except ImportError:
    # When used directly or in tests
    from planner import Orchestrator, Task, LLMProvider, TaskPlanner, DAGScheduler
    from git_utils import WorktreeManager
    from core import EventBus, LogEvent, EventType, ClaudeRunner, FakeClaudeShim

logger = logging.getLogger(__name__)


class RunStatus(Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class OrchestratorRun:
    """Represents a complete orchestrator run"""
    run_id: str
    goal: str
    repo_path: Path
    status: RunStatus = RunStatus.PENDING
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    tasks: Dict[str, Task] = field(default_factory=dict)
    planner: Optional[TaskPlanner] = None
    scheduler: Optional[DAGScheduler] = None
    claude_runner: Optional[ClaudeRunner] = None
    worktree_manager: Optional[WorktreeManager] = None
    event_bus: Optional[EventBus] = None
    fake_claude: Optional[FakeClaudeShim] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "repo_path": str(self.repo_path),
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "task_count": len(self.tasks),
            "metadata": self.metadata
        }


class SwarmOrchestrator:
    """Main orchestrator class that coordinates all components"""

    def __init__(self,
                 repo_path: Path,
                 llm_provider: str = "anthropic",
                 model: Optional[str] = None,
                 api_key: Optional[str] = None,
                 max_parallel: int = 3,
                 use_fake_claude: bool = False):
        self.repo_path = repo_path.resolve()
        self.llm_provider = llm_provider
        self.model = model
        self.api_key = api_key
        self.max_parallel = max_parallel
        self.use_fake_claude = use_fake_claude

        # Core components
        self.event_bus = EventBus()
        self.worktree_manager = WorktreeManager(self.repo_path)

        # State management
        self.current_run: Optional[OrchestratorRun] = None
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.completed_tasks: Set[str] = set()
        self.failed_tasks: Set[str] = set()
        self.shutdown_requested = False

        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
        # Initiate cancellation if we have a running orchestrator
        if self.current_run and self.current_run.status == RunStatus.RUNNING:
            # Don't await here as we're in a signal handler
            asyncio.create_task(self.cancel_run())

    async def start_run(self, goal: str) -> str:
        """
        Start a new orchestrator run.
        Returns the run ID.
        """
        if self.current_run and self.current_run.status == RunStatus.RUNNING:
            raise RuntimeError("A run is already in progress")

        # Generate run ID
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # Create orchestrator run object
        self.current_run = OrchestratorRun(
            run_id=run_id,
            goal=goal,
            repo_path=self.repo_path,
            status=RunStatus.STARTING
        )

        # Initialize components for this run
        await self._initialize_run_components()

        self.logger.info(f"Started orchestrator run {run_id} for goal: {goal}")
        self.logger.info(f"Repository: {self.repo_path}")

        # Publish start event
        await self.event_bus.publish(LogEvent(
            task_id="orchestrator",
            level="info",
            message=f"Started run {run_id}: {goal}"
        ).to_event())

        return run_id

    async def _initialize_run_components(self):
        """Initialize components specific to this run"""
        run = self.current_run
        assert run is not None

        # Setup core components
        run.event_bus = self.event_bus
        run.worktree_manager = self.worktree_manager

        # Setup LLM provider
        try:
            from .planner import LLMAdapter, LLMProvider
        except ImportError:
            from planner import LLMAdapter, LLMProvider
        provider_enum = LLMProvider.ANTHROPIC if self.llm_provider == "anthropic" else LLMProvider.OPENAI

        # Setup planner
        llm_adapter = LLMAdapter(provider_enum, self.model or "", self.api_key)
        run.planner = TaskPlanner(llm_adapter)

        # Setup scheduler
        run.scheduler = DAGScheduler(max_parallel=self.max_parallel)

        # Setup Claude runner
        run.claude_runner = ClaudeRunner(self.event_bus, self.worktree_manager)

        # Setup fake Claude if requested
        if self.use_fake_claude:
            fake_claude_path = self.repo_path / ".orch" / "fake_claude"
            run.fake_claude = FakeClaudeShim(fake_claude_path)

        # Setup orchestrator-level planner (higher level)
        run.orchestrator_planner = Orchestrator(
            llm_provider=provider_enum,
            model=self.model or "",
            api_key=self.api_key,
            max_parallel=self.max_parallel
        )

    async def execute_run(self) -> bool:
        """
        Execute the current orchestrator run.
        Returns True if successful.
        """
        if not self.current_run:
            raise RuntimeError("No run has been started")

        run = self.current_run
        run.status = RunStatus.RUNNING

        self.logger.info(f"Executing run {run.run_id}")

        try:
            # Execute the orchestrator planning loop
            success = await run.orchestrator_planner.orchestrate(run.goal, run.repo_path)

            if success:
                run.status = RunStatus.COMPLETED
                self.logger.info(f"Run {run.run_id} completed successfully")

                # Publish completion event
                await self.event_bus.publish(LogEvent(
                    task_id="orchestrator",
                    level="info",
                    message=f"Run {run.run_id} completed successfully"
                ).to_event())
            else:
                run.status = RunStatus.FAILED
                self.logger.error(f"Run {run.run_id} failed")

                # Publish failure event
                await self.event_bus.publish(LogEvent(
                    task_id="orchestrator",
                    level="error",
                    message=f"Run {run.run_id} failed"
                ).to_event())

            run.end_time = time.time()
            return success

        except Exception as e:
            run.status = RunStatus.FAILED
            run.end_time = time.time()
            self.logger.exception(f"Run {run.run_id} failed with exception: {e}")

            # Publish error event
            await self.event_bus.publish(LogEvent(
                task_id="orchestrator",
                level="error",
                message=f"Run {run.run_id} failed: {str(e)}"
            ).to_event())

            return False
        finally:
            # Cleanup
            await self._cleanup_run()

    async def _cleanup_run(self):
        """Cleanup resources after a run"""
        run = self.current_run
        if not run:
            return

        self.logger.info(f"Cleaning up run {run.run_id}")

        # Cancel any running tasks
        for task_id, task in self.running_tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.running_tasks.clear()

        # Publish cleanup event
        await self.event_bus.publish(LogEvent(
            task_id="orchestrator",
            level="info",
            message=f"Cleaned up run {run.run_id}"
        ).to_event())

    async def cancel_run(self):
        """Cancel the current run"""
        if not self.current_run:
            return

        self.logger.info(f"Cancelling run {self.current_run.run_id}")
        self.shutdown_requested = True
        self.current_run.status = RunStatus.CANCELLED
        self.current_run.end_time = time.time()

        # Cancel running tasks
        for task_id, task in self.running_tasks.items():
            if not task.done():
                task.cancel()

        await self._cleanup_run()

        # Publish cancellation event
        await self.event_bus.publish(LogEvent(
            task_id="orchestrator",
            level="warning",
            message=f"Run {self.current_run.run_id} was cancelled"
        ).to_event())

    def get_run_status(self) -> Optional[Dict]:
        """Get the status of the current run"""
        if not self.current_run:
            return None

        run = self.current_run
        return {
            "run_id": run.run_id,
            "goal": run.goal,
            "repo_path": str(run.repo_path),
            "status": run.status.value,
            "start_time": run.start_time,
            "end_time": run.end_time,
            "duration": run.end_time - run.start_time if run.end_time else None,
            "task_count": len(run.tasks),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "running_tasks": len(self.running_tasks)
        }

    async def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for the current run to complete.
        Returns True if successful, False if failed or timed out.
        """
        if not self.current_run:
            raise RuntimeError("No run has been started")

        start_time = time.time()
        while self.current_run.status == RunStatus.RUNNING:
            if timeout and (time.time() - start_time) > timeout:
                self.logger.warning(f"Wait for run {self.current_run.run_id} timed out after {timeout}s")
                await self.cancel_run()
                return False

            await asyncio.sleep(0.5)

        success = self.current_run.status == RunStatus.COMPLETED
        if not success:
            self.logger.info(f"Run {self.current_run.run_id} ended with status: {self.current_run.status.value}")

        return success


# Convenience functions
async def run_orchestrator(goal: str,
                          repo_path: Path,
                          llm_provider: str = "anthropic",
                          model: Optional[str] = None,
                          api_key: Optional[str] = None,
                          max_parallel: int = 3,
                          use_fake_claude: bool = False,
                          timeout: Optional[float] = None) -> bool:
    """
    Convenience function to run the orchestrator.

    Args:
        goal: The goal to achieve
        repo_path: Path to the git repository
        llm_provider: LLM provider ("anthropic" or "openai")
        model: Specific model to use
        api_key: API key for the LLM provider
        max_parallel: Maximum number of parallel tasks
        use_fake_claude: Whether to use fake Claude for testing
        timeout: Timeout in seconds (None for no timeout)

    Returns:
        True if the goal was achieved successfully
    """
    orchestrator = SwarmOrchestrator(
        repo_path=repo_path,
        llm_provider=llm_provider,
        model=model,
        api_key=api_key,
        max_parallel=max_parallel,
        use_fake_claude=use_fake_claude
    )

    run_id = await orchestrator.start_run(goal)
    try:
        success = await orchestrator.execute_run()
        if timeout:
            # If we want to wait with timeout, we'd use wait_for_completion instead
            pass
        return success
    finally:
        if orchestrator.current_run and orchestrator.current_run.status == RunStatus.RUNNING:
            await orchestrator.cancel_run()


if __name__ == "__main__":
    # Simple test
    import asyncio
    import tempfile

    async def test():
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()

            # Initialize git repo
            proc = await asyncio.create_subprocess_exec(
                "git", "init",
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            await asyncio.create_subprocess_exec(
                "git", "config", "user.name", "Test",
                cwd=repo_path
            ).wait()

            await asyncio.create_subprocess_exec(
                "git", "config", "user.email", "test@test.com",
                cwd=repo_path
            ).wait()

            # Create initial commit
            (repo_path / "README.md").write_text("# Test Repo\n")
            proc = await asyncio.create_subprocess_exec(
                "git", "add", "README.md",
                cwd=repo_path
            )
            await proc.communicate()

            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", "Initial commit",
                cwd=repo_path
            )
            await proc.communicate()

            # Test orchestrator with fake Claude
            orchestrator = SwarmOrchestrator(
                repo_path=repo_path,
                llm_provider="anthropic",
                use_fake_claude=True
            )

            run_id = await orchestrator.start_run("Create a simple hello world program")
            print(f"Started run: {run_id}")

            # Don't actually execute since we're testing
            await orchestrator.cancel_run()
            print("Test completed successfully")

    asyncio.run(test())