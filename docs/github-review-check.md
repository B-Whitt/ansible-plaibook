---
type: Guide
title: GitHub plaibook review check
description: The pull-request check that runs plai review after the other checks pass and posts one review.
tags: [github-actions, review]
status: stable
---

# GitHub plaibook review check

The check name to require in branch protection is **`plaibook review`**.

It runs on the GitHub-hosted runner. It does not call an Automation Controller. `post_results` stays `false`. The workflow posts the pull-request review itself.

The review starts on the pull request and waits until the other checks on that commit have passed. A failed check stays failed. A green `plaibook review` does not cover it. `skipped` and `neutral` checks do not block the review. In-progress checks do. The workflow job is the check. It does not call the Checks API.

The workflow runs:

```bash
plai review org/repo/N --json --force -e review_require_ci_passing=false
```

The GitHub-hosted runner installs OpenShell first. That installer starts a local gateway. The next step creates one sandbox from `ghcr.io/nvidia/openshell-community/sandboxes/base:latest` (the `sandbox_image` in `review.yml`) and deletes it, so that image is already in the local store. The first pull is about 1 GiB and does not finish inside the playbook's 300-second ready wait. `plai review` then uses the default sandbox and waits up to 600 seconds for it to become ready. `review_require_ci_passing=false` is set because this workflow already required the other checks to pass, and a previous `plaibook review` failure must not skip the re-run. The product default for `post_results` and for `review_require_ci_passing` is unchanged.

`NEEDS_CHANGES` is submitted as one pull request review with event `REQUEST_CHANGES`. `READY_FOR_HUMAN_REVIEW` is submitted as one review with event `COMMENT`. The workflow does not approve. Inline comments are part of that review. Python renders each comment from the finding fields. The agent does not write the comment body. Every posted finding that sets `replacement` must set it to the exact new source for `start_line` through `line`. That comment contains one `suggestion` block, and the Files changed tab offers **Commit suggestion**. A finding whose fix is not one contiguous edit omits `replacement`. The review summary lists every finding as a numbered Markdown list, with a blank line before the next item. A finding that also has a suggestion block says to see the suggested fix below. A replacement that is prose, or a multi-line replacement without `start_line`, fails the playbook. A later run on the same commit does not post a second review when that summary and review state are already present. It updates a comment that has the same `<!-- plaibook-finding:... -->` marker instead of stacking a copy.

The review author is `plai-review[bot]`. Grant the app Pull requests write, install it on the repository, and set the Actions secrets `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY`. The private key is used only to mint a short-lived installation token for the publish step, after the agent has finished. Checkout and the check gate keep using the read-only `GITHUB_TOKEN`.

Re-run the review with a pull-request comment that starts with `/plai-review`, or with `workflow_dispatch` and the pull request number. That posts another review. It does not update the `plaibook review` check on the pull request head. That check is the job started by `pull_request`. Comments from bots, and from users who are not OWNER, MEMBER, or COLLABORATOR, are ignored.

The gate reads check runs for the pull request head SHA when that commit has other check runs. GitHub attaches `pull_request` checks to that head commit in this repository. The merge commit has none. The gate uses the merge commit only when the head has no other check runs and the merge commit does. The review is still published against the head SHA.

The job token is `contents: read` and `pull-requests: read`. It cannot write the pull request. The publish step runs after the agent and posts with the GitHub App installation token. The review step installs OpenShell and uses the default sandbox. `CURSOR_API_KEY` is present in the review step because the Cursor SDK call runs on the controller. `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY` are required to post.

The review runs as cursor. Each repository sets the Actions secret `CURSOR_API_KEY`. If it is missing, the check fails. It does not succeed when no review ran.

The check is `success` only when the run-scoped result has one target and the verdict is `READY_FOR_HUMAN_REVIEW`. `NEEDS_CHANGES` still posts the review and fails the check.

Callers in other repositories pin the reusable workflow to a full commit SHA. The caller grants read access. The called job does not raise it. The app secrets post the review.

```yaml
jobs:
  review:
    permissions:
      contents: read
      pull-requests: read
    uses: aknochow/ansible-plaibook/.github/workflows/plai-review-run.yml@<40-character-sha>
    with:
      source_repository: aknochow/ansible-plaibook
      source_sha: <40-character-sha>
    secrets:
      cursor_api_key: ${{ secrets.CURSOR_API_KEY }}
      github_app_id: ${{ secrets.PLAI_GITHUB_APP_ID }}
      github_app_private_key: ${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}
```

Pass `source_repository` and `source_sha` from a caller in another repository. Those inputs are the checkout ref. `github.workflow_sha` is the caller commit, so an external pin must not use it. This repository calls the workflow by path and leaves the inputs empty. On github.com, `job.workflow_sha` is that path's commit.

Without the GitHub App secrets the publish step fails. The job token cannot post the review.

Do not pass that secret to a `uses:` ref of `@main`. This repository calls the workflow with a same-repository path. The workflow runs from the pull request, so this change does not have to be on the default branch before the first review. This change does not edit branch protection. After it is on the default branch, require the check name `plaibook review`.
