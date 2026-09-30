# -*- coding: utf-8 -*-
"""Gate, suggestion comments, and check conclusion for the GitHub review."""

from __future__ import annotations

import argparse
import json

import pytest
from github_pr_review import (
    CHECK_NAME,
    REVIEW_KEYWORD,
    _cmd_publish,
    check_conclusion,
    concrete_replacement,
    fill_pull_request,
    gate_state,
    latest_check_runs,
    main,
    other_check_count,
    partition_actions,
    plan_comments,
    publish_review,
    render_comment,
    resolve_event,
    resolve_gate_sha,
    review_event,
    review_matches,
    review_payload,
    reviewed_sha_is_current,
    select_gate_sha,
    should_post,
    suggestion_errors,
    suggestion_replacement,
    summary_body,
    without_own_run,
)


def test_check_name_and_keyword():
    assert CHECK_NAME == "plaibook review"
    assert REVIEW_KEYWORD == "/plai-review"


def test_gate_waits_for_in_progress_and_blocks_on_failure():
    sha_runs = [
        {"name": "Test (Python 3.12)", "status": "completed", "conclusion": "success", "check_suite": {"id": 2}},
        {"name": "Analyze Python", "status": "in_progress", "conclusion": None, "check_suite": {"id": 3}},
        {"name": CHECK_NAME, "status": "completed", "conclusion": "failure", "check_suite": {"id": 1}},
    ]
    suites = [
        {"id": 1, "status": "completed", "conclusion": "failure"},
        {"id": 2, "status": "completed", "conclusion": "success"},
        {"id": 3, "status": "in_progress", "conclusion": None},
    ]
    assert gate_state(sha_runs, suites) == "waiting"
    sha_runs[1]["status"] = "completed"
    sha_runs[1]["conclusion"] = "failure"
    suites[2]["status"] = "completed"
    suites[2]["conclusion"] = "failure"
    assert gate_state(sha_runs, suites) == "failed"
    sha_runs[1]["conclusion"] = "success"
    suites[2]["conclusion"] = "success"
    assert gate_state(sha_runs, suites) == "passed"


def test_gate_ignores_the_in_progress_review_job():
    runs = [
        {"name": "Test (Python 3.12)", "status": "completed", "conclusion": "success", "check_suite": {"id": 2}},
        {
            "name": "plaibook review / plaibook review",
            "status": "in_progress",
            "conclusion": None,
            "check_suite": {"id": 9},
        },
    ]
    suites = [
        {"id": 2, "status": "completed", "conclusion": "success"},
        {"id": 9, "status": "in_progress", "conclusion": None},
    ]
    assert gate_state(runs, suites) == "passed"
    runs[1]["name"] = "CI / plaibook review"
    assert gate_state(runs, suites) == "passed"


def test_latest_check_runs_keeps_the_newest_attempt():
    runs = [
        {
            "name": "Scorecard analysis",
            "id": 1,
            "started_at": "2026-09-28T15:00:00Z",
            "status": "completed",
            "conclusion": "cancelled",
        },
        {
            "name": "Scorecard analysis",
            "id": 2,
            "started_at": "2026-09-28T15:10:00Z",
            "status": "completed",
            "conclusion": "success",
        },
        {"name": "CI", "id": 3, "started_at": "2026-09-28T15:00:00Z", "status": "completed", "conclusion": "success"},
    ]
    kept = latest_check_runs(runs)
    assert [(item["name"], item["id"]) for item in kept] == [("Scorecard analysis", 2), ("CI", 3)]


def test_gate_does_not_wait_on_an_empty_queued_suite():
    runs = [
        {"name": "Test (Python 3.12)", "status": "completed", "conclusion": "success", "check_suite": {"id": 2}},
        {
            "name": "plaibook review / plaibook review",
            "status": "in_progress",
            "conclusion": None,
            "check_suite": {"id": 9},
        },
    ]
    suites = [
        {"id": 2, "status": "completed", "conclusion": "success"},
        {"id": 9, "status": "in_progress", "conclusion": None},
        {"id": 3, "status": "queued", "conclusion": None},
    ]
    assert gate_state(runs, suites) == "passed"


def test_without_own_run_drops_the_current_actions_run():
    runs = [
        {
            "name": "Test (Python 3.12)",
            "status": "completed",
            "conclusion": "success",
            "check_suite": {"id": 2},
            "details_url": "https://github.com/acme/repo/actions/runs/4/job/1",
        },
        {
            "name": "plaibook review",
            "status": "in_progress",
            "check_suite": {"id": 9},
            "details_url": "https://github.com/acme/repo/actions/runs/9/job/2",
        },
    ]
    suites = [
        {"id": 2, "status": "completed", "conclusion": "success"},
        {"id": 9, "status": "in_progress", "conclusion": None},
    ]
    kept_runs, kept_suites = without_own_run(runs, suites, "9")
    assert [item["name"] for item in kept_runs] == ["Test (Python 3.12)"]
    assert [item["id"] for item in kept_suites] == [2]


def test_resolve_pull_request_uses_the_head_sha():
    event = {
        "pull_request": {
            "number": 79,
            "head": {"sha": "a" * 40, "repo": {"full_name": "aknochow/ansible-plaibook"}},
        }
    }
    resolved = resolve_event("pull_request", event, "aknochow/ansible-plaibook")
    assert resolved["action"] == "review"
    assert resolved["pr"] == "79"
    assert resolved["sha"] == "a" * 40
    assert resolved["trigger"] == "pull_request"


def test_resolve_skips_a_fork_pull_request():
    event = {
        "pull_request": {
            "number": 79,
            "head": {"sha": "a" * 40, "repo": {"full_name": "contributor/ansible-plaibook"}},
        }
    }
    resolved = resolve_event("pull_request", event, "aknochow/ansible-plaibook")
    assert resolved["action"] == "skip"


def _same_repo_pull(sha: str) -> dict:
    return {"head": {"sha": sha, "repo": {"full_name": "aknochow/ansible-plaibook"}}}


def test_fill_pull_request_skips_a_fork(monkeypatch):
    def fake_request(method, url, payload=None):
        assert method == "GET"
        assert url.endswith("/pulls/79")
        return 200, {"head": {"sha": "a" * 40, "repo": {"full_name": "contributor/ansible-plaibook"}}}, ""

    monkeypatch.setattr("github_pr_review._request", fake_request)
    resolved = fill_pull_request(
        {"action": "review", "repo": "aknochow/ansible-plaibook", "pr": "79", "sha": "", "trigger": "issue_comment"}
    )
    assert resolved["action"] == "skip"


def test_fill_pull_request_uses_the_same_repo_head(monkeypatch):
    sha = "b" * 40

    def fake_request(method, url, payload=None):
        return 200, _same_repo_pull(sha), ""

    monkeypatch.setattr("github_pr_review._request", fake_request)
    resolved = fill_pull_request(
        {"action": "review", "repo": "aknochow/ansible-plaibook", "pr": "79", "sha": "", "trigger": "issue_comment"}
    )
    assert resolved["action"] == "review"
    assert resolved["sha"] == sha


def test_fill_pull_request_keeps_the_event_sha(monkeypatch):
    event_sha = "a" * 40

    def fake_request(method, url, payload=None):
        return 200, _same_repo_pull("b" * 40), ""

    monkeypatch.setattr("github_pr_review._request", fake_request)
    resolved = fill_pull_request(
        {
            "action": "review",
            "repo": "aknochow/ansible-plaibook",
            "pr": "79",
            "sha": event_sha,
            "trigger": "pull_request",
        }
    )
    assert resolved["sha"] == event_sha


def test_fill_pull_request_records_the_merge_commit(monkeypatch):
    sha = "b" * 40
    merge = "c" * 40

    def fake_request(method, url, payload=None):
        pull = _same_repo_pull(sha)
        pull["merge_commit_sha"] = merge
        return 200, pull, ""

    monkeypatch.setattr("github_pr_review._request", fake_request)
    resolved = fill_pull_request(
        {"action": "review", "repo": "aknochow/ansible-plaibook", "pr": "79", "sha": "", "trigger": "issue_comment"}
    )
    assert resolved["sha"] == sha
    assert resolved["merge_sha"] == merge


def test_select_gate_sha_prefers_the_head_when_it_has_check_runs():
    head, merge = "a" * 40, "b" * 40
    assert select_gate_sha(head, 3, merge, 9) == head
    assert select_gate_sha(head, 0, merge, 2) == merge
    assert select_gate_sha(head, 0, "", 0) == head
    assert select_gate_sha(head, 0, merge, 0) == head


def test_resolve_gate_sha_skips_the_merge_commit_when_the_head_has_runs(monkeypatch):
    def fake_count(repo, sha, ignore_run_id=""):
        assert sha == "a" * 40
        return 4

    monkeypatch.setattr("github_pr_review.other_check_count", fake_count)
    chosen = resolve_gate_sha("aknochow/ansible-plaibook", "a" * 40, "b" * 40)
    assert chosen == "a" * 40


def test_omitted_replacement_stays_in_the_summary():
    finding = {
        "file": "a.py",
        "line": 1,
        "severity": "Major",
        "lens": "Functionality",
        "description": "The prompt drops findings that are not a drop-in edit.",
        "fix": "Report the finding and omit replacement.",
    }
    assert suggestion_errors(finding) == []
    actions = plan_comments([finding], [])
    assert actions[0]["op"] == "unanchored"
    assert "not a drop-in edit" in actions[0]["text"]
    body = summary_body(
        {"targets": [{"verdict": "NEEDS_CHANGES", "score": 80}]},
        actions
        + [
            {
                "op": "create",
                "path": "b.py",
                "line": 2,
                "body": "suggestion",
                "note": "**Minor** (Functionality) `b.py:2`\n\nHas a drop-in edit.",
                "suggested": True,
            }
        ],
    )
    assert "## Findings" in body
    assert "1. **Major** (Functionality) `a.py:1`" in body
    assert "\n\n2. **Minor** (Functionality) `b.py:2`" in body
    assert "*See the suggested fix below.*" in body


def test_other_check_count_ignores_plaibook_review_runs(monkeypatch):
    def fake_get_all(url, key):
        assert key == "check_runs"
        return [
            {"name": "plaibook review / plaibook review", "id": 1, "started_at": "t", "details_url": ""},
            {"name": "Test (Python 3.12)", "id": 2, "started_at": "t", "details_url": ""},
        ]

    monkeypatch.setattr("github_pr_review._get_all", fake_get_all)
    assert other_check_count("aknochow/ansible-plaibook", "a" * 40) == 1


def test_gate_does_not_pass_when_no_other_check_exists():
    runs = [{"name": CHECK_NAME, "status": "completed", "conclusion": "success", "check_suite": {"id": 1}}]
    suites = [{"id": 1, "status": "completed", "conclusion": "success"}]
    assert gate_state(runs, suites) == "waiting"


def test_replacement_becomes_a_committable_suggestion():
    finding = {
        "file": "app.py",
        "start_line": 3,
        "line": 4,
        "severity": "Minor",
        "lens": "Functionality",
        "description": "missing suffix",
        "evidence": "return name",
        "fix": "Also match a called-workflow suffix.",
        "replacement": "    return name.endswith(suffix)\n",
        "evidence_status": "verified",
    }
    assert suggestion_replacement(finding) == "    return name.endswith(suffix)"
    body = render_comment(finding)
    assert "```suggestion" in body
    assert "Also match" not in body.split("```suggestion", 1)[1].split("```", 1)[0]
    prose = dict(finding)
    prose["replacement"] = "Add a suffix check so external callers are recognized."
    assert suggestion_replacement(prose) is None
    assert suggestion_errors(prose)
    comments, _retire, _updates = partition_actions(
        [{"op": "create", "body": body, "path": "app.py", "line": 4, "start_line": 3}],
        rehome=False,
    )
    assert comments[0]["start_line"] == 3
    assert comments[0]["start_side"] == "RIGHT"
    assert comments[0]["side"] == "RIGHT"


def test_multiline_replacement_without_start_line_is_rejected():
    finding = {
        "file": "app.py",
        "line": 4,
        "severity": "Minor",
        "lens": "Functionality",
        "description": "missing suffix",
        "evidence": "return name",
        "fix": "Also match a called-workflow suffix.",
        "replacement": "    return name.endswith(suffix)\n    return False\n",
        "evidence_status": "verified",
    }
    assert any("start_line" in item for item in suggestion_errors(finding))
    ranged = dict(finding)
    ranged["start_line"] = 3
    body = render_comment(ranged)
    assert "```suggestion" in body
    assert "return False" in body.split("```suggestion", 1)[1]


def test_suggestion_versus_prose():
    assert concrete_replacement("Use a parameterized query.") is None
    assert concrete_replacement("safe = check(name)") == "safe = check(name)"
    fenced = "Replace the call.\n```python\nsafe = check(name)\n```\n"
    assert concrete_replacement(fenced) == "safe = check(name)"
    assert concrete_replacement("```\nkeep(...)\n```") is None


def test_comment_plan_updates_instead_of_stacking():
    finding = {
        "file": "app.py",
        "line": 3,
        "severity": "Major",
        "lens": "Quality",
        "description": "unchecked name",
        "evidence": "run(name)",
        "fix": "Check the name before running it.",
        "replacement": "run(check(name))\n",
        "evidence_status": "verified",
    }
    body = render_comment(finding)
    assert "```suggestion\nrun(check(name))\n```" in body
    assert "\nFix:\n" not in body
    existing = [{"id": 9, "body": body}]
    assert [item["op"] for item in plan_comments([finding], existing)] == ["skip"]
    updated = plan_comments([finding | {"replacement": "checked = run(name)\n"}], existing)
    assert updated[0]["op"] == "update"
    assert "```suggestion\nchecked = run(name)\n```" in updated[0]["body"]
    assert plan_comments([finding | {"evidence_status": "refuted"}], []) == []
    with pytest.raises(RuntimeError, match="not a GitHub suggestion"):
        plan_comments([finding | {"replacement": "Add a check."}], [])


def test_summary_includes_models_cost_and_tokens():
    result = {
        "agent_family": "cursor",
        "models": ["composer-2.5"],
        "agents_dispatched": 8,
        "cost_usd": 0.0345,
        "total_input_tokens": 10,
        "total_output_tokens": 2,
        "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 93.3}],
    }
    body = summary_body(result, [])
    assert "cursor · composer-2.5 · 8 agents · $0.0345 · 10 in / 2 out" in body
    assert "Score: 93.3" in body


def test_conclusion_and_posting():
    ready = {"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 96}]}
    assert check_conclusion(ready)[0] == "success"
    needs = {"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 40}]}
    assert check_conclusion(needs)[0] == "failure"
    assert should_post(needs) is True
    skipped = {"status": "ok", "targets": [{"verdict": "SKIPPED", "skip_reason": "ci"}]}
    assert should_post(skipped) is False
    assert check_conclusion(None)[0] == "failure"
    failed = {"status": "failed", "error": "a.py:1 needs replacement", "targets": []}
    assert "needs replacement" in check_conclusion(failed)[2]


def test_resolve_ignores_bots_and_non_pr_runs():
    push = {"workflow_run": {"event": "push"}}
    assert resolve_event("workflow_run", push, "aknochow/ansible-plaibook")["action"] == "skip"
    comment = {
        "issue": {"number": 8, "pull_request": {"url": "https://example"}},
        "comment": {
            "body": "/plai-review",
            "user": {"login": "dependabot[bot]", "type": "Bot"},
            "author_association": "NONE",
        },
    }
    assert resolve_event("issue_comment", comment, "aknochow/ansible-plaibook")["action"] == "skip"
    stranger = {
        "issue": {"number": 8, "pull_request": {"url": "https://example"}},
        "comment": {
            "body": "/plai-review",
            "user": {"login": "someone", "type": "User"},
            "author_association": "NONE",
        },
    }
    assert resolve_event("issue_comment", stranger, "aknochow/ansible-plaibook")["action"] == "skip"
    owner = {
        "issue": {"number": 8, "pull_request": {"url": "https://example"}},
        "comment": {
            "body": "/plai-review\n",
            "user": {"login": "aknochow", "type": "User"},
            "author_association": "OWNER",
        },
    }
    resolved = resolve_event("issue_comment", owner, "aknochow/ansible-plaibook")
    assert resolved["action"] == "review"
    assert resolved["pr"] == "8"


def test_review_event_requests_changes_for_needs_changes():
    assert review_event("NEEDS_CHANGES") == "REQUEST_CHANGES"
    assert review_event("READY_FOR_HUMAN_REVIEW") == "COMMENT"
    sha = "a" * 40
    summary = "plaibook review: **NEEDS_CHANGES**"
    assert review_matches({"body": summary, "state": "COMMENTED", "commit_id": sha}, summary, "COMMENT", sha)
    assert not review_matches(
        {"body": summary, "state": "COMMENTED", "commit_id": sha}, summary, "REQUEST_CHANGES", sha
    )


def test_partition_rehomes_existing_comments_into_one_review():
    actions = [
        {"op": "skip", "id": 9, "path": "a.yml", "line": 4, "body": "keep\n"},
        {"op": "create", "path": "b.yml", "line": 2, "body": "new\n"},
        {"op": "update", "id": 3, "path": "c.yml", "line": 1, "body": "edited\n"},
    ]
    comments, retire, updates = partition_actions(actions, rehome=True)
    assert [item["path"] for item in comments] == ["a.yml", "b.yml", "c.yml"]
    assert comments[0]["side"] == "RIGHT"
    assert retire == [9, 3]
    assert updates == []
    payload = review_payload("b" * 40, "summary", "REQUEST_CHANGES", comments)
    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["comments"] == comments
    kept, retire_none, updates = partition_actions(actions, rehome=False)
    assert [item["path"] for item in kept] == ["b.yml"]
    assert retire_none == []
    assert updates[0]["id"] == 3


def _finding(fix="Use a safer call.", replacement="token = scoped()\n"):
    return {
        "file": "app.yml",
        "line": 12,
        "severity": "Major",
        "replacement": replacement,
        "lens": "Security",
        "description": "token is in scope",
        "fix": fix,
        "evidence_status": "verified",
    }


def test_publish_skips_when_the_head_moved(monkeypatch, tmp_path, capsys):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps({"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 81.7, "findings": []}]}),
        encoding="utf-8",
    )

    def fake_request(method, url, payload=None):
        assert method == "GET"
        return 200, {"head": {"sha": "b" * 40}}, ""

    def refuse_publish(*_args, **_kwargs):
        raise AssertionError("published a stale review")

    monkeypatch.setattr("github_pr_review._request", fake_request)
    monkeypatch.setattr("github_pr_review.publish_review", refuse_publish)
    assert reviewed_sha_is_current("acme/repo", "9", "b" * 40) is True
    args = argparse.Namespace(
        result=str(result_path),
        review_rc=0,
        repo="acme/repo",
        pr="9",
        sha="a" * 40,
        check_id="",
    )
    assert _cmd_publish(args) == 0
    assert "head moved" in capsys.readouterr().err


def test_publish_posts_the_pipeline_error_when_there_are_no_findings(monkeypatch, capsys, tmp_path):
    calls = []
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "error": "a.py:1 needs replacement set to drop-in source",
                "targets": [],
            }
        ),
        encoding="utf-8",
    )

    def fake_request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "POST" and str(url).endswith("/reviews"):
            return 201, {"id": 3}, ""
        raise AssertionError((method, url))

    monkeypatch.setattr("github_pr_review._request", fake_request)
    args = argparse.Namespace(
        result=str(result_path),
        review_rc="2",
        repo="acme/repo",
        pr="9",
        sha="a" * 40,
        check_id="",
    )
    assert _cmd_publish(args) == 1
    assert calls[0][2]["event"] == "COMMENT"
    assert "needs replacement" in calls[0][2]["body"]
    assert "needs replacement" in capsys.readouterr().err


def test_publish_review_submits_one_changes_requested_review(monkeypatch):
    calls = []
    body = render_comment(_finding())

    def fake_request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/comments?per_page=100"):
            return 200, [{"id": 41, "body": body, "pull_request_review_id": 7, "path": "app.yml", "line": 12}], ""
        if method == "GET" and "/reviews?" in url:
            return 200, [{"id": 7, "body": "old", "state": "COMMENTED", "commit_id": "a" * 40}], ""
        if method == "POST" and url.endswith("/reviews"):
            return 201, {"id": 99, "state": "CHANGES_REQUESTED"}, ""
        if method == "DELETE":
            return 204, None, ""
        raise AssertionError((method, url))

    monkeypatch.setattr("github_pr_review._request", fake_request)
    result = {"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 81.7, "findings": [_finding()]}]}
    publish_review("acme/repo", "9", "b" * 40, result)
    posts = [item for item in calls if item[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1].endswith("/pulls/9/reviews")
    assert posts[0][2]["event"] == "REQUEST_CHANGES"
    assert posts[0][2]["comments"][0]["path"] == "app.yml"
    assert "```suggestion\ntoken = scoped()\n```" in posts[0][2]["comments"][0]["body"]
    assert [item[0] for item in calls if item[0] == "DELETE"] == ["DELETE"]
    assert calls[-1][1].endswith("/pulls/comments/41")


def test_publish_review_keeps_a_matching_changes_requested_review(monkeypatch):
    calls = []
    finding = _finding("safe = check(name)")
    body = render_comment(finding)
    sha = "c" * 40
    result = {"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 40, "findings": [finding]}]}
    existing = [{"id": 41, "body": body, "pull_request_review_id": 8}]
    review_body = summary_body(result, plan_comments([finding], existing))
    assert "1. **" in review_body
    assert "*See the suggested fix below.*" in review_body

    def fake_request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/comments?per_page=100"):
            return 200, [{"id": 41, "body": body, "pull_request_review_id": 8}], ""
        if method == "GET" and "/reviews?" in url:
            return 200, [{"id": 8, "body": review_body, "state": "CHANGES_REQUESTED", "commit_id": sha}], ""
        raise AssertionError((method, url))

    monkeypatch.setattr("github_pr_review._request", fake_request)
    result = {"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 40, "findings": [finding]}]}
    publish_review("acme/repo", "9", sha, result)
    assert [item[0] for item in calls] == ["GET", "GET"]
    assert "```suggestion" in body


def test_publish_review_drops_inline_comments_when_lines_are_rejected(monkeypatch):
    calls = []

    def fake_request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/comments?per_page=100"):
            return 200, [], ""
        if method == "GET" and "/reviews?" in url:
            return 200, [], ""
        if method == "POST" and payload and payload.get("comments"):
            return 422, "line is not part of the diff", ""
        if method == "POST":
            return 201, {"id": 2}, ""
        raise AssertionError((method, url, payload))

    monkeypatch.setattr("github_pr_review._request", fake_request)
    result = {"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES", "score": 40, "findings": [_finding()]}]}
    publish_review("acme/repo", "9", "d" * 40, result)
    posts = [item for item in calls if item[0] == "POST"]
    assert len(posts) == 2
    assert "comments" not in posts[1][2]
    assert posts[1][2]["event"] == "REQUEST_CHANGES"
    assert "```suggestion" in posts[1][2]["body"]
    assert "token = scoped()" in posts[1][2]["body"]
    assert not any(item[0] == "DELETE" for item in calls)


def test_conclude_fails_a_run_that_did_not_finish(tmp_path, capsys):
    path = tmp_path / "result.json"
    path.write_text(
        '{"status": "failed", "error": "a.py:1 needs replacement set to drop-in source (broken line)", "targets": []}',
        encoding="utf-8",
    )
    rc = main(["conclude", "--result", str(path), "--review-rc", "2"])
    assert rc == 1
    assert "broken line" in capsys.readouterr().err
