"""Task and TaskStatus data models — the core work-item entity."""
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


@dataclass
class Task:
    project_id: str
    title: str
    status: TaskStatus = TaskStatus.OPEN
    assignee: object = None
    id: str | None = None
    tags: list = field(default_factory=list)
