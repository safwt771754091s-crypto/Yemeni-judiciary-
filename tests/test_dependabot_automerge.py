"""Guards for the Dependabot auto-merge setup.

The repository auto-merges Dependabot pull requests, but only after CI passes.
These checks pin the safety-critical parts of that wiring so a later edit cannot
silently turn it into "merge everything" or "merge without tests":

* Dependabot is configured for the ecosystems this repo actually uses;
* the auto-merge workflow is triggered by the ``CI`` workflow finishing, not by
  the pull request itself, and it only merges on a successful conclusion;
* it only touches pull requests authored by Dependabot.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
AUTO_MERGE = ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"


def test_dependabot_tracks_pip_and_actions() -> None:
    config = DEPENDABOT.read_text(encoding="utf-8")
    assert "version: 2" in config
    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: github-actions" in config


def test_auto_merge_is_gated_on_the_ci_workflow_result() -> None:
    workflow = AUTO_MERGE.read_text(encoding="utf-8")
    # Triggered by CI finishing, and only when it succeeded — "so long as the
    # tests are passing". A `pull_request` trigger would run before CI finishes.
    assert "workflow_run:" in workflow
    assert 'workflows: ["CI"]' in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'pull_request'" in workflow


def test_auto_merge_only_touches_dependabot_pull_requests() -> None:
    workflow = AUTO_MERGE.read_text(encoding="utf-8")
    # `gh pr list --json author` reports Dependabot as "app/dependabot", while
    # the REST API reports "dependabot[bot]". Both must be accepted, otherwise
    # the merge never fires (a regression that shipped once).
    assert 'author.login == \\"dependabot[bot]\\"' in workflow
    assert 'author.login == \\"app/dependabot\\"' in workflow
    # The branch prefix is what actually distinguishes Dependabot; a spoofed
    # author name alone must not be enough to get merged.
    assert 'startswith(\\"dependabot/\\")' in workflow
    assert "gh pr merge" in workflow and "--squash" in workflow
