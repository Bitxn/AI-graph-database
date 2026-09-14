"""Application entry point — wires HTTP routes to the service layer and starts the server."""
from api.routes import build_app
from db.repository import Repository


def main() -> None:
    repo = Repository("taskflow.db")
    app = build_app(repo)
    app.run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
