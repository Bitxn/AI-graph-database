"""
Env-drift — the onboarding hook, and mostly deterministic.

We parse code for environment reads (os.getenv/os.environ, process.env,
import.meta.env, Deno.env.get) and diff them against the declared surface in
.env.example (or .env.sample/.template/.dist). Then:

  - used-but-undeclared → the ".env.example is stale, new hire can't boot" bug
  - declared-but-unused → dead config / a variable that was renamed

The LLM does one small thing: classify each drifting variable as a secret vs a
plain toggle, so the report can say "you forgot to document a SECRET" loudly.
Detection stays deterministic; the model only labels.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from oneport_secrets.config import Config
from oneport_secrets.exceptions import AuthError, OneportError
from oneport_secrets.llm import call_gemini, is_gemini_model
from oneport_secrets.result import EnvResult, EnvVar
from oneport_secrets.scanner import _is_ignored, _iter_worktree_files, _is_probably_binary

_ENV_READ_PATTERNS = [
    re.compile(r"""os\.(?:getenv|environ\.get)\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\]"""),
    re.compile(r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""process\.env\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""Deno\.env\.get\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),
]

# Framework-provided vars that are never declared in a project's .env.example.
_BUILTIN_ENV = {
    "NODE_ENV", "PATH", "HOME", "PWD", "USER", "SHELL", "LANG", "TERM",
    "PYTHONPATH", "CI", "TZ", "TMPDIR", "HOSTNAME",
}

_EXAMPLE_FILENAMES = [".env.example", ".env.sample", ".env.template", ".env.dist", ".env.local.example"]

_MAX_FILE_BYTES = 2_000_000


def find_example_file(root: Path) -> Path | None:
    for name in _EXAMPLE_FILENAMES:
        p = root / name
        if p.exists():
            return p
    return None


def parse_env_declarations(path: Path) -> set[str]:
    """Extract declared variable names from a .env-style file (KEY=... lines)."""
    names: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):]
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            names.add(m.group(1))
    return names


def collect_env_reads(root: Path, ignore: list[str]) -> dict[str, list[str]]:
    """Map each env var name read in code to a list of 'path:line' references."""
    reads: dict[str, list[str]] = {}
    for fpath in _iter_worktree_files(root, ignore):
        try:
            raw = fpath.read_bytes()
        except OSError:
            continue
        if len(raw) > _MAX_FILE_BYTES or _is_probably_binary(raw):
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        rel = str(fpath.relative_to(root)).replace(os.sep, "/")
        for lineno, line in enumerate(content.splitlines(), start=1):
            for pat in _ENV_READ_PATTERNS:
                for m in pat.finditer(line):
                    name = m.group(1)
                    if name in _BUILTIN_ENV:
                        continue
                    reads.setdefault(name, []).append(f"{rel}:{lineno}")
    return reads


CLASSIFY_SYSTEM_PROMPT = """\
You label environment-variable names as either a "secret" (credentials, API
keys, tokens, passwords, connection strings, signing keys) or a "toggle"
(feature flags, hostnames, ports, log levels, timeouts, public config).

Judge only from the variable NAME. Output ONLY valid JSON, no prose:
{"labels": [{"name": "STRIPE_SECRET_KEY", "kind": "secret"}, {"name": "LOG_LEVEL", "kind": "toggle"}]}
"""


class EnvChecker:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config
        self._last_tokens = 0

    def check(self, path: str, ignore: list[str] | None = None, classify: bool = True) -> EnvResult:
        root = Path(path).resolve()
        ignore = ignore or []

        example = find_example_file(root)
        declared = parse_env_declarations(example) if example else set()
        reads = collect_env_reads(root, ignore)
        used = set(reads)

        undeclared = sorted(used - declared)
        unused = sorted(declared - used)

        result = EnvResult(
            used_but_undeclared=[EnvVar(name=n, used_in=reads[n], used=True, declared=False)
                                 for n in undeclared],
            declared_but_unused=[EnvVar(name=n, declared=True, used=False) for n in unused],
            example_path=str(example.relative_to(root)) if example else "",
            scanned_files=0,
        )

        names = undeclared + unused
        if classify and names and self.config and self.config.has_key:
            try:
                self._classify(result, names)
                result.classified = True
                result.model = self.config.model
                result.total_tokens = self._last_tokens
            except OneportError:
                # Classification is a nicety, not a gate — never fail the check on it.
                pass
        return result

    def _classify(self, result: EnvResult, names: list[str]) -> None:
        raw = self._call_model(CLASSIFY_SYSTEM_PROMPT, "Variables:\n" + "\n".join(names))
        labels = _parse_labels(raw)
        by_name = {v.name: v for v in result.used_but_undeclared + result.declared_but_unused}
        for item in labels:
            var = by_name.get(str(item.get("name", "")))
            if var and str(item.get("kind", "")).lower() in {"secret", "toggle"}:
                var.kind = str(item["kind"]).lower()

    def _call_model(self, system: str, user: str) -> str:
        assert self.config is not None
        if is_gemini_model(self.config.model):
            text, tokens = call_gemini(
                model=self.config.model,
                api_key=self.config.api_key,
                system=system,
                user=user,
                max_tokens=self.config.max_tokens,
            )
            self._last_tokens = tokens
            return text
        raise AuthError("env-check classification requires a gemini-* model + GEMINI_API_KEY.")


def _parse_labels(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OneportError("env classifier returned non-JSON output") from exc
    labels = data.get("labels", []) if isinstance(data, dict) else []
    return [x for x in labels if isinstance(x, dict)]
