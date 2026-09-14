"""
Provider-specific remediation.

For every REAL secret, "rotate it" is useless advice - engineers need the exact
place to revoke *this* provider's credential and the order of operations. This
maps each detector id to concrete revoke -> rotate -> purge-history steps.

Remediation is deterministic (keyed off the detector), not model-generated, so
the advice is stable and can't hallucinate a dashboard URL.
"""

from __future__ import annotations

from oneport_secrets.result import Finding

# The last step is universal: rotating isn't enough while the key still sits in
# git history. Appended to every provider's steps.
_PURGE_HISTORY = (
    "Purge it from git history (git filter-repo --invert-paths, or the BFG), "
    "then force-push and have collaborators re-clone."
)

_STEPS: dict[str, list[str]] = {
    "aws-access-key-id": [
        "Deactivate then delete the key in IAM -> Users -> Security credentials "
        "(console.aws.amazon.com/iam), or: aws iam delete-access-key --access-key-id <ID>.",
        "Create a replacement key and update your secret store (never commit it).",
        "Check CloudTrail for use of the leaked key by an unexpected principal.",
    ],
    "aws-secret-access-key": [
        "Treat the paired AWS access key as compromised - deactivate & delete it in IAM.",
        "Rotate to a new key pair stored in a secrets manager, not the repo.",
        "Audit CloudTrail for unauthorized calls.",
    ],
    "gcp-api-key": [
        "Regenerate or delete the key in Google Cloud Console -> APIs & Services -> "
        "Credentials (console.cloud.google.com/apis/credentials).",
        "Add application/API restrictions to the replacement key.",
    ],
    "gcp-service-account": [
        "Disable and delete the leaked service-account key in IAM & Admin -> Service Accounts.",
        "Create a new key (or better, use workload identity federation instead of static keys).",
        "Review Cloud Audit Logs for use of the service account.",
    ],
    "stripe-secret-key": [
        "Roll the key immediately at dashboard.stripe.com/apikeys (Developers -> API keys -> Roll).",
        "Update the new key in your server secret store; restricted keys are preferable to full ones.",
        "Review the Stripe dashboard logs for unexpected API activity.",
    ],
    "github-pat": [
        "Revoke the token at github.com/settings/tokens.",
        "Regenerate with the minimum scopes, or switch to a fine-grained token / GitHub App.",
        "Check the account's security log for actions taken with the token.",
    ],
    "github-fine-grained": [
        "Revoke the fine-grained PAT at github.com/settings/tokens.",
        "Recreate it scoped to only the repositories and permissions actually needed.",
    ],
    "github-oauth": [
        "Revoke the OAuth/app token at github.com/settings/applications (or via the app).",
        "Rotate the OAuth app client secret if the app itself is compromised.",
    ],
    "gitlab-pat": [
        "Revoke the token at gitlab.com -> Preferences -> Access Tokens.",
        "Recreate with the least scope required and store it in CI/CD variables, not the repo.",
    ],
    "slack-token": [
        "Revoke the token at api.slack.com/apps (or Workspace admin -> Manage apps).",
        "Reinstall the app to mint a fresh token and store it server-side.",
    ],
    "slack-webhook": [
        "Delete the incoming webhook in the Slack app config - the URL itself is the secret.",
        "Create a new webhook URL and update the caller.",
    ],
    "twilio-api-key": [
        "Delete the API key in the Twilio Console -> Account -> API keys & tokens.",
        "Create a replacement Standard/Restricted key and update your config.",
    ],
    "sendgrid-key": [
        "Delete the key at app.sendgrid.com/settings/api_keys.",
        "Create a new key with only the scopes you need.",
    ],
    "mailgun-key": [
        "Rotate the key in the Mailgun dashboard -> Settings -> API Keys.",
    ],
    "openai-key": [
        "Revoke the key at platform.openai.com/api-keys.",
        "Create a new key and set a usage limit; store it in an env/secret manager.",
        "Review usage at platform.openai.com/usage for unexpected spend.",
    ],
    "anthropic-key": [
        "Revoke the key at console.anthropic.com -> Settings -> API Keys.",
        "Create a replacement key and store it in a secret manager.",
    ],
    "npm-token": [
        "Revoke the token at npmjs.com -> Access Tokens.",
        "Create a granular/automation token scoped to CI and store it as a CI secret.",
    ],
    "pypi-token": [
        "Delete the token at pypi.org/manage/account/token/.",
        "Create a new project-scoped token for uploads and store it in CI secrets.",
    ],
    "stripe-test-key": [
        "Test-mode keys can't move real money, but still rotate it at dashboard.stripe.com/test/apikeys.",
    ],
    "jwt": [
        "If this JWT is a long-lived credential, revoke/rotate the signing key so all "
        "tokens signed with it are invalidated.",
        "Prefer short TTLs and never commit tokens.",
    ],
    "private-key": [
        "Treat the private key as compromised. Revoke any certificate issued for it and "
        "rotate the key pair.",
        "For SSH keys, remove the public key from all authorized_keys / provider settings and "
        "generate a fresh pair.",
    ],
    "db-connection-uri": [
        "Rotate the database user's password immediately and update the connection string in "
        "your secret store.",
        "Restrict the DB user's network access and privileges; assume the data was reachable.",
    ],
    "generic-secret-assignment": [
        "Rotate the underlying credential at its provider and move it to an environment "
        "variable / secret manager.",
    ],
    "high-entropy-string": [
        "Identify which system this token belongs to and rotate it there.",
        "Move the value out of the repo into a secret manager or environment variable.",
    ],
}

_DEFAULT_STEPS = [
    "Rotate this credential at its provider and revoke the exposed value.",
    "Move it out of the repository into an environment variable or secret manager.",
]


def steps_for(detector_id: str) -> list[str]:
    """Revocation + rotation steps for a detector, always ending with history purge."""
    steps = list(_STEPS.get(detector_id, _DEFAULT_STEPS))
    steps.append(_PURGE_HISTORY)
    return steps


def annotate(findings: list[Finding]) -> None:
    """Attach remediation steps to every finding in place."""
    for f in findings:
        f.remediation = steps_for(f.detector_id)
