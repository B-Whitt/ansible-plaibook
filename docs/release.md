---
type: Guide
title: Release
description: How a plaibook version becomes a GitHub Release, a PyPI upload, and an execution-environment image.
tags: [release, pypi, execution-environment]
status: stable
---

# Release

The package version stays in four places: `pyproject.toml`,
`plaibook/__init__.py`,
`.claude/skills/ansible-plaibook-review/SKILL.md`, and the
`ghcr.io/aknochow/plaibook-ee:<version>` example in the README.
`tests/test_version_alignment.yml` fails when those strings disagree.

## Bump

Run **Open a release pull request** (`release-bump.yml`) from `main`.
The input is `patch`, `minor`, `major`, or an explicit `X.Y.Z`.
The workflow writes that version, runs the alignment playbook, and
opens `chore/release-<version>`. It does not tag.

The pull request is opened with `GITHUB_TOKEN`, so it does not start
other workflows. The alignment playbook already ran in the bump job.

## Cut

After that pull request is on `main`, run **Cut a GitHub release**
(`release-cut.yml`). It reads the version on `main`, stops if
`v<version>` already exists, and creates that tag plus a
non-prerelease Release on the current `main` commit.

The job uses the `release` environment. `RELEASE_GITHUB_APP_ID` and
`RELEASE_GITHUB_APP_PRIVATE_KEY` must be set there. The workflow
fails if either secret is unset. It does not use `GITHUB_TOKEN`,
because a release created with that token does not start
`publish.yml` or `ee-publish.yml`.

## Publish

`publish.yml` runs on `release: published`. It skips prereleases,
requires the tag to be `v` plus `pyproject.toml`'s version, and
uploads from the `pypi` environment. That environment stays the
upload gate. `ee-publish.yml` publishes
`ghcr.io/aknochow/plaibook-ee:<version>` from the same release.

Pinning the review Action to the new tag is a separate pull request
after the tag exists. The release workflows do not change that pin.
