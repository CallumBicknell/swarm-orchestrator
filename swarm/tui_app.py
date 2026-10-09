"""
TUI application for the Swarm orchestrator.
Using the textual library to create a terminal-based user interface.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Set
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, DataTable, Label, Static, TabbedContent, TabPane, Button
from textual.widgets import DataTable
from textual.reactive import reactive
from textual.timer import Timer
from textual.binding import Binding

try:
    # When used as part of the swarm package
    from .core import EventBus, LogEvent, EventType
except ImportError:
    # When used directly or in tests
    from core import EventBus, LogEvent, EventType

try:
    # When used as part of the swarm package
    from .planner import Task
except ImportError:
    # When used directly or in tests
    from planner import Task


class SwarmTUI(App):
    """A Textual application for the Swarm orchestrator."""

    CSS_PATH = "tui.css"
    TITLE = "Swarm Orchestrator"
    SUB_TITLE = "Monitoring and controlling agent swarms"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("0", "show_all_agents", "All Agents"),
        Binding("o", "show_orchestrator", "Orchestrator"),
        Binding("enter", "show_selected_task", "Show Task"),
    ]

    def __init__(self, event_bus: EventBus):
        super().__init__()
        self.event_bus = event_bus
        self.tasks: Dict[str, Task] = {}
        self.logs: List[LogEvent] = []
        self.selected_task_id: Optional[str] = None
        # Subscribe to events
        self.event_bus._subscribers.add(self._event_queue)
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield Container(
            Horizontal(
                Vertical(
                    Static("Tasks", classes="title"),
                    DataTable(id="task-table"),
                    id="tasks-panel",
                ),
                Vertical(
                    TabbedContent(
                        TabPane("All Agents", id="all-agents-tab"),
                        TabPane("Orchestrator", id="orchestrator-tab"),
                        id="tabs",
                    ),
                    id="tabs-panel",
                ),
                id="main-container",
            ),
            Vertical(
                Static("Logs", classes="title"),
                Label(id="log-display"),
                id="logs-panel",
            ),
            id="app-grid",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.setup_task_table()
        self.set_interval(1 / 4, self.update_from_queue)  # Update 4 times per second

    def setup_task_table(self) -> None:
        """Initialize the task table."""
        table = self.query_one("#task-table", DataTable)
        table.add_columns("ID", "Status", "Tries", "Cost")
        table.cursor_type = "row"
        table.zebra_stripes = True

    async def update_from_queue(self) -> None:
        """Update the UI from the event queue."""
        try:
            while True:
                event = self._event_queue.get_nowait()
                await self.process_event(event)
        except asyncio.QueueEmpty:
            pass

    async def process_event(self, event: LogEvent) -> None:
        """Process an event from the event bus."""
        self.logs.append(event)
        # Keep only the last 1000 logs
        if len(self.logs) > 1000:
            self.logs = self.logs[-1000:]

        # Update task state if it's a task event
        if event.type == EventType.TASK and "task_id" in event.data:
            task_id = event.data["task_id"]
            if task_id not in self.tasks:
                # Create a basic task object from the event data
                self.tasks[task_id] = Task(
                    id=task_id,
                    title=event.data.get("title", f"Task {task_id}"),
                    prompt=event.data.get("prompt", ""),
                    dependencies=event.data.get("dependencies", []),
                )
            # Update the task with new data
            for key, value in event.data.items():
                if hasattr(self.tasks[task_id], key):
                    setattr(self.tasks[task_id], key, value)

        # Update the UI
        self.update_task_table()
        self.update_logs()

    def update_task_table(self) -> None:
        """Update the task table with current task data."""
        table = self.query_one("#task-table", DataTable)
        table.clear()
        # Sort tasks: orchestrator first, then by status
        sorted_tasks = sorted(
            self.tasks.values(),
            key=lambda t: (
                t.id != "orchestrator",  # orchestrator first
                t.status,  # then by status
            ),
        )
        for task in sorted_tasks:
            table.add_row(
                task.id,
                task.status.title() if task.status else "Pending",
                str(getattr(task, "attempts", 0)),
                f"${getattr(task, 'actual_cost', 0):.3f}",
                key=task.id,
            )

    def update_logs(self) -> None:
        """Update the log display."""
        # Show the last 20 logs
        recent_logs = self.logs[-20:] if len(self.logs) >= 20 else self.logs
        log_text = "\n".join(
            f"[{log.timestamp.strftime('%H:%M:%S')}] "
            f"[{log.data.get('task_id', '?')}] "
            f"{log.data.get('message', '')}"
            for log in recent_logs
        )
        self.query_one("#log-display", Label).update(log_text)

    def action_show_all_agents(self) -> None:
        """Switch to the All Agents tab."""
        self.query_one("#tabs").active = "all-agents-tab"

    def action_show_orchestrator(self) -> None:
        """Switch to the Orchestrator tab."""
        self.query_one("#tabs").active = "orchestrator-tab"

    def action_show_selected_task(self) -> None:
        """Show the selected task in a tab."""
        if self.selected_task_id:
            tab_id = f"task-{self.selected_task_id}-tab"
            # Check if the tab already exists
            if not self.query_one(f"#{tab_id}", TabPane, default=None):
                # Create a new tab for the task
                tab_content = Vertical(
                    Static(f"Task: {self.selected_task_id}", classes="title"),
                    Label(id=f"task-log-{self.selected_task_id}"),
                    Button("Back to Tabs", id="back-to-tabs", variant="primary"),
                )
                self.query_one("#tabs").add_pane(
                    TabPane(f"Task {self.selected_task_id}", tab_content, id=tab_id)
                )
            self.query_one("#tabs").active = tab_id

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """When a row is selected in the task table."""
        task_id = str(event.row_key.value)
        self.selected_task_id = task_id
        # Update the task-specific log
        task_logs = [
            log
            for log in self.logs
            if log.data.get("task_id") == task_id
        ]
        log_text = "\n".join(
            f"[{log.timestamp.strftime('%H:%M:%S')}] {log.data.get('message', '')}"
            for log in task_logs[-20:]
        )
        self.query_one(
            f"#task-log-{self.selected_task_id}", Label
        ).update(log_text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "back-to-tabs":
            self.query_one("#tabs").active = "all-agents-tab"


def run_tui(event_bus: EventBus) -> None:
    """Run the TUI application."""
    app = SwarmTUI(event_bus)
    app.run()


if __name__ == "__main__":
    # For testing purposes
    event_bus = EventBus()
    run_tui(event_bus)