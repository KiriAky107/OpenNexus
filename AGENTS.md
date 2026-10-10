# Repository writing guide

## README style

Use OpenNexus's `README.md` and `README.zh-CN.md` as the editorial reference for this repository and its Sync and Community companions. Write for someone deciding whether to use the product, then for someone deploying or contributing to it.

- Open with a centered brand block: the existing logo, project name, a short product description, language switch, section navigation, and relevant version, CI, technology and license badges. Use the existing `flat-square` badge style. Badge links must resolve to the corresponding repository.
- Put the current published release and a concise update summary near the top. Explain concrete behavior and user benefits. Do not advertise an unpublished version or describe a planned feature as available.
- Follow with highlights, a working quick start, and task-oriented workflows. Give readers an actionable route from setup to a useful result before presenting implementation details.
- Put architecture, repository layout, development, packaging or deployment, security, contribution links and the license afterward. Adapt sections to each product; a service needs deployment steps rather than desktop installation steps.
- Use Markdown headings and short connected paragraphs. Use lists for parallel capabilities or numbered steps, and tables for responsibilities, requirements, configuration or package types. Separate blocks with blank lines.
- Feature lists may use a short bold capability name followed by a concrete explanation, as in the main README. Avoid turning every paragraph into a list of slogans. Preserve established branding without adding decorative emoji to technical instructions.
- Use Mermaid when a process, state transition or architecture is easier to understand visually. Diagrams must match implemented behavior. Screenshots must come from the actual application, include useful alt text and use repository-owned assets; do not invent previews.
- Keep `README.md` in English and `README.zh-CN.md` in Simplified Chinese. Preserve the same section order, facts, examples, requirements and feature coverage in both. Translate for natural reading, keeping product names, route names and commands exact.
- Prefer plain verbs and specific outcomes. Explain what the user selects, reviews, receives or recovers. Avoid exaggerated claims, canned transitions, forced contrasts, repeated disclaimers and conversational filler.
- Give commands in fenced blocks with the appropriate language. Use placeholders for credentials and user-controlled paths. Verify flags against the current CLI, and link directly to existing files and real release pages.
- State actual prerequisites, storage behavior, permissions and recovery requirements where they help the user act. Keep product versions separate from API versions. Do not infer end-to-end encryption, production readiness or successful verification from a plan.

For example, prefer “Review the current and historical content, then restore as a new revision” to “A powerful history experience that seamlessly protects every change.”

## Scope and delivery

Keep private plans, internal `docs`, local checklists, receipts and test data out of commits. Public README files, this writing guide and user-requested public documentation are separate deliverables. Do not repeatedly audit unchanged documentation tracking.

Group changes into meaningful commits and run checks appropriate to the affected behavior. Preserve user data, credentials, production instances and existing release backups. Never claim unfinished work or an unverified release as complete.

## Git workflow

- Name branches by engineering purpose: `feat/<scope>`, `fix/<scope>`, `perf/<scope>`, `refactor/<scope>`, `test/<scope>`, `docs/<scope>`, `chore/<scope>`, `release/<version>` or `hotfix/<scope>`. Use concise lowercase words separated by hyphens and a concrete scope or version.
- Never use `codex` or another agent, model or tool name as a branch prefix. This applies to local and remote branches in OpenNexus, Sync and Community.
- Keep `main` as the integration branch. Start a suitably named branch for each new task; do not reuse a completed release branch for unrelated work.
- Preserve commit identities when renaming branches. Keep release tags and packaged source pinned to their verified commits, even when later documentation commits advance a branch.
