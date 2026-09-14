"""Business rules for tasks — creating, assigning, and completing work items."""
from models.task import Task, TaskStatus


class TaskService:
    def __init__(self, repo):
        self._repo = repo

    def create(self, project_id, title, assignee=None):
        task = Task(project_id=project_id, title=title, assignee=assignee, status=TaskStatus.OPEN)
        return self._repo.save_task(task)

    def complete(self, task_id):
        task = self._repo.get_task(task_id)
        if task is None:
            raise ValueError(f"no such task: {task_id}")
        task.status = TaskStatus.DONE
        return self._repo.save_task(task)
