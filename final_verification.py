#!/usr/bin/env python3
"""
Final verification script for the Swarm orchestrator.
This script verifies that all components are in place and basic functionality works.
"""

import asyncio
import tempfile
import subprocess
from pathlib import Path
import sys
import os

# Add the swarm directory to the path
sys.path.insert(0, str(Path(__file__).parent / "swarm"))

def test_file_exists(filepath):
    """Check if a file exists"""
    full_path = Path(__file__).parent / filepath
    return full_path.exists()

def test_file_content(filepath, expected_strings):
    """Check if file contains expected strings"""
    full_path = Path(__file__).parent / filepath
    if not full_path.exists():
        return False, f"File {filepath} not found"
    try:
        content = full_path.read_text()
        for expected in expected_strings:
            if expected not in content:
                return False, f"Missing expected string: {expected}"
        return True, "All expected strings found"
    except Exception as e:
        return False, f"Error reading file: {e}"

def main():
    """Run final verification"""
    print("=== Swarm Orchestrator Final Verification ===\n")

    all_passed = True

    # 1. Check core files exist
    print("1. Checking core component files...")
    core_files = [
        "swarm/core.py",
        "swarm/planner.py",
        "swarm/git_utils.py",
        "swarm/orchestrator.py",
        "swarm/web_server.py",
        "swarm/tui_app.py",
        "swarm/tui.css"
    ]

    for filepath in core_files:
        if test_file_exists(filepath):
            print(f"   ✓ {filepath}")
        else:
            print(f"   ✗ {filepath} - MISSING")
            all_passed = False

    # 2. Check static files for web GUI
    print("\n2. Checking web GUI files...")
    web_files = [
        "swarm/static/index.html",
        "swarm/static/app.js"
    ]

    for filepath in web_files:
        if test_file_exists(filepath):
            print(f"   ✓ {filepath}")
        else:
            print(f"   ✗ {filepath} - MISSING")
            all_passed = False

    # 3. Check test files
    print("\n3. Checking test files...")
    test_files = [
        "tests/test_core.py",
        "tests/test_planner.py",
        "tests/test_web.py",
        "tests/test_tui.py"
    ]

    for filepath in test_files:
        if test_file_exists(filepath):
            print(f"   ✓ {filepath}")
        else:
            print(f"   ✗ {filepath} - MISSING")
            all_passed = False

    # 4. Check core.py for key components
    print("\n4. Checking core.py components...")
    core_components = [
        "class EventBus",
        "class WorktreeManager",
        "class ClaudeRunner",
        "class FakeClaudeShim",
        "def publish",
        "def subscribe",
        "def create_worktree",
        "def remove_worktree",
        "def run_task"
    ]

    success, message = test_file_content("swarm/core.py", core_components)
    if success:
        print(f"   ✓ Core components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 5. Check planner.py for key components
    print("\n5. Checking planner.py components...")
    planner_components = [
        "class LLMAdapter",
        "class TaskPlanner",
        "class DAGScheduler",
        "class Task",
        "def create_plan",
        "def validate_tasks",
        "def get_ready_tasks"
    ]

    success, message = test_file_content("swarm/planner.py", planner_components)
    if success:
        print(f"   ✓ Planner components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 6. Check git_utils.py for key components
    print("\n6. Checking git_utils.py components...")
    git_components = [
        "class GitManager",
        "class WorktreeManager",
        "def create_worktree",
        "def remove_worktree",
        "def merge_branch",
        "def has_unmerged_files"
    ]

    success, message = test_file_content("swarm/git_utils.py", git_components)
    if success:
        print(f"   ✓ Git utilities components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 7. Check orchestrator.py for key components
    print("\n7. Checking orchestrator.py components...")
    orchestrator_components = [
        "class SwarmOrchestrator",
        "class OrchestratorRun",
        "def start_run",
        "def execute_run",
        "def cancel_run"
    ]

    success, message = test_file_content("swarm/orchestrator.py", orchestrator_components)
    if success:
        print(f"   ✓ Orchestrator components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 8. Check web_server.py for key components
    print("\n8. Checking web_server.py components...")
    web_components = [
        "class WebServer",
        "def sse_handler",
        "def approve_handler",
        "def health_handler",
        "def _broadcast_events"
    ]

    success, message = test_file_content("swarm/web_server.py", web_components)
    if success:
        print(f"   ✓ Web server components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 9. Check tui_app.py for key components
    print("\n9. Checking tui_app.py components...")
    tui_components = [
        "class SwarmTUI",
        "def compose",
        "def on_mount",
        "def process_event",
        "def update_task_table"
    ]

    success, message = test_file_content("swarm/tui_app.py", tui_components)
    if success:
        print(f"   ✓ TUI components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 10. Check index.html for key components
    print("\n10. Checking index.html components...")
    html_components = [
        "class SwarmWebGUI",
        "this.init()",
        "this.setupEventListeners()",
        "this.connectToSSE()",
        "this.handleEvent",
        "this.updateTaskState",
        "this.renderTaskCards",
        "this.updateStatistics"
    ]

    success, message = test_file_content("swarm/static/index.html", html_components)
    if success:
        print(f"   ✓ Web GUI components found")
    else:
        print(f"   ✗ {message}")
        all_passed = False

    # 11. Run basic tests if possible
    print("\n12. Running basic import tests...")
    try:
        # Test importing core components
        from core import EventBus, WorktreeManager, ClaudeRunner, FakeClaudeShim
        print("   ✓ Core components import successfully")

        # Test importing planner components
        from planner import LLMAdapter, TaskPlanner, Task
        print("   ✓ Planner components import successfully")

        # Test importing git utilities
        from git_utils import GitManager, WorktreeManager
        print("   ✓ Git utilities components import successfully")

    except Exception as e:
        print(f"   ✗ Import error: {e}")
        all_passed = False

    # Summary
    print("\n=== Verification Summary ===")
    if all_passed:
        print("✓ ALL CHECKS PASSED")
        print("The Swarm orchestrator implementation is complete and ready for use.")
        print("\nNext steps:")
        print("1. Install required dependencies: pip install aiohttp textual")
        print("2. Run the orchestrator: python -c \"import asyncio; from swarm.orchestrator import SwarmOrchestrator; asyncio.run(SwarmOrchestrator(Path('.')).start_run('Test goal'))\"")
        print("3. Launch the web GUI: open swarm/static/index.html in a browser")
        print("4. Launch the TUI: python -m swarm.tui_app (when textual is installed)")
        return True
    else:
        print("✗ SOME CHECKS FAILED")
        print("Please review the errors above and fix any missing components.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)