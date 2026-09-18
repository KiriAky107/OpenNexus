# Contributing to OpenNexus

[简体中文](CONTRIBUTING.zh-CN.md) | **English**

Thank you for improving OpenNexus. This guide supplements the engineering, testing, Issue, and Pull Request requirements in [README.md](README.md).

## Before starting

1. Search existing Issues and Pull Requests.
2. Open or reference an Issue for behavior changes, architectural work, schema migrations, or cross-repository compatibility changes.
3. Confirm that the change belongs in this repository rather than the separate Sync Server or Community repository.
4. Never use a personal vault, production credential, private server, or identifying competition material as a committed fixture.

## Development workflow

1. Fork the repository or create a feature branch from the current `main` branch.
2. Install dependencies from the committed lock files.
3. Make one focused logical change and add or update tests.
4. Run the checks for every affected layer.
5. Update both README languages and release notes when public behavior changes.
6. Push the feature branch and open a Draft Pull Request if early design feedback is needed.

Suggested branch names:

```text
feat/agent-resume
fix/pdf-export-dialog
docs/community-standards
```

## Commit messages

Use Conventional Commits:

```text
<type>(<optional-scope>): <imperative summary>
```

Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, and `chore`. Keep authorship accurate; do not rewrite another contributor's identity without their explicit request.

## Engineering expectations

- Preserve the local-first model and the trust boundaries between WebView, Tauri host, AI Core, Vault, and optional remote services.
- Treat the Rust host as the authority for privileged desktop filesystem and credential operations.
- Keep persistent schema changes append-only through migrations and document recovery or rollback behavior.
- Keep APIs typed and synchronize frontend contracts, backend schemas, native commands, and tests.
- Preserve idempotency for file, media, sync, and extension transactions.
- Validate untrusted archives, Markdown, extension manifests, paths, URLs, model output, and remote responses.
- Explain new dependencies and verify their licenses and packaged size.

## Required checks

Use the commands documented in [README.md](README.md#testing). In the Pull Request, list only commands that were actually run and state any unavailable environment or skipped check.

Documentation-only changes must at least pass Markdown link, code-fence, and Mermaid parsing checks. UI, native-dialog, sidecar, export, installer, or synchronization changes require packaged-desktop acceptance where applicable.

## Documentation and privacy

- Keep English and Simplified Chinese shared documentation aligned.
- Use examples that contain fictitious users, domains, tokens, paths, and vault content.
- Remove credentials, personal information, private hostnames, school or employer information, and identifying competition material.
- Do not commit generated bundles, model weights, user databases, media recordings, or personal documents.

## Review

Reviewers evaluate correctness, security boundaries, migration safety, tests, accessibility, documentation, licensing, and release impact. Address review comments with new commits while review is active; maintainers may squash when merging.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Security reports follow [SECURITY.md](SECURITY.md).
