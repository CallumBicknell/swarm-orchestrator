# Swarm Orchestrator

A distributed system for running parallel Claude Code agents with a live TUI and web GUI.

## Overview

Swarm is an orchestrator that manages multiple Claude Code agents working in parallel to achieve a specified goal. It features:

- **Event-driven architecture** with central event bus and history replay
- **Git worktree management** for isolated task execution
- **Process lifecycle management** for Claude Code agents
- **Real-time TUI** (terminal user interface) using the textual library
- **Web GUI** with Server-Sent Events (SSE) for live updates
- **LLM-based planning** to break down goals into task DAGs
- **Approval workflow** for task validation
- **Fault tolerance** with timeout handling and cleanup

## Components

### Core Infrastructure (`swarm/core.py`)
- `EventBus`: Publish/subscribe with history replay
- `WorktreeManager`: Git worktree operations
- `ClaudeRunner`: Executes Claude Code tasks with stream-json parsing
- `FakeClaudeShim`: Testing utility that mimics Claude Code output

### Planning Logic (`swarm/planner.py`)
- `LLMAdapter`: Interface for Anthropic and OpenAI APIs
- `TaskPlanner`: Converts goals into task DAGs
- `DAGScheduler`: Executes tasks based on dependencies and parallel limits
- `Task`: Data class representing a work item

### Orchestration (`swarm/orchestrator.py`)
- `SwarmOrchestrator`: Main coordinator
- `OrchestratorRun`: Tracks a single orchestration run
- Lifecycle management (start, execute, cancel)

### Git Utilities (`swarm/git_utils.py`)
- `GitManager`: Low-level git operations
- `WorktreeManager`: High-level worktree management
- Merge conflict detection and resolution

### Web Server (`swarm/web_server.py`)
- aiohttp server with SSE endpoint (`/events`)
- Approval endpoint (`/api/approve` requires `X-Swarm: 1` header)
- Health check endpoint (`/health`)

### Web GUI (`swarm/static/`)
- `index.html`: Main interface with task cards, logs, and controls
- Embedded JavaScript for real-time updates via SSE

### TUI (`swarm/tui_app.py` and `swarm/tui.css`)
- Textual-based terminal interface
- Task table and tabbed log views
- Keyboard controls (q=quit, 0=all agents, o=orchestrator, enter=select task)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd swarm-orchestrator

# Install Python dependencies
pip install aiohttp textual

# Ensure you have claude CLI installed and available in PATH
# https://docs.anthropic.com/claude/docs/quickstart
```

## Usage

### As a Library

```python
import asyncio
from pathlib import Path
from swarm.orchestrator import SwarmOrchestrator

async def main():
    # Initialize orchestrator
    orchestrator = SwarmOrchestrator(
        repo_path=Path("."),  # Current directory as git repo
        llm_provider="anthropic",
        use_fake_claude=True  # Set to False for real Claude CLI
    )
    
    # Start a run
    run_id = await orchestrator.start_run("Create a simple REST API for user management")
    print(f"Started run: {run_id}")
    
    # Execute the run
    success = await orchestrator.execute_run()
    print(f"Run completed successfully: {success}")
    
    # Clean up if still running
    if orchestrator.current_run and orchestrator.current_run.status == "running":
        await orchestrator.cancel_run()

if __name__ == "__main__":
    asyncio.run(main())
```

### Running the Web GUI

1. Start the orchestrator in one terminal:
```bash
python -c "
import asyncio
from swarm.orchestrator import SwarmOrchestrator
asyncio.run(SwarmOrchestrator(Path('.')).start_run('Test goal'))
"
```

2. Open `swarm/static/index.html` in a web browser to view the GUI.

### Running the TUI

```bash
python -m swarm.tui_app
```

## Architecture

### Event Bus
Central pub/sub system where:
- Publishers emit events (log, task status, approval requests, etc.)
- Subscribers receive live events plus full history on new subscriptions
- Used for communication between orchestrator, planners, runners, TUI, and web GUI

### Git Worktree Management
- Each task runs in its own git worktree
- Worktrees are created from the current integration branch
- Tasks commit to their worktrees; orchestrator merges to integration branch
- Automatic conflict detection and resolution

### Task Lifecycle
1. **Planning**: LLM breaks goal into tasks with dependencies
2. **Scheduling**: Tasks run when dependencies are satisfied
3. **Execution**: ClaudeRunner executes task in isolated worktree
4. **Review**: Completed tasks await approval
5. **Merging**: Approved tasks are merged to integration branch
6. **Completion**: Goal checked; follow-up rounds planned if needed

## Configuration

### Environment Variables
- `ANTHROPIC_API_KEY`: API key for Anthropic (if using Claude)
- `OPENAI_API_KEY`: API key for OpenAI (if using GPT models)

### Orchestrator Parameters
- `repo_path`: Path to git repository
- `llm_provider`: "anthropic" or "openai"
- `model`: Specific model name (defaults vary by provider)
- `api_key`: API key for LLM provider
- `max_parallel`: Maximum concurrent tasks (default: 3)
- `use_fake_claude`: Use fake Claude for testing (default: False)

## Development

### Running Tests
```bash
# Core and planner tests (no external dependencies)
python -m unittest discover tests

# Web server tests (requires aiohttp)
# TUI tests (requires textual)
```

### Project Structure
```
swarm/
├── core.py          # Event bus, worktree helpers, Claude runner
├── planner.py       # LLM adaptation, task planning, DAG scheduling
├── git_utils.py     # Git operations and worktree management
├── orchestrator.py  # Main orchestrator coordination
├── web_server.py    # aiohttp server with SSE and approval endpoints
├── tui_app.py       # Textual TUI application
├── tui.css          # TUI styling
└── __init__.py

tests/
├── test_core.py
├── test_planner.py
├── test_web.py
└── test_tui.py

swarm/static/
├── index.html       # Web GUI
└── app.js           # JavaScript placeholder
```

## Design Decisions

### Why Event Bus with History Replay?
- Enables late-joining subscribers (like TUI or web GUI) to get full context
- Provides audit trail for debugging and replay
- Decouples components for better scalability and maintainability

### Why Git Worktrees?
- Provides isolated execution environments for each task
- Leverages git's built-in conflict detection
- Allows tasks to work on different branches simultaneously
- Clean cleanup via worktree removal

### Why Separate TUI and Web GUI?
- TUI for developers who prefer terminal workflows
- Web GUI for richer visualization and remote access
- Both consume the same event stream for consistency

### Why Stream-JSON for Claude Code?
- Enables real-time processing of agent output
- Allows interception and transformation of events
- Provides structured data for UI components

## Limitations and Future Work

### Current Limitations
- Requires claude CLI to be installed and in PATH
- LLM planning uses mock responses when API keys aren't available
- Web GUI authentication is minimal (X-Swarm header only)
- No persistence of orchestrator state across restarts

### Planned Enhancements
- Persistent storage for orchestrator state
- Enhanced authentication and authorization
- More sophisticated conflict resolution strategies
- Plugin system for custom task types
- Performance optimizations for large-scale orchestration

## License

MIT License - see LICENSE file for details.

## Acknowledgments

Built with:
- [Claude Code](https://claude.ai/code) for agent execution
- [textual](https://textual.textualize.io/) for TUI
- [aiohttp](https://docs.aiohttp.org/) for web server
- [Anthropic](https://www.anthropic.com/) and [OpenAI](https://openai.com/) APIs for planning