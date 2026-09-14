"""HTTP routes for tasks, projects, and users — the API surface of TaskFlow."""
from services.task_service import TaskService
from services.user_service import UserService


def build_app(repo):
    tasks = TaskService(repo)
    users = UserService(repo)

    def create_task(project_id, title, assignee_email):
        user = users.get_by_email(assignee_email)
        return tasks.create(project_id, title, assignee=user)

    def complete_task(task_id):
        return tasks.complete(task_id)

    return _Router({
        "POST /tasks": create_task,
        "POST /tasks/complete": complete_task,
    })


class _Router:
    def __init__(self, routes):
        self._routes = routes

    def run(self, host, port):
        print(f"TaskFlow listening on {host}:{port}")
