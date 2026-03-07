#!/usr/bin/env python3
"""
zbx Autonomous Worker
=====================
Runs on a time budget, implements improvements from a task catalogue,
commits each change to a dedicated branch, then opens a single PR.
The only human action required is reviewing and merging the PR.

Flow
----
1. Create branch: ai/worker-YYYYMMDD
2. Pick tasks (round-robin across: tests, reliability, docs, features)
3. For each task run an LLM agentic loop (read → plan → write → verify)
4. Commit each completed task to the branch
5. Open a PR to main with a summary of all work done
6. Create a tracking issue listing what was done

Environment variables
---------------------
GITHUB_TOKEN   GitHub token (contents write + PR write + issues write)
REPO           owner/repo  e.g. psantana5/zbx
BUDGET_MINUTES How many minutes to run (default 90)
FOCUS          tests | reliability | docs | features | auto (default auto)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from openai import OpenAI


def _detect_repo() -> str:
    """Infer owner/repo from git remote.origin.url (fallback for local runs)."""
    import subprocess as _sp
    try:
        url = _sp.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        # handles https://github.com/owner/repo.git and git@github.com:owner/repo.git
        url = url.removeprefix("https://github.com/").removeprefix("git@github.com:")
        return url.removesuffix(".git")
    except Exception:
        raise RuntimeError("Cannot detect repo — set REPO env var (e.g. owner/repo)")

# ─── config ──────────────────────────────────────────────────────────────────
REPO_ROOT     = Path.cwd()
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
REPO          = os.environ.get("REPO") or _detect_repo()
BUDGET_MIN    = int(os.environ.get("BUDGET_MINUTES", "90"))
FOCUS         = os.environ.get("FOCUS", "auto").lower()
MODEL         = "gpt-4o"
MAX_ROUNDS    = 25          # agentic loop cap per task

client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=GITHUB_TOKEN)
GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# ─── task catalogue ──────────────────────────────────────────────────────────
TASK_CATALOGUE = {
    "tests": [
        ("add-tests-diff-engine",
         "Look at zbx/diff_engine.py. Find any public functions not covered by "
         "tests/test_diff_engine.py. Add meaningful pytest test cases for them."),

        ("add-tests-config-loader",
         "Look at zbx/config_loader.py. Create or extend tests/test_config_loader.py "
         "with cases covering YAML validation, missing required keys, and multi-file loading. "
         "Mock file I/O where needed."),

        ("add-tests-commands",
         "Look at zbx/commands/host.py, hostgroup.py, macro.py. Write tests in "
         "tests/test_commands.py that mock ZabbixClient and verify the correct API "
         "methods are called with the right arguments."),

        ("add-tests-plan-apply",
         "Look at zbx/commands/plan.py and apply.py. Add unit tests in tests/ that "
         "cover --dry-run, --output / --from-plan paths (mock ZabbixClient so no "
         "real server is needed)."),
    ],
    "reliability": [
        ("error-handling-client",
         "Audit zbx/zabbix_client.py for missing error handling. Every API call "
         "should catch requests exceptions and raise a clear ZabbixClientError "
         "with a user-friendly message. Implement any that are missing."),

        ("error-handling-deployer",
         "Audit zbx/deployer.py for edge-cases: empty template list, templates "
         "with no items/triggers, duplicate item keys. Add guards and clear error messages."),

        ("error-handling-config-loader",
         "In zbx/config_loader.py, make sure malformed YAML files surface a "
         "friendly error with the file path and line number, not a raw traceback."),

        ("rich-error-panels",
         "Review zbx/commands/apply.py and plan.py for unhandled exceptions that "
         "show a raw traceback. Wrap them in try/except with rich.console error panels."),
    ],
    "docs": [
        ("add-docstrings-core",
         "Audit zbx/zabbix_client.py and zbx/deployer.py for public methods "
         "missing docstrings. Add concise Google-style docstrings to any that lack them."),

        ("add-docstrings-models",
         "Audit zbx/models.py and zbx/diff_engine.py for public classes and "
         "functions missing docstrings. Add concise Google-style docstrings."),

        ("improve-cli-help",
         "Review help= strings in zbx/commands/*.py. Any command or option with "
         "an empty or generic help string should get a clear, specific description."),

        ("check-yaml-descriptions",
         "Review configs/checks/ — any check.yaml missing a 'description' field "
         "should have one added explaining what it monitors and its dependencies."),
    ],
    "features": [
        ("template-list-command",
         "Add a 'zbx template list' sub-command in zbx/commands/template.py that "
         "prints a Rich table of all templates in configs/ (name, item count, trigger "
         "count, file path). Register it in zbx/cli.py."),

        ("json-output-flag",
         "Add --format json output to 'zbx host list', 'zbx hostgroup list', and "
         "'zbx macro list' so results can be piped to other tools. Default stays rich table."),

        ("check-verify-command",
         "Add 'zbx check verify <check-name>' in zbx/commands/check.py that runs the "
         "check script locally with --test flag (if it exists) and shows pass/fail."),

        ("diff-since-flag",
         "Add a --since YYYY-MM-DD flag to 'zbx diff' that uses git log to find which "
         "YAML files changed since that date and only diffs those templates."),
    ],
}

ALL_TASKS = [(slug, desc) for tasks in TASK_CATALOGUE.values() for slug, desc in tasks]

def pick_tasks() -> list[tuple[str, str]]:
    if FOCUS == "auto":
        # Interleave categories so each run covers multiple areas
        from itertools import zip_longest
        cats = list(TASK_CATALOGUE.values())
        result = []
        for row in zip_longest(*cats):
            result.extend(t for t in row if t is not None)
        return result
    return TASK_CATALOGUE.get(FOCUS, ALL_TASKS)

# ─── tools exposed to the model ──────────────────────────────────────────────
TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file from the repository.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List files in a directory (one level deep).",
        "parameters": {"type": "object",
                       "properties": {"directory": {"type": "string"}},
                       "required": ["directory"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write (overwrite) a file with new content.",
        "parameters": {"type": "object",
                       "properties": {
                           "path":    {"type": "string"},
                           "content": {"type": "string"},
                       },
                       "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a safe shell command (read-only or pytest).",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}},
                       "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "task_done",
        "description": "Signal task is complete. Provide a short one-line summary of the change.",
        "parameters": {"type": "object",
                       "properties": {"summary": {"type": "string"}},
                       "required": ["summary"]},
    }},
    {"type": "function", "function": {
        "name": "task_skip",
        "description": "Skip this task — nothing useful to add (e.g. already done).",
        "parameters": {"type": "object",
                       "properties": {"reason": {"type": "string"}},
                       "required": ["reason"]},
    }},
]

SHELL_ALLOWLIST = (
    "ls ", "cat ", "find ", "grep ", "head ", "tail ", "wc ",
    "python3 -m pytest", "python3 -c ", "python3 -m ",
    "git log", "git diff", "git status",
    "zbx validate", "zbx schema",
)

def dispatch(name: str, args: dict) -> str:
    if name == "read_file":
        p = REPO_ROOT / args["path"]
        if not p.exists():
            return f"ERROR: {args['path']} does not exist"
        content = p.read_text(errors="replace")
        return content[:12_000] + ("\n... (truncated)" if len(content) > 12_000 else "")

    if name == "list_files":
        p = REPO_ROOT / args["directory"]
        if not p.exists():
            return f"ERROR: {args['directory']} does not exist"
        return "\n".join(
            str(e.relative_to(REPO_ROOT)) + ("/" if e.is_dir() else "")
            for e in sorted(p.iterdir())
        )

    if name == "write_file":
        p = REPO_ROOT / args["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        return f"OK: wrote {len(args['content'])} bytes to {args['path']}"

    if name == "run_shell":
        cmd = args["command"].strip()
        if not any(cmd.startswith(a) for a in SHELL_ALLOWLIST):
            return f"BLOCKED: '{cmd}' is not on the allowlist"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           cwd=REPO_ROOT, timeout=60)
        out = (r.stdout + r.stderr).strip()
        return out[-3000:] if len(out) > 3000 else out

    return "ERROR: unknown tool"

# ─── agentic loop ────────────────────────────────────────────────────────────
SYSTEM = f"""\
You are an autonomous software engineer improving the **zbx** project
(Python CLI for Zabbix config-as-code, repo: {REPO}).

Tools: read_file, list_files, write_file, run_shell (safe subset), task_done, task_skip.

Workflow for each task:
1. Read the relevant source files.
2. Plan the minimal correct change.
3. Write the changed files with write_file.
4. Run `python3 -m pytest tests/ -q` to confirm nothing breaks.
5. Call task_done with a concise one-line summary (will become the commit message).

Rules:
- Minimal change — don't refactor things unrelated to the task.
- Never remove working code unless the task requires it.
- If the task is already fully done, call task_skip (don't make a no-op commit).
- No TODO comments — implement or skip.
"""

def run_task(description: str) -> tuple[bool, str]:
    """Run agentic loop for one task. Returns (files_changed, summary)."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": f"Task:\n\n{description}"},
    ]
    files_changed = False

    for _ in range(MAX_ROUNDS):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=0.2, max_tokens=4096,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            messages.append({"role": "user",
                              "content": "Please call task_done or task_skip."})
            continue

        tool_results = []
        final = None
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            if name == "task_done":
                final = ("done", args.get("summary", ""))
                tool_results.append({"tool_call_id": tc.id, "role": "tool",
                                     "content": "acknowledged"})
            elif name == "task_skip":
                final = ("skip", args.get("reason", ""))
                tool_results.append({"tool_call_id": tc.id, "role": "tool",
                                     "content": "acknowledged"})
            else:
                if name == "write_file":
                    files_changed = True
                tool_results.append({"tool_call_id": tc.id, "role": "tool",
                                     "content": dispatch(name, args)})

        messages.extend(tool_results)
        if final:
            return (files_changed if final[0] == "done" else False), final[1]

    return False, "hit max rounds without completing"

# ─── git / GitHub helpers ────────────────────────────────────────────────────
def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       cwd=REPO_ROOT, check=check)
    return (r.stdout + r.stderr).strip()

def setup_git() -> None:
    git("config", "user.name",  "zbx-worker[bot]")
    git("config", "user.email", "zbx-worker[bot]@users.noreply.github.com")

def commit_task(summary: str) -> bool:
    """Stage all changes and commit. Returns True if something was committed."""
    git("add", "-A")
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                          cwd=REPO_ROOT)
    if diff.returncode == 0:
        return False   # nothing staged
    git("commit", "-m",
        f"chore(worker): {summary}\n\n"
        "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>")
    return True

def push_branch(branch: str) -> None:
    subprocess.run(["git", "push", "origin", branch], cwd=REPO_ROOT, check=True)

def open_pr(branch: str, tasks_done: list[tuple[str, str]]) -> str:
    """Open a PR and return its URL."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"chore(worker): autonomous improvements — {today}"

    lines = ["## Autonomous worker run\n",
             f"**Date:** {today}  |  **Focus:** {FOCUS}  |  "
             f"**Tasks completed:** {len(tasks_done)}\n\n",
             "### Changes\n"]
    for slug, summary in tasks_done:
        lines.append(f"- **{slug}**: {summary}")
    lines += ["\n---",
              "_All changes made by the zbx autonomous worker. "
              "Review and merge to apply._"]
    body = "\n".join(lines)

    resp = requests.post(
        f"https://api.github.com/repos/{REPO}/pulls",
        headers=GH_HEADERS,
        json={"title": title, "body": body,
              "head": branch, "base": "main"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return resp.json().get("html_url", "")
    # PR might already exist
    return ""

def create_tracking_issue(tasks_done: list[tuple[str, str]],
                          tasks_skipped: list[tuple[str, str]],
                          pr_url: str) -> None:
    """Create a brief tracking issue for this run."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## Worker run — {today}\n",
             f"**PR:** {pr_url or '(none — nothing to commit)'}\n\n",
             f"### ✅ Completed ({len(tasks_done)})\n"]
    for slug, summary in tasks_done:
        lines.append(f"- `{slug}`: {summary}")
    if tasks_skipped:
        lines += [f"\n### ⏭ Skipped ({len(tasks_skipped)})"]
        for slug, reason in tasks_skipped:
            lines.append(f"- `{slug}`: {reason}")
    lines += ["\n---",
              "_Automatically created by the zbx autonomous worker._"]

    requests.post(
        f"https://api.github.com/repos/{REPO}/issues",
        headers=GH_HEADERS,
        json={"title": f"[worker] run {today}",
              "body": "\n".join(lines),
              "labels": ["ai-generated"]},
        timeout=15,
    )

# ─── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    deadline  = time.time() + BUDGET_MIN * 60
    today     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch    = f"ai/worker-{today}"

    tasks_done: list[tuple[str, str]] = []
    tasks_skipped: list[tuple[str, str]] = []

    print(f"zbx autonomous worker  budget={BUDGET_MIN}m  focus={FOCUS}")
    print(f"branch: {branch}")
    print("=" * 60)

    setup_git()
    git("checkout", "-b", branch)

    task_list = pick_tasks()

    for i, (slug, description) in enumerate(task_list):
        if time.time() >= deadline:
            print(f"\n⏰  Budget exhausted ({len(tasks_done)} completed).")
            break

        remaining = int((deadline - time.time()) / 60)
        print(f"\n[{i+1}/{len(task_list)}] ⏳ {remaining}m left — {slug}")
        print(f"  {description[:90]}…")

        try:
            changed, summary = run_task(description)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}")
            tasks_skipped.append((slug, str(exc)))
            continue

        if changed:
            committed = commit_task(summary)
            if committed:
                print(f"  ✅  committed: {summary}")
                tasks_done.append((slug, summary))
            else:
                print(f"  ⚠️   write_file was called but git diff was empty")
                tasks_skipped.append((slug, "no effective diff"))
        else:
            print(f"  ⏭   skipped: {summary}")
            tasks_skipped.append((slug, summary))

    pr_url = ""
    if tasks_done:
        print(f"\nPushing branch {branch}…")
        push_branch(branch)
        print("Opening PR…")
        pr_url = open_pr(branch, tasks_done)
        print(f"PR: {pr_url or '(failed to create)'}")
    else:
        print("\nNo changes — skipping PR.")

    print("Creating tracking issue…")
    create_tracking_issue(tasks_done, tasks_skipped, pr_url)

    print(f"\nDone — {len(tasks_done)} committed, {len(tasks_skipped)} skipped.")

if __name__ == "__main__":
    main()
