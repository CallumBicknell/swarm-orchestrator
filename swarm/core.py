#!/usr/bin/env python3
"""
Core infrastructure for the Swarm orchestrator.
Includes event bus, git/worktree helpers, and Claude Code runner.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Set, Tuple, Union
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== Event Bus ====================

class EventType(Enum):
    LOG = "log"
    PLAN = "plan"
    ROUND = "round"
    TASK = "task"
    OUT = "out"
    APPROVAL = "approval"
    APPROVED = "approved"
    DONE = "done"

@dataclass
class Event:
    """Base event class"""
    type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict = field(default_factory=dict)

@dataclass
class LogEvent:
    """Log event"""
    task_id: str
    level: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_event(self) -> Event:
        return Event(
            type=EventType.LOG,
            timestamp=self.timestamp,
            data={
                "task_id": self.task_id,
                "level": self.level,
                "message": self.message
            }
        )

@dataclass
class TaskEvent:
    """Task status event"""
    task_id: str
    status: str
    attempts: int = 0
    cost: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_event(self) -> Event:
        return Event(
            type=EventType.TASK,
            timestamp=self.timestamp,
            data={
                "task_id": self.task_id,
                "status": self.status,
                "attempts": self.attempts,
                "cost": self.cost
            }
        )

@dataclass
class OutEvent:
    """Output event from Claude"""
    task_id: str
    kind: str  # text|tool|result|err|sys
    text: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_event(self) -> Event:
        return Event(
            type=EventType.OUT,
            timestamp=self.timestamp,
            data={
                "task_id": self.task_id,
                "kind": self.kind,
                "text": self.text
            }
        )

class EventBus:
    """Central event bus with history replay"""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._history: List[Event] = []
        self._max_history = 10000

    async def publish(self, event: Event):
        """Publish event to all subscribers and store in history"""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Notify all subscribers
        for queue in self._subscribers:
            try:
                await queue.put(event)
            except Exception as e:
                logger.warning(f"Failed to publish event to subscriber: {e}")

    def subscribe(self) -> AsyncGenerator[Event, None]:
        """Subscribe to events, returning history first then live events"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)

        async def event_generator():
            # First, replay history
            for event in self._history:
                yield event

            # Then yield live events
            try:
                while True:
                    event = await queue.get()
                    yield event
            except asyncio.CancelledError:
                pass
            finally:
                self._subscribers.discard(queue)

        return event_generator()

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber"""
        self._subscribers.discard(queue)

# ==================== Git/Worktree Helpers ====================

class GitError(Exception):
    """Git operation failed"""
    pass

class WorktreeManager:
    """Manage git worktrees for the orchestrator"""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path.resolve()
        self.orch_dir = self.repo_path / ".orch"
        self.orch_dir.mkdir(exist_ok=True)

        # Add .orch to .git/info/exclude if not present
        exclude_file = self.repo_path / ".git" / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        exclude_line = ".orch/\n"

        if exclude_file.exists():
            content = exclude_file.read_text()
            if exclude_line not in content:
                exclude_file.write_text(content + exclude_line)
        else:
            exclude_file.write_text(exclude_line)

    async def run_git(self, *args, cwd: Optional[Path] = None) -> str:
        """Run a git command and return stdout"""
        cmd = ["git"] + list(args)
        cwd = cwd or self.repo_path

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise GitError(f"Git command failed: {' '.join(cmd)}\nstderr: {stderr.decode()}")

            return stdout.decode().strip()
        except Exception as e:
            raise GitError(f"Failed to run git command: {e}")

    async def get_current_branch(self) -> str:
        """Get the current branch name"""
        return await self.run_git("rev-parse", "--abbrev-ref", "HEAD")

    async def get_current_commit(self) -> str:
        """Get the current commit hash"""
        return await self.run_git("rev-parse", "HEAD")

    async def create_worktree(self, branch_name: str, path: Path) -> Path:
        """Create a new worktree at the specified path"""
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Check if branch exists, if not create it from current HEAD
        if not await self.check_branch_exists(branch_name):
            await self.create_branch(branch_name)

        # Add worktree
        await self.run_git("worktree", "add", str(path), branch_name)
        return path

    async def remove_worktree(self, path: Path, force: bool = False):
        """Remove a worktree"""
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        await self.run_git(*args)

    async def list_worktrees(self) -> List[Dict]:
        """List all worktrees"""
        output = await self.run_git("worktree", "list", "--porcelain")
        worktrees = []
        current = {}

        for line in output.split('\n'):
            if line.startswith('worktree '):
                if current:
                    worktrees.append(current)
                current = {'path': Path(line.split(' ', 1)[1])}
            elif line.startswith('HEAD '):
                current['head'] = line.split(' ', 1)[1]
            elif line.startswith('branch '):
                current['branch'] = line.split(' ', 1)[1]
            elif line == '' and current:
                worktrees.append(current)
                current = {}

        if current:
            worktrees.append(current)

        return worktrees

    async def create_branch(self, branch_name: str, start_point: str = "HEAD") -> str:
        """Create a new branch"""
        await self.run_git("branch", branch_name, start_point)
        return branch_name

    async def check_branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists"""
        try:
            await self.run_git("rev-parse", "--verify", branch_name)
            return True
        except GitError:
            return False

    async def merge_branch(self, source_branch: str, target_branch: str = "HEAD",
                          no_ff: bool = False) -> bool:
        """Merge source_branch into target_branch"""
        # Switch to target branch
        await self.run_git("checkout", target_branch)

        # Perform merge
        merge_args = ["merge"]
        if no_ff:
            merge_args.append("--no-ff")
        merge_args.append(source_branch)

        try:
            await self.run_git(*merge_args)
            return True
        except GitError:
            return False

    async def abort_merge(self):
        """Abort the current merge"""
        await self.run_git("merge", "--abort")

    async def has_unmerged_files(self) -> bool:
        """Check if there are unmerged files"""
        try:
            output = await self.run_git("ls-files", "--unmerged")
            return bool(output.strip())
        except GitError:
            return False

# ==================== Claude Code Runner ====================

class ClaudeRunner:
    """Runs Claude Code processes with stream-json output"""

    def __init__(self, event_bus: EventBus, worktree_manager: WorktreeManager,
                 permission_mode: str = "acceptEdits", yolo: bool = False,
                 worker_model: Optional[str] = None):
        self.event_bus = event_bus
        self.worktree_manager = worktree_manager
        self.permission_mode = "dangerously-skip-permissions" if yolo else permission_mode
        self.worker_model = worker_model
        self.default_tools = ["Bash", "Edit", "Write", "Read", "Glob", "Grep"]

        # Increase asyncio stream limit to 64MB
        asyncio.StreamReader._limit = 64 * 1024 * 1024

    async def run_task(self, task_id: str, prompt: str, max_turns: int = 10,
                      session_id: Optional[str] = None,
                      append_system_prompt: Optional[str] = None,
                      worktree_path: Optional[Path] = None) -> AsyncGenerator[Dict, None]:
        """
        Run a Claude Code task and yield parsed events.

        Yields dictionaries with keys: type, task_id, data
        """
        # Build command
        cmd = ["claude", "-p", prompt]

        # Output format
        cmd.extend(["--output-format", "stream-json"])

        # Verbose
        cmd.append("--verbose")

        # Max turns
        cmd.extend(["--max-turns", str(max_turns)])

        # Permission mode
        cmd.extend(["--permission-mode", self.permission_mode])

        # Allowed tools
        tools_str = ",".join(self.default_tools)
        cmd.extend(["--allowed-tools", tools_str])

        # Worker model if specified
        if self.worker_model:
            cmd.extend(["--model", self.worker_model])

        # Session ID (first run) or resume
        if session_id:
            cmd.extend(["--resume", session_id])
        else:
            session_id = str(uuid.uuid4())
            cmd.extend(["--session-id", session_id])

        # Append system prompt
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])

        # Set working directory
        cwd = worktree_path or self.worktree_manager.repo_path

        logger.info(f"Starting Claude task {task_id} with session {session_id}")

        # Check if claude binary exists
        try:
            # Start process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL
            )
        except FileNotFoundError:
            logger.error("Claude binary not found. Please ensure 'claude' is installed and in PATH.")
            raise RuntimeError("Claude binary not found. Please ensure 'claude' is installed and in PATH.")
        except Exception as e:
            logger.error(f"Failed to start Claude process: {e}")
            raise

        # Create tasks to read stdout and stderr
        stdout_task = asyncio.create_task(self._process_stdout(process.stdout, task_id, session_id))
        stderr_task = asyncio.create_task(self._process_stderr(process.stderr, task_id))

        # Wait for process to complete
        try:
            await process.wait()
        except asyncio.CancelledError:
            # Kill the process if cancelled
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise

        # Wait for readers to finish
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        logger.info(f"Claude task {task_id} finished with return code {process.returncode}")

        # Yield final result event
        yield {
            "type": "result",
            "task_id": task_id,
            "data": {
                "returncode": process.returncode,
                "session_id": session_id
            }
        }

    async def _process_stdout(self, stdout: asyncio.StreamReader, task_id: str,
                             session_id: str):
        """Process stdout lines and yield events"""
        buffer = ""
        async for line in stdout:
            line = line.decode('utf-8', errors='replace')
            buffer += line

            # Process complete lines
            while '\n' in buffer:
                line_end = buffer.find('\n')
                line_content = buffer[:line_end]
                buffer = buffer[line_end + 1:]

                if line_content.strip():
                    try:
                        event_data = json.loads(line_content)
                        yield {
                            "type": "claude_output",
                            "task_id": task_id,
                            "data": {
                "session_id": session_id,
                                **event_data
                            }
                        }
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON line: {line_content[:100]}... Error: {e}")
                        # Yield as raw text event
                        yield {
                            "type": "raw_output",
                            "task_id": task_id,
                            "data": {
                                "session_id": session_id,
                                "text": line_content
                            }
                        }

    async def _process_stderr(self, stderr: asyncio.StreamReader, task_id: str):
        """Process stderr lines and yield events"""
        async for line in stderr:
            line = line.decode('utf-8', errors='replace')
            if line.strip():
                yield {
                    "type": "stderr",
                    "task_id": task_id,
                    "data": {
                        "text": line.strip()
                    }
                }

# ==================== Fake Claude Shim for Testing ====================

class FakeClaudeShim:
    """Fake Claude Code executable for testing"""

    def __init__(self, script_path: Path):
        self.script_path = script_path
        self.script_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_shim()

    def _write_shim(self):
        """Write the fake claude shim script"""
        script_content = '''#!/usr/bin/env python3
"""
Fake Claude Code shim for testing the swarm orchestrator.
Emits realistic stream-json output and honours --resume.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

def main():
    args = sys.argv[1:]

    # Parse arguments
    prompt = None
    output_format = None
    max_turns = 10
    session_id = None
    resume_id = None
    append_system_prompt = None
    permission_mode = "acceptEdits"

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-p" and i + 1 < len(args):
            prompt = args[i + 1]
            i += 2
        elif arg == "--output-format" and i + 1 < len(args):
            output_format = args[i + 1]
            i += 2
        elif arg == "--max-turns" and i + 1 < len(args):
            try:
                max_turns = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg == "--session-id" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif arg == "--resume" and i + 1 < len(args):
            resume_id = args[i + 1]
            i += 2
        elif arg == "--append-system-prompt" and i + 1 < len(args):
            append_system_prompt = args[i + 1]
            i += 2
        elif arg == "--permission-mode" and i + 1 < len(args):
            permission_mode = args[i + 1]
            i += 2
        else:
            i += 1

    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)

    # Generate session ID if not provided
    if not session_id and not resume_id:
        session_id = str(uuid.uuid4())
    elif resume_id:
        session_id = resume_id
    elif not session_id:
        session_id = str(uuid.uuid4())

    # Emit stream-json events
    if output_format == "stream-json":
        # Hook started events
        hook_uuid1 = str(uuid.uuid4())
        hook_uuid2 = str(uuid.uuid4())
        print(json.dumps({
            "type": "system",
            "subtype": "hook_started",
            "hook_id": hook_uuid1,
            "hook_name": "SessionStart:startup",
            "hook_event": "SessionStart",
            "uuid": hook_uuid1,
            "session_id": session_id
        }), flush=True)

        print(json.dumps({
            "type": "system",
            "subtype": "hook_started",
            "hook_id": hook_uuid2,
            "hook_name": "SessionStart:startup",
            "hook_event": "SessionStart",
            "uuid": hook_uuid2,
            "session_id": session_id
        }), flush=True)

        # Hook response events
        print(json.dumps({
            "type": "system",
            "subtype": "hook_response",
            "hook_id": hook_uuid1,
            "hook_name": "SessionStart:startup",
            "hook_event": "SessionStart",
            "output": "",
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "outcome": "success",
            "uuid": str(uuid.uuid4()),
            "session_id": session_id
        }), flush=True)

        print(json.dumps({
            "type": "system",
            "subtype": "hook_response",
            "hook_id": hook_uuid2,
            "hook_name": "SessionStart:startup",
            "hook_event": "SessionStart",
            "output": "",
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "outcome": "success",
            "uuid": str(uuid.uuid4()),
            "session_id": session_id
        }), flush=True)

        # Initialize event
        init_data = {
            "type": "system",
            "subtype": "init",
            "cwd": os.getcwd(),
            "session_id": session_id,
            "tools": ["Task", "Bash", "CronCreate", "CronDelete", "CronList", "DesignSync", "Edit", "EnterWorktree", "ExitWorktree", "ListAgents", "Monitor", "NotebookEdit", "PushNotification", "Read", "ReportFindings", "ScheduleWakeup", "SendMessage", "Skill", "TaskStop", "WebFetch", "WebSearch", "Workflow", "Write"],
            "mcp_servers": [],
            "model": "auto[1m]",
            "permissionMode": permission_mode,
            "slash_commands": [],
            "terminal_slash_commands": [],
            "apiKeySource": "ANTHROPIC_API_KEY",
            "claude_code_version": "2.1.292",
            "output_style": "default",
            "agents": [],
            "skills": [],
            "plugins": [],
            "capabilities": [],
            "analytics_disabled": False,
            "product_feedback_disabled": False,
            "uuid": str(uuid.uuid4()),
            "memory_paths": {"auto": "/tmp/fake-claude-memory/"},
            "messaging_socket_path": "/tmp/fake-claude.sock",
            "fast_mode_state": "off",
            "fast_mode_disabled_reason": "sdk_opt_in_required",
            "per_turn_effort_active": False,
            "view_mode": "default"
        }
        print(json.dumps(init_data), flush=True)

        # Thinking tokens
        print(json.dumps({
            "type": "system",
            "subtype": "thinking_tokens",
            "estimated_tokens": 5,
            "estimated_tokens_delta": 5,
            "session_id": session_id,
            "uuid": str(uuid.uuid4())
        }), flush=True)

        # Assistant message (thinking)
        thinking_uuid = str(uuid.uuid4())
        print(json.dumps({
            "type": "assistant",
            "message": {
                "id": f"msg_{int(time.time())}",
                "type": "message",
                "role": "assistant",
                "model": "auto",
                "content": [{"type": "thinking", "thinking": prompt}],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "context_management": None
            },
            "parent_tool_use_id": None,
            "session_id": session_id,
            "uuid": thinking_uuid,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "thinking_duration_ms": 5
        }), flush=True)

        # Assistant message (text response)
        text_uuid = str(uuid.uuid4())
        response_text = f"Fake response to: {prompt[:50]}..."
        print(json.dumps({
            "type": "assistant",
            "message": {
                "id": f"msg_{int(time.time()) + 1}",
                "type": "message",
                "role": "assistant",
                "model": "auto",
                "content": [{"type": "text", "text": response_text}],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": len(response_text)},
                "context_management": None
            },
            "parent_tool_use_id": None,
            "session_id": session_id,
            "uuid": text_uuid,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "thinking_duration_ms": 0
        }), flush=True)

        # Result event
        result_uuid = str(uuid.uuid4())
        print(json.dumps({
            "type": "result",
            "duration_api_ms": 100,
            "stop_reason": "end_turn",
            "session_id": session_id,
            "total_cost_usd": 0.001,
            "usage": {
                "input_tokens": 20,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": len(response_text),
                "output_tokens_details": {"thinking_tokens": 0},
                "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                "service_tier": "standard",
                "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
                "inference_geo": "",
                "iterations": [],
                "speed": "standard",
                "fallback_credit": None,
                "modelUsage": {"auto[1m]": {
                    "inputTokens": 20,
                    "outputTokens": len(response_text),
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "webSearchRequests": 0,
                    "costUSD": 0.001,
                    "contextWindow": 1000000,
                    "maxOutputTokens": 32000,
                    "thinkingTokens": 0,
                    "canonicalModel": "auto[1m]",
                    "provider": "firstParty",
                    "costBasis": "unknown"
                }}
            },
            "permission_denials": [],
            "terminal_reason": "completed",
            "fast_mode_state": "off",
            "fast_mode_disabled_reason": "sdk_opt_in_required",
            "subagent_stats": {"spawned": 0, "requested": {"background": 0, "foreground": 0, "unset": 0}, "started_in_background": 0, "max_depth": 0, "spawned_by_subagents": 0, "completed": 0, "failed": 0, "killed": {"parent": 0, "user": 0, "system": 0}, "refused": {"depth_limit": 0, "concurrency_limit": 0, "budget": 0}, "by_type": {}},
            "safety_stops": 0,
            "is_error": False,
            "num_turns": 1,
            "subtype": "success",
            "api_error_status": None,
            "result": response_text,
            "ttft_ms": 100,
            "type": "result",
            "duration_ms": 200,
            "uuid": result_uuid,
            "ttft_stream_ms": 90,
            "time_to_request_ms": 10,
            "first_content_frame_ms": 95,
            "queued_turn_count": 0,
            "result_index": 0
        }), flush=True)

    else:
        # Text output (non-stream)
        print(f"Fake response to: {prompt[:50]}...")

if __name__ == "__main__":
    main()
'''
        self.script_path.write_text(script_content)
        self.script_path.chmod(0o755)  # Make executable

if __name__ == "__main__":
    # Test the core components
    import asyncio

    async def test_event_bus():
        bus = EventBus()

        # Subscribe
        queue = asyncio.Queue()
        bus._subscribers.add(queue)

        # Publish event
        log_event = LogEvent(
            task_id="test",
            level="info",
            message="Test message"
        )
        await bus.publish(log_event.to_event())

        # Get event
        event = await queue.get()
        print(f"Received event: {event}")

        bus._subscribers.discard(queue)

    async def test_worktree_manager(tmp_path):
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()
        (repo_path / ".git" / "info").mkdir()

        manager = WorktreeManager(repo_path)

        # Test creating worktree
        worktree_path = repo_path / "worktree"
        await manager.create_worktree("test-branch", worktree_path)
        print(f"Created worktree at {worktree_path}")

        # Cleanup
        await manager.remove_worktree(worktree_path)

    # Run tests
    asyncio.run(test_event_bus())
    print("Core tests passed!")