#!/usr/bin/env python3
"""
Autonomous Agent — proactive, scheduled intelligence for the zbx repository.

Modes
-----
scan      Scan the codebase for improvement opportunities (TODOs, test gaps,
          missing docs, potential bugs) and create `ai-task` issues for the
          AI maintainer to process.

issues    Process ALL open `ai-task` issues in batch by delegating to the
          ai_maintainer logic for each one.

release   Auto-bump the patch version and create a release PR if there are
          unreleased commits on main and the test suite passes.

full      scan + issues (default for the daily cron run).

Environment variables
---------------------
GITHUB_TOKEN   GitHub token with contents/PR/issues write permissions
REPO           owner/repo string, e.g. psantana5/zbx
AGENT_MODE     One of: scan | issues | release | full (default: full)
DRY_RUN        Set to '1' to skip GitHub writes (print what would happen)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path.cwd()
MAX_SCAN_ISSUES = 5          # max issues to create per scan run
MAX_ISSUE_ROUNDS = 30        # agentic loop cap per issue
SCAN_MODEL = "gpt-4o-mini"   # cheaper model for the scan/triage phase
FIX_MODEL = "claude-3-5-sonnet"  # stronger model for the fix phase

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ.get("REPO", "")
AGENT_MODE = os.environ.get("AGENT_MODE", "full").lower()
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

API_BASE = "https://models.inference.ai.azure.com"
GH_API = "https://api.github.com"

ai_client = OpenAI(base_url=API_BASE, api_key=GITHUB_TOKEN)

GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


def gh_get(path: str) -> dict | list:
    r = requests.get(f"{GH_API}{path}", headers=GH_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def gh_post(path: str, payload: dict) -> dict:
    if DRY_RUN:
        print(f"[DRY_RUN] POST {path}: {json.dumps(payload)[:200]}")
        return {}
    r = requests.post(f"{GH_API}{path}", headers=GH_HEADERS, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def gh_patch(path: str, payload: dict) -> dict:
    if DRY_RUN:
        print(f"[DRY_RUN] PATCH {path}: {json.dumps(payload)[:200]}")
        return {}
    r = requests.patch(f"{GH_API}{path}", headers=GH_HEADERS, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def get_open_ai_task_issues() -> list[dict]:
    """Return all open issues with the 'ai-task' label."""
    issues: list[dict] = []
    page = 1
    while True:
        batch = gh_get(f"/repos/{REPO}/issues?labels=ai-task&state=open&per_page=30&page={page}")
        if not isinstance(batch, list) or not batch:
            break
        issues.extend(batch)
        page += 1
    # Exclude pull requests (GitHub returns PRs in issues endpoint too)
    return [i for i in issues if "pull_request" not in i]


def create_issue(title: str, body: str, labels: list[str]) -> int:
    """Create a GitHub issue and return its number."""
    result = gh_post(
        f"/repos/{REPO}/issues",
        {"title": title, "body": body, "labels": labels},
    )
    return result.get("number", 0)


def ensure_label_exists(name: str, color: str = "0075ca", description: str = "") -> None:
    """Create the label if it doesn't exist yet."""
    try:
        gh_get(f"/repos/{REPO}/labels/{name}")
    except Exception:  # noqa: BLE001
        try:
            gh_post(
                f"/repos/{REPO}/labels",
                {"name": name, "color": color, "description": description},
            )
        except Exception:  # noqa: BLE001
            pass  # label might already exist with different casing


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], cwd: Path = REPO_ROOT, timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return result.returncode, (result.stdout + result.stderr).strip()


def git(*args: str) -> str:
    code, out = run(["git", *args])
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{out}")
    return out


# ---------------------------------------------------------------------------
# MODE: scan — proactive codebase analysis
# ---------------------------------------------------------------------------

SCAN_SYSTEM = """\
You are a senior Python engineer reviewing the zbx codebase for improvement
opportunities. Your job is to identify specific, actionable issues worth
creating GitHub issues for.

For each finding, return a JSON object with:
  title   — concise issue title (max 80 chars)
  body    — detailed description with file path, line numbers, suggested fix
  label   — one of: "bug", "enhancement", "documentation", "test"

Be selective — only report genuinely useful improvements, not cosmetic style issues.
Return a JSON array of objects (empty array if nothing significant found).
Focus on:
- Actual bugs or logic errors
- Missing error handling for edge cases
- Test coverage gaps for important functions
- Missing documentation for public API
- Performance issues
- Security concerns

Do NOT report:
- Minor style issues (variable naming, etc.)
- Already-documented TODO items that are feature requests
- Anything that would require major architectural changes
"""


def _read_source_for_scan() -> str:
    """Build a compact source snapshot for the LLM to analyse."""
    parts: list[str] = []
    # Core modules (most important)
    priority = [
        "zbx/zabbix_client.py",
        "zbx/deployer.py",
        "zbx/diff_engine.py",
        "zbx/config_loader.py",
        "zbx/models.py",
        "zbx/commands/apply.py",
        "zbx/commands/plan.py",
        "zbx/commands/inventory.py",
    ]
    for p in priority:
        fp = REPO_ROOT / p
        if fp.exists():
            content = fp.read_text()
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"
            parts.append(f"### {p}\n```python\n{content}\n```")

    # Also include test files to understand current coverage
    for tp in ["tests/test_models.py", "tests/test_diff_engine.py"]:
        fp = REPO_ROOT / tp
        if fp.exists():
            content = fp.read_text()
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            parts.append(f"### {tp}\n```python\n{content}\n```")

    return "\n\n".join(parts)


def _get_existing_issue_titles() -> set[str]:
    """Return a set of open issue titles to avoid creating duplicates."""
    try:
        issues = gh_get(f"/repos/{REPO}/issues?state=open&per_page=100")
        if isinstance(issues, list):
            return {i["title"].lower() for i in issues}
    except Exception:  # noqa: BLE001
        pass
    return set()


def scan_and_create_issues() -> int:
    """Analyse the codebase and create up to MAX_SCAN_ISSUES `ai-task` issues."""
    print("=== MODE: scan ===")
    ensure_label_exists("ai-task", "e4e669", "Automated task for the AI maintainer")
    ensure_label_exists("ai-generated", "bfd4f2", "Created by the autonomous agent")

    source = _read_source_for_scan()
    existing_titles = _get_existing_issue_titles()

    print("  Asking model to analyse codebase…")
    response = ai_client.chat.completions.create(
        model=SCAN_MODEL,
        messages=[
            {"role": "system", "content": SCAN_SYSTEM},
            {"role": "user", "content": (
                "Analyse the following zbx source code and identify improvement opportunities.\n\n"
                + source
                + "\n\nReturn ONLY a valid JSON array."
            )},
        ],
        temperature=0.2,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content or "[]"
    # Extract JSON from possible markdown code fences
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    raw_json = match.group(0) if match else "[]"

    try:
        findings: list[dict] = json.loads(raw_json)
    except json.JSONDecodeError:
        print(f"  WARNING: Could not parse model response as JSON: {raw[:200]}")
        findings = []

    print(f"  Found {len(findings)} potential issue(s)")
    created = 0

    for finding in findings[:MAX_SCAN_ISSUES]:
        title = finding.get("title", "").strip()
        body = finding.get("body", "").strip()
        label = finding.get("label", "enhancement")

        if not title or not body:
            continue

        # Skip if a very similar issue already exists
        if title.lower() in existing_titles:
            print(f"  SKIP (duplicate): {title}")
            continue

        body_with_footer = (
            body + "\n\n---\n_This issue was automatically created by the zbx autonomous agent._"
        )

        issue_num = create_issue(title, body_with_footer, ["ai-task", "ai-generated", label])
        if issue_num:
            print(f"  Created issue #{issue_num}: {title}")
            existing_titles.add(title.lower())
            created += 1
        else:
            print(f"  (dry-run) Would create: {title}")
            created += 1

    print(f"  scan complete — {created} issue(s) created")
    return created


# ---------------------------------------------------------------------------
# MODE: issues — process all open ai-task issues
# ---------------------------------------------------------------------------


def process_all_issues() -> None:
    """Process every open `ai-task` issue using the ai_maintainer logic."""
    print("=== MODE: issues ===")

    issues = get_open_ai_task_issues()
    if not issues:
        print("  No open ai-task issues found.")
        return

    print(f"  Found {len(issues)} open ai-task issue(s)")

    for issue in issues:
        num = str(issue["number"])
        title = issue["title"]
        body = issue.get("body") or ""
        print(f"\n  → Processing issue #{num}: {title}")

        if DRY_RUN:
            print(f"    [DRY_RUN] Would invoke ai_maintainer for issue #{num}")
            continue

        # Invoke ai_maintainer.py as a subprocess with the issue env vars set
        env = {
            **os.environ,
            "ISSUE_NUMBER": num,
            "ISSUE_TITLE": title,
            "ISSUE_BODY": body,
            "REPO": REPO,
            "GITHUB_TOKEN": GITHUB_TOKEN,
        }
        result = subprocess.run(
            [sys.executable, ".github/scripts/ai_maintainer.py"],
            env=env,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"    WARNING: ai_maintainer exited {result.returncode} for issue #{num}")
        else:
            print(f"    OK: ai_maintainer completed for issue #{num}")


# ---------------------------------------------------------------------------
# MODE: release — auto bump + tag if unreleased commits exist
# ---------------------------------------------------------------------------

RELEASE_SYSTEM = """\
You are a release manager for the zbx Python CLI tool.
Given the list of unreleased commits, write a concise CHANGELOG entry and
decide the appropriate semver bump (patch/minor/major).

Return JSON:
{
  "bump": "patch" | "minor" | "major",
  "changelog_entry": "markdown text for the new version section",
  "release_title": "one-line summary for the git tag / GitHub release"
}
"""


def get_latest_tag() -> str:
    try:
        return git("describe", "--tags", "--abbrev=0").strip()
    except RuntimeError:
        return "v0.0.0"


def get_unreleased_commits(latest_tag: str) -> list[str]:
    try:
        log = git("log", f"{latest_tag}..HEAD", "--oneline")
        return [l.strip() for l in log.splitlines() if l.strip()]
    except RuntimeError:
        return []


def get_current_version() -> str:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def bump_version(current: str, bump: str) -> str:
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if bump == "major":
        return f"{parts[0] + 1}.0.0"
    if bump == "minor":
        return f"{parts[0]}.{parts[1] + 1}.0"
    return f"{parts[0]}.{parts[1]}.{parts[2] + 1}"


def run_unit_tests() -> bool:
    """Return True if unit tests pass."""
    code, out = run(
        ["python3", "-m", "pytest", "tests/test_models.py", "tests/test_diff_engine.py", "-q"],
        timeout=120,
    )
    print(f"  Tests: {'PASS' if code == 0 else 'FAIL'}")
    if code != 0:
        print(out[-1000:])
    return code == 0


def auto_release() -> None:
    """Create a release PR if unreleased commits exist and tests pass."""
    print("=== MODE: release ===")

    latest_tag = get_latest_tag()
    commits = get_unreleased_commits(latest_tag)
    print(f"  Latest tag: {latest_tag}")
    print(f"  Unreleased commits: {len(commits)}")

    if not commits:
        print("  Nothing to release.")
        return

    if not run_unit_tests():
        print("  Tests failing — skipping release.")
        return

    current_version = get_current_version()
    print(f"  Current version: {current_version}")

    commits_text = "\n".join(f"- {c}" for c in commits[:30])
    response = ai_client.chat.completions.create(
        model=SCAN_MODEL,
        messages=[
            {"role": "system", "content": RELEASE_SYSTEM},
            {"role": "user", "content": (
                f"Current version: {current_version}\n"
                f"Unreleased commits:\n{commits_text}\n\n"
                "Return ONLY valid JSON."
            )},
        ],
        temperature=0.1,
        max_tokens=800,
    )

    raw = response.choices[0].message.content or "{}"
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    raw_json = match.group(0) if match else "{}"

    try:
        decision = json.loads(raw_json)
    except json.JSONDecodeError:
        decision = {"bump": "patch", "changelog_entry": commits_text, "release_title": "Patch release"}

    new_version = bump_version(current_version, decision.get("bump", "patch"))
    changelog_entry = decision.get("changelog_entry", commits_text)
    release_title = decision.get("release_title", f"Release {new_version}")

    print(f"  Bump: {current_version} → {new_version} ({decision.get('bump', 'patch')})")

    if DRY_RUN:
        print(f"  [DRY_RUN] Would create release PR for v{new_version}: {release_title}")
        return

    # Create release branch
    branch = f"ai/release-v{new_version}"
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    git("checkout", "-b", branch)

    # Bump pyproject.toml
    pyproject_path = REPO_ROOT / "pyproject.toml"
    pyproject = pyproject_path.read_text()
    pyproject = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        f'\\1"{new_version}"',
        pyproject,
        flags=re.MULTILINE,
    )
    pyproject_path.write_text(pyproject)

    # Bump __init__.py
    init_path = REPO_ROOT / "zbx" / "__init__.py"
    if init_path.exists():
        init = init_path.read_text()
        init = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"', init)
        init_path.write_text(init)

    # Prepend changelog entry
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    if changelog_path.exists():
        existing = changelog_path.read_text()
        today = subprocess.check_output(["date", "+%Y-%m-%d"]).decode().strip()
        new_section = f"\n## [{new_version}] — {today}\n\n{changelog_entry}\n"
        # Insert after the header (first two lines)
        lines = existing.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("---"):
                insert_at = i + 1
                break
        lines.insert(insert_at, new_section)
        changelog_path.write_text("".join(lines))

    git("add", "-A")
    git("commit", "-m", (
        f"chore: release v{new_version}\n\n"
        f"Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
    ))
    subprocess.run(["git", "push", "origin", branch], cwd=REPO_ROOT, check=True)

    # Open PR
    result = subprocess.run(
        ["gh", "pr", "create",
         "--base", "main",
         "--head", branch,
         "--title", f"chore: release v{new_version} — {release_title}",
         "--body", (
             f"Automated release PR generated by the zbx autonomous agent.\n\n"
             f"**Version:** `{current_version}` → `{new_version}`\n\n"
             f"### Changes\n\n{changelog_entry}\n\n"
             f"---\n_Merge this PR to publish v{new_version} to PyPI._"
         ),
         "--label", "ai-generated"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    pr_url = result.stdout.strip()
    print(f"  Release PR created: {pr_url}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"zbx Autonomous Agent — mode={AGENT_MODE} dry_run={DRY_RUN}")
    print(f"Repo: {REPO}")
    print("-" * 60)

    if AGENT_MODE in ("scan", "full"):
        scan_and_create_issues()

    if AGENT_MODE in ("issues", "full"):
        process_all_issues()

    if AGENT_MODE == "release":
        auto_release()

    print("\nAutonomous agent run complete.")


if __name__ == "__main__":
    main()
