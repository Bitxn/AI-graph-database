"""User data model — a teammate who can own tasks."""
from dataclasses import dataclass


@dataclass
class User:
    email: str
    name: str
    id: str | None = None
