#!/usr/bin/env python3
"""
AI Maintainer — processes issues labeled 'ai-task' and opens pull requests.

Triggered by GitHub Actions when an issue receives the 'ai-task' label.
Uses GitHub Models API (Claude claude-3-5-sonnet, OpenAI-compatible) to understand
the issue, explore the codebase, write the necessary changes, and create a PR.

Environment variables (injected by the workflow):
    GITHUB_TOKEN   GitHub token with contents/PR/issues write permissions
    ISSUE_NUMBER   Number of the issue to process
    ISSUE_TITLE    Issue title
    ISSUE_BODY     Issue body (may be empty)
    REPO           owner/repo string, e.g. psantana5/zbx
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path.cwd()
MAX_ROUNDS = 30  # safety cap on agentic loop iterations

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
ISSUE_NUMBER = str(os.environ["ISSUE_NUMBER"])
ISSUE_TITLE = os.environ.get("ISSUE_TITLE", f"Issue #{ISSUE_NUMBER}")
ISSUE_BODY = os.environ.get("ISSUE_BODY", "")
REPO = os.environ.get("REPO", "")

# GitHub Models API — OpenAI-compatible, authenticated via GITHUB_TOKEN.
# Uses Anthropic's Claude via GitHub's model proxy (no extra secrets needed).
# See: https://docs.github.com/en/github-models
client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

MODEL = "gpt-4o"  # GPT-4o via GitHub Models

# ---------------------------------------------------------------------------
# Tools exposed to the model
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repo root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories inside a directory (one level deep).",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory path relative to the repo root.",
                    }
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repo root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": "Append text to the end of an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to repo root."},
                    "content": {"type": "string", "description": "Text to append."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a pattern across the repository using grep. Returns matching lines with file:line context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex or literal search string."},
                    "path": {"type": "string", "description": "Directory or file to search in (default: whole repo)."},
                    "file_glob": {"type": "string", "description": "File glob filter, e.g. '*.py' (optional)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the unit test suite (tests/test_models.py and tests/test_diff_engine.py). Returns pass/fail summary.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a safe read-only shell command (ls, cat, grep, python3 -c, zbx validate, etc.). "
                "Do NOT use for destructive operations — use write_file instead of shell redirects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate",
            "description": (
                "Run `zbx validate <path>` to check YAML schema correctness. "
                "Always call this after writing a template or check.yaml."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to validate (file or directory).",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Signal that all changes are complete and ready to be committed. "
                "Call this only after writing and validating all files. "
                "If the issue is unclear or too risky, set pr_title to start with 'SKIP:' "
                "and explain in pr_body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": (
                            f"Branch name — MUST follow the format: "
                            f"ai/issue-{ISSUE_NUMBER}-<short-kebab-description>"
                        ),
                    },
                    "pr_title": {
                        "type": "string",
                        "description": "Pull request title (will be prefixed with 'AI Fix: ').",
                    },
                    "pr_body": {
                        "type": "string",
                        "description": "Pull request description summarising the changes made.",
                    },
                },
                "required": ["branch", "pr_title", "pr_body"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def tool_read_file(path: str) -> str:
    p = REPO_ROOT / path
    try:
        content = p.read_text()
        # Truncate very large files to avoid blowing up context
        if len(content) > 8000:
            content = content[:8000] + f"\n... (truncated, {len(content)} bytes total)"
        return content
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def tool_list_files(directory: str) -> str:
    d = REPO_ROOT / directory
    try:
        entries = sorted(d.iterdir())
        lines = [("📁 " if e.is_dir() else "📄 ") + e.name for e in entries]
        return "\n".join(lines) if lines else "(empty directory)"
    except FileNotFoundError:
        return f"ERROR: directory not found: {directory}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def tool_write_file(path: str, content: str) -> str:
    p = REPO_ROOT / path
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def tool_append_to_file(path: str, content: str) -> str:
    p = REPO_ROOT / path
    try:
        with p.open("a") as f:
            f.write(content)
        return f"OK: appended {len(content)} chars to {path}"
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def tool_search_code(pattern: str, path: str = ".", file_glob: str = "") -> str:
    cmd = ["grep", "-rn", "--include", file_glob or "*", pattern, path]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=15)
    output = result.stdout.strip()
    if not output:
        return "No matches found."
    lines = output.splitlines()
    if len(lines) > 60:
        lines = lines[:60]
        lines.append(f"... ({len(output.splitlines()) - 60} more lines truncated)")
    return "\n".join(lines)


def tool_run_tests() -> str:
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/test_models.py", "tests/test_diff_engine.py",
         "-v", "--tb=short", "-q"],
        capture_output=True, text=True, timeout=120, cwd=REPO_ROOT,
    )
    output = (result.stdout + result.stderr).strip()
    # Truncate to last 100 lines to keep context manageable
    lines = output.splitlines()
    if len(lines) > 100:
        output = "\n".join(lines[-100:])
    return output


# Allowed prefixes for run_shell — prevents destructive use
_SHELL_ALLOWLIST = (
    "ls", "cat", "head", "tail", "grep", "find", "wc", "echo",
    "python3 -c", "python3 -m", "zbx validate", "zbx schema",
    "git log", "git diff", "git status", "git tag",
)


def tool_run_shell(command: str) -> str:
    first_word = command.strip().split()[0] if command.strip() else ""
    allowed = any(command.strip().startswith(p) for p in _SHELL_ALLOWLIST)
    if not allowed:
        return (
            f"DENIED: '{first_word}' is not in the allowlist. "
            "Use write_file for writes, run_tests for tests."
        )
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True,  # noqa: S602
        timeout=30, cwd=REPO_ROOT,
    )
    output = (result.stdout + result.stderr).strip()
    if len(output) > 4000:
        output = output[:4000] + "\n... (truncated)"
    return output or f"(exit {result.returncode}, no output)"


def tool_validate(path: str) -> str:
    result = subprocess.run(
        ["zbx", "validate", path],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
    )
    output = (result.stdout + result.stderr).strip()
    return output if output else ("OK: no output (exit 0)" if result.returncode == 0 else "ERROR: non-zero exit")


def dispatch_tool(name: str, args: dict) -> str:
    if name == "read_file":
        return tool_read_file(args["path"])
    if name == "list_files":
        return tool_list_files(args["directory"])
    if name == "write_file":
        return tool_write_file(args["path"], args["content"])
    if name == "append_to_file":
        return tool_append_to_file(args["path"], args["content"])
    if name == "search_code":
        return tool_search_code(args["pattern"], args.get("path", "."), args.get("file_glob", ""))
    if name == "run_tests":
        return tool_run_tests()
    if name == "run_shell":
        return tool_run_shell(args["command"])
    if name == "validate":
        return tool_validate(args["path"])
    return f"ERROR: unknown tool '{name}'"


# ---------------------------------------------------------------------------
# Context builder — gives the model a snapshot of the repo layout
# ---------------------------------------------------------------------------


def build_file_tree() -> str:
    """Return a compact tree of key directories."""
    sections: list[str] = []
    for top in ["zbx", "configs", "scripts", ".github/scripts"]:
        d = REPO_ROOT / top
        if not d.exists():
            continue
        files = [
            "  " + str(f.relative_to(REPO_ROOT))
            for f in sorted(d.rglob("*"))
            if f.is_file() and ".git" not in f.parts
        ]
        sections.append(f"### {top}/\n" + "\n".join(files[:50]))
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
You are an automated maintainer for the **zbx** repository.

zbx is a Python CLI tool (Typer + Pydantic + Rich) that manages Zabbix
monitoring configuration as code — the same mental model as Terraform/Ansible.
Engineers write YAML files and run `zbx apply` to deploy templates, items,
triggers and discovery rules to a Zabbix server via its JSON-RPC API.

## Repository layout

```
zbx/
  cli.py              CLI entry point (commands registered here)
  models.py           Pydantic models for every Zabbix object
  config_loader.py    Loads / validates YAML → model objects
  zabbix_client.py    Zabbix JSON-RPC API client
  deployer.py         Creates / updates Zabbix resources
  diff_engine.py      Desired vs current state comparison
  formatter.py        Rich terminal output
  agent_deployer.py   SSH / local script deployment to hosts
  commands/           One module per CLI sub-command

configs/
  templates/          Zabbix template YAML files
  checks/             Self-contained monitoring checks
  hosts/              Host playbook YAML files

scripts/              Monitoring scripts (Python / shell)
inventory.yaml        Host inventory
```

## YAML template schema

```yaml
template: my-template        # unique technical ID in Zabbix
name: "Display Name"
description: "..."
groups:
  - Templates

items:
  - name: Metric name
    key: item.key             # Zabbix item key
    interval: 60s
    value_type: float         # float | char | log | unsigned | text
    units: "%"
    description: "..."

triggers:
  - name: Alert name
    expression: last(/my-template/item.key) > 90
    severity: high            # not_classified|information|warning|average|high|disaster
    description: "..."

discovery_rules:
  - name: Discovery name
    key: discovery.key
    interval: 1h
    item_prototypes:
      - name: "Item for [{{#MACRO}}]"
        key: "item[{{#MACRO}}]"
        interval: 60s
        value_type: float
    trigger_prototypes:
      - name: "Alert for [{{#MACRO}}]"
        expression: "last(/my-template/item[{{#MACRO}}]) > 90"
        severity: warning
```

## Self-contained check schema  (configs/checks/<name>/check.yaml)

Same as the template schema above, plus an optional `agent:` block:

```yaml
template: my-check
# ... items / triggers / discovery_rules ...

agent:
  scripts:
    - source: configs/checks/my-check/script.py
      dest: /usr/local/scripts/zabbix/script.py
      mode: "0755"
  userparameters:
    - name: my-check
      parameters:
        - key: my.check.value
          command: /usr/local/scripts/zabbix/script.py
  test_keys:
    - my.check.value
```

## Rules

1. **Read before writing** — always call `read_file` on relevant existing files
   before modifying or creating anything.
2. **Trigger expressions** — must reference the template:
   `last(/template-id/item.key) > value`
3. **New monitoring check** → add script to `scripts/` AND template to
   `configs/templates/`, OR create a self-contained check under `configs/checks/`.
4. **Validate** — always call `validate` after writing a template YAML.
5. **Branch name** — MUST be `ai/issue-{ISSUE_NUMBER}-<short-kebab-description>`.
6. **Never modify** workflow files or CI config unless explicitly requested.
7. **Skip** unclear or destructive requests: call `finish` with
   `pr_title` starting with `"SKIP:"` and explain in `pr_body`.
"""


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------


def run() -> dict:
    """Run the model in a tool-call loop until it calls finish()."""
    file_tree = build_file_tree()

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"## Issue #{ISSUE_NUMBER}: {ISSUE_TITLE}\n\n"
                f"{ISSUE_BODY or '(no description provided)'}\n\n"
                "---\n\n"
                f"## Repository file tree\n\n{file_tree}\n\n"
                "Please implement this issue.\n"
                "1. Read the relevant files to understand the current code.\n"
                "2. Write the necessary changes.\n"
                "3. Validate any modified YAML.\n"
                "4. Call `finish()` with the branch name, PR title and PR body."
            ),
        }
    ]

    for round_num in range(MAX_ROUNDS):
        print(f"[round {round_num + 1}/{MAX_ROUNDS}] calling model…", flush=True)

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )

        msg = response.choices[0].message

        # Append assistant message (must include tool_calls if present)
        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            # Model returned prose — nudge it to continue with tools
            print(f"  (text only) {(msg.content or '')[:120]}")
            messages.append({
                "role": "user",
                "content": (
                    "Please continue by calling the appropriate tools, "
                    "or call finish() if all changes are complete."
                ),
            })
            continue

        finish_args: dict | None = None

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  → {name}({', '.join(f'{k}={repr(v)[:40]}' for k, v in args.items())})")

            if name == "finish":
                finish_args = args
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "OK: finishing",
                })
            else:
                result = dispatch_tool(name, args)
                truncated = result[:200] + "…" if len(result) > 200 else result
                print(f"    ← {truncated}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        if finish_args is not None:
            return finish_args

    # Hit the round limit
    print(f"WARNING: reached max rounds ({MAX_ROUNDS}) without finish()")
    return {
        "branch": f"ai/issue-{ISSUE_NUMBER}-timeout",
        "pr_title": f"SKIP: Issue #{ISSUE_NUMBER} — AI agent timed out",
        "pr_body": (
            f"The AI agent exceeded the maximum number of rounds ({MAX_ROUNDS}) "
            "without completing. Manual review required."
        ),
    }


# ---------------------------------------------------------------------------
# Git + GitHub helpers
# ---------------------------------------------------------------------------


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return result.stdout.strip()


def post_issue_comment(body: str) -> None:
    gh("issue", "comment", ISSUE_NUMBER, "--body", body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"AI Maintainer — issue #{ISSUE_NUMBER}: {ISSUE_TITLE}", flush=True)
    print("-" * 60)

    finish_args = run()

    branch = finish_args["branch"]
    pr_title = finish_args["pr_title"]
    pr_body = finish_args["pr_body"]

    print(f"\nfinish() called → branch={branch!r}")

    # Model decided to skip this issue
    if pr_title.startswith("SKIP:"):
        reason = pr_body or pr_title
        print(f"Skipping: {reason}")
        post_issue_comment(
            f"🤖 **AI maintainer skipped this issue.**\n\n{reason}\n\n"
            "_Manual implementation may be required._"
        )
        sys.exit(0)

    # Configure git identity for the commit
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "github-actions[bot]@users.noreply.github.com")

    # Check whether the model actually changed any files
    status = git("status", "--porcelain")
    if not status:
        print("No files changed — nothing to commit.")
        post_issue_comment(
            f"🤖 **AI maintainer** found no file changes needed for issue #{ISSUE_NUMBER}."
        )
        sys.exit(0)

    print(f"Changed files:\n{status}")

    # Create branch, commit, push
    git("checkout", "-b", branch)
    git("add", "-A")
    git(
        "commit",
        "-m",
        (
            f"AI fix for issue #{ISSUE_NUMBER}: {ISSUE_TITLE}\n\n"
            f"Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
        ),
    )
    subprocess.run(["git", "push", "origin", branch], cwd=REPO_ROOT, check=True)

    # Open PR
    pr_url = gh(
        "pr", "create",
        "--base", "main",
        "--head", branch,
        "--title", f"AI Fix: {ISSUE_TITLE}",
        "--body", (
            f"Automated implementation generated by Copilot based on "
            f"[issue #{ISSUE_NUMBER}](../../issues/{ISSUE_NUMBER}).\n\n"
            f"{pr_body}"
        ),
    )

    print(f"PR created: {pr_url}")

    post_issue_comment(
        f"🤖 **AI maintainer** has opened a pull request for this issue: {pr_url}"
    )


if __name__ == "__main__":
    # Inject system prompt into the client for all calls
    original_create = client.chat.completions.create

    def create_with_system(*args, messages=None, **kwargs):  # type: ignore[override]
        if messages and messages[0].get("role") != "system":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        return original_create(*args, messages=messages, **kwargs)

    client.chat.completions.create = create_with_system  # type: ignore[method-assign]

    main()
