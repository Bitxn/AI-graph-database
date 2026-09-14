"""oneport-apiwatch — self-hosted API health monitoring with AI-explained failures.

The whole point: this runs as a *stateless check* on the customer's own machine,
cron, or CI (e.g. a scheduled GitHub Action). Nothing runs on Oneport's servers.
State (status history for trend detection) lives in a local file the customer owns.
"""

__version__ = "0.2.0"
