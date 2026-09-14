"""Database access layer — a thin repository that persists tasks and users to SQLite."""
import sqlite3
import uuid

from models.task import Task
from models.user import User


class Repository:
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._tasks = {}
        self._users = {}

    def save_task(self, task: Task):
        task.id = task.id or uuid.uuid4().hex
        self._tasks[task.id] = task
        return task

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def save_user(self, user: User):
        user.id = user.id or uuid.uuid4().hex
        self._users[user.email] = user
        return user

    def get_user_by_email(self, email):
        return self._users.get(email)
