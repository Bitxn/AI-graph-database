"""Platform integrations for fetching PR/MR diffs."""

from oneport.integrations.github import GitHubIntegration
from oneport.integrations.gitlab import GitLabIntegration
from oneport.integrations.bitbucket import BitbucketIntegration
from oneport.integrations.local_diff import LocalDiffIntegration

__all__ = [
    "GitHubIntegration",
    "GitLabIntegration",
    "BitbucketIntegration",
    "LocalDiffIntegration",
]
