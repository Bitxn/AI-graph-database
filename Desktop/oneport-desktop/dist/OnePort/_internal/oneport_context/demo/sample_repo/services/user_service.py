"""User accounts and lookup — resolves teammates by email for task assignment."""
from models.user import User


class UserService:
    def __init__(self, repo):
        self._repo = repo

    def get_by_email(self, email):
        user = self._repo.get_user_by_email(email)
        if user is None:
            user = self._repo.save_user(User(email=email, name=email.split("@")[0]))
        return user
