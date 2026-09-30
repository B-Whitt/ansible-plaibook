---
type: Guide
title: GitHub plaibook review check
description: The pull-request check that runs plai review after the other checks pass and posts one review.
tags: [github-actions, review]
status: stable
---

# GitHub plaibook review check

The check name GitHub reports for this repository is **`plaibook review / plaibook review`**. The caller job and the called job are both named `plaibook review`, and a reusable workflow qualifies the called job with the caller job's name. Branch protection here should require that qualified name. An external caller must require the name produced by its own caller job, `<caller job name> / plaibook review`.

It runs on the GitHub-hosted runner. It does not call an Automation Controller. `post_results` stays `false`. The workflow posts the pull-request review itself.

The review starts on the pull request and waits until the other checks on that commit have passed. A failed check stays failed. A green `plaibook review` does not cover it. `skipped` and `neutral` checks do not block the review. In-progress checks do. The workflow job is the check. It does not call the Checks API.

The workflow runs:

```bash
plai review org/repo/N --json --force -e review_require_ci_passing=false
```

The GitHub-hosted runner installs OpenShell v0.1.2 from the installer at commit `6648bd0c290efbc41ba131ee9831ee45cd431f94`, after checking its sha256. That installer starts a local gateway. The review sandbox image is `ghcr.io/nvidia/openshell-community/sandboxes/base` pinned by digest. That image embeds a policy the local gateway cannot activate: the sandbox stays in `ConfigurationInvalid` until the 300-second repair window expires. The workflow sets a gateway-global filesystem policy with no network rules, which replaces the image policy, then creates one sandbox from that digest and deletes it. `plai review` uses the same digest and waits up to 600 seconds for it to become ready. Model calls stay on the controller, so the guest does not need network rules. `review_require_ci_passing=false` is set because this workflow already required the other checks to pass, and a previous `plaibook review` failure must not skip the re-run. The product default for `post_results` and for `review_require_ci_passing` is unchanged.

`NEEDS_CHANGES` is submitted as one pull request review with event `REQUEST_CHANGES`. `READY_FOR_HUMAN_REVIEW` is submitted as one review with event `COMMENT`. The workflow does not approve. Inline comments are part of that review. Python renders each comment from the finding fields. The agent does not write the comment body. Every posted finding that sets `replacement` must set it to the exact new source for `start_line` through `line`. That comment contains one `suggestion` block, and the Files changed tab offers **Commit suggestion**. A finding whose fix is not one contiguous edit omits `replacement`. The review summary lists every finding as a numbered Markdown list, with a blank line before the next item. A finding that also has a suggestion block says to see the suggested fix below. A replacement that is prose, or a multi-line replacement without `start_line`, fails the playbook. A later run on the same commit does not post a second review when that summary and review state are already present. It updates a comment that has the same `<!-- plaibook-finding:... -->` marker instead of stacking a copy.

The GitHub App slug is `plai-review`. The review author is `plai-review[bot]`. Grant the app Pull requests write, install it on the repository, and set the Actions secrets `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY`. The private key is used only to mint a short-lived installation token for the publish step, after the agent has finished. Checkout and the check gate keep using the read-only `GITHUB_TOKEN`.

Re-run the review with a pull-request comment that starts with `/plai-review`, or with `workflow_dispatch` and the pull request number. That posts another review. It does not update the `plaibook review` check on the pull request head. That check is the job started by `pull_request`. Comments from bots, and from users who are not OWNER, MEMBER, or COLLABORATOR, are ignored.

The gate reads check runs on the pull request head and on the merge commit. When both commits have other checks, both must pass. In-progress checks wait. A commit with no other checks passes, and the review still runs. The review is published against the head SHA.

The job token is `contents: read`, `pull-requests: read`, and `checks: read`. `checks: read` is what the gate uses to list check runs on a private repository. The token cannot write the pull request. The publish step runs after the agent and posts with the GitHub App installation token. The review step installs OpenShell and uses the default sandbox. `CURSOR_API_KEY` is present in the review step because the Cursor SDK call runs on the controller. `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY` are required to post.

The review runs as cursor. Each repository sets the Actions secret `CURSOR_API_KEY`. If it is missing, the check fails. It does not succeed when no review ran.

The check is `success` only when the run-scoped result has one target and the verdict is `READY_FOR_HUMAN_REVIEW`. `NEEDS_CHANGES` still posts the review and fails the check.

Callers in other repositories pin the reusable workflow to a full commit SHA. The caller grants read access. The called job does not raise it. The app secrets post the review.

```yaml
jobs:
  review:
    permissions:
      contents: read
      pull-requests: read
      checks: read
    uses: aknochow/ansible-plaibook/.github/workflows/plai-review-run.yml@<40-character-sha>
    secrets:
      cursor_api_key: ${{ secrets.CURSOR_API_KEY }}
      github_app_id: ${{ secrets.PLAI_GITHUB_APP_ID }}
      github_app_private_key: ${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}
```

Callers do not pass a repository or a ref. The reusable workflow checks out `aknochow/ansible-plaibook` at commit `f648a1f16e53545c385b3772f9eb3824a6fd8c17`. This repository calls the workflow at commit `40d6c02af5b5f1f8999a28751f0bfa841352e4e2`. The review job installs that tree with `uv sync --locked`. `GITHUB_SHA` on `pull_request` is the merge commit and is not the tools pin. When the pull request base already contains the OpenShell 0.1 client, plaibook is installed from that base commit. The publisher that receives the GitHub App private key is the copy taken before `plai review` starts.

Without the GitHub App secrets the publish step fails. The job token cannot post the review.

Do not pass that secret to a `uses:` ref of `@main`. This repository calls the workflow at a full commit SHA. This change does not edit branch protection. After it is on the default branch, require the check name `plaibook review / plaibook review`. The GitHub App slug is `plai-review`.
