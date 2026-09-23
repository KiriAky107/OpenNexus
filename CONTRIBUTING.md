# Contributing to OpenNexus

[简体中文](CONTRIBUTING.zh-CN.md) | **English**

---

Thank you for your interest in building and improving OpenNexus. We welcome code contributions, documentation enhancements, architectural refinements, and reproducible bug reports from the community.

OpenNexus is a local-first, security-sensitive desktop environment combining a native Tauri host, a Python AI engine, and a reactive Vue frontend. To maintain system reliability, data sovereignty, and engineering velocity, all contributions must adhere to the engineering standards outlined below.

---

## 1. Ground Rules & Repository Boundaries

Before writing code or filing proposals, ensure your contribution is directed to the appropriate layer:

| Focus Area | Responsible Repository | Stack |
| --- | --- | --- |
| **Desktop App & AI Engine** | **[OpenNexus](https://github.com/KiriAky107/OpenNexus?utm_source=gemini)** *(This Repo)* | Tauri 2 (Rust), Vue 3, FastAPI |
| **Encrypted Sync Server** | [Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus?utm_source=gemini) | Independent service, PostgreSQL, S3 |
| **Extension Registry & Skills** | [Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus?utm_source=gemini) | Manifests, catalog distribution |

* **Issue First**: For significant architectural proposals, database schema migrations, contract modifications, or breaking changes, please [open an Issue](https://www.google.com/search?q=https://github.com/KiriAky107/OpenNexus/issues/new/choose&utm_source=gemini) to discuss design trade-offs before investing time in implementation.
* **Synthetic Data Only**: Never commit real user vaults, production API tokens, private IPs, or personal documents. All tests and fixtures must use synthetic, generated mock data.

---

## 2. Development Lifecycle

### Step 1: Branch Strategy

Create a dedicated feature branch off the latest `main` branch:

```bash
git checkout main
git pull origin main
git checkout -b <type>/<short-description>

```

* **Feature**: `feat/agent-checkpoint-resume`
* **Bug Fix**: `fix/pdf-export-timeout`
* **Performance**: `perf/hybrid-retrieval-rerank`
* **Documentation**: `docs/mcp-configuration-guide`

### Step 2: Toolchain Setup

Ensure you install dependencies strictly from the pinned lockfiles:

```powershell
# Setup backend dependencies
cd backend
uv sync --frozen

# Setup frontend dependencies
cd ..\frontend
corepack enable
corepack prepare pnpm@10.28.0 --activate
pnpm install --frozen-lockfile

```

### Step 3: Atomic Changes

Keep each Pull Request focused on a single logical objective. Separate architectural refactorings, automated formatting sweeps, and functional logic into distinct commits or PRs.

---

## 3. Engineering Tenets

Every contribution must align with our core design invariants:

### Local-First & Trust Boundaries

* **The Rust Host is Authoritative**: High-privilege actions (direct filesystem access outside sandbox scopes, OS credential storage, native dialogs, child process supervision) must reside in the Tauri host (`frontend/src-tauri/`).
* **The AI Core is an Isolated Engine**: The FastAPI backend acts as a supervised compute engine over an authenticated loopback channel. It must never expose raw credentials or assume unrestricted filesystem authority.
* **The Frontend is Untrusted**: The Vue WebView renders UI and issues structured commands. It must never hold raw provider API secrets in memory or `localStorage`.

### Type Synchronization & Contract Rigor

Whenever an IPC command or API payload changes, you must synchronously update:

1. Backend schemas (Pydantic models in `backend/`).
2. Rust command arguments and error enums (`src-tauri/src/`).
3. Frontend TypeScript interfaces (`frontend/src/`).
4. Unit/Integration contract tests.

### Idempotency & Persistence

* **Atomic Operations**: File edits, media processing jobs, and extension installations must be restart-safe and idempotent. Interrupted jobs must be cleanable or resumable without leaving orphaned state.
* **Append-Only Migrations**: Database changes in `app.db` or host SQLite databases must use structured migrations. Never execute destructive, non-recoverable modifications on user data schemas.

### Dependency Hygiene

Explain any proposed dependency in the PR description, including runtime performance impact, bundle size increase, license compatibility (MIT/Apache-2.0 preferred), and security posture.

---

## 4. Commit Standards

We enforce the [Conventional Commits](https://www.conventionalcommits.org/?utm_source=gemini) specification:

```text
<type>(<scope>): <short imperative summary>

[optional body: explanation of context, trade-offs, and design rationale]

[optional footer: Closes #123, Breaking Changes]

```

### Common Types

* `feat`: A new user-visible capability or developer API.
* `fix`: A bug fix or error mitigation.
* `perf`: A code change that improves compute, retrieval, or UI performance.
* `refactor`: Code restructurings that neither fix bugs nor add features.
* `test`: Adding missing tests or correcting existing tests.
* `docs`: Documentation updates or corrections.
* `build` / `ci`: Changes to build tooling, dependencies, or CI workflows.

### Examples

```text
feat(agent): persist checkpoint states during long-running tasks
fix(export): prevent crash when compiling math blocks to PDF
docs(setup): clarify Rust MSVC baseline requirement on Windows

```

---

## 5. Verification Matrix

Before requesting a review, run the quality gates corresponding to the layers you touched:

```powershell
# 1. AI Core & Backend Verification
cd backend
uv run pytest

# 2. Frontend Type Checking, Unit Tests, and Build
cd ..\frontend
pnpm type-check
pnpm test
pnpm build

# 3. Tauri / Rust Native Host Checks
cd src-tauri
cargo fmt --check
cargo test --all-targets --features desktop
cargo clippy --all-targets --features desktop -- -D warnings

```

* **Packaged Acceptance**: Changes affecting native file dialogs, sidecar lifecycle, PDF/DOCX exporters, or installer behavior must be verified via a local packaged build (`pnpm desktop:build`).
* **Documentation Checks**: Markdown files must maintain valid internal links, correct code blocks, and compliant Mermaid syntax.

---

## 6. Pull Request Submission & Review

When your changes are verified and ready:

1. **Open a PR**: Reference relevant Issues using GitHub keywords (`Closes #123`, `Fixes #456`).
2. **Fill the Template**: Clearly outline the *Problem*, *Solution*, and *Verification Evidence* (including terminal outputs or UI before/after recordings).
3. **Keep Docs in Sync**: If public behavior, CLI commands, or configuration keys change, update both `README.md` and `README.zh-CN.md`.
4. **Active Review**: Maintainers will review code for correctness, security boundaries, performance regressions, and architectural fit. Push new commits directly to your branch during the review cycle; commits will be squashed upon merge.

---

## 7. Security & Code of Conduct

* **Security Disclosures**: Never report security vulnerabilities or credentials via public PRs or Issues. Use [GitHub Private Vulnerability Reporting](https://www.google.com/search?q=https://github.com/KiriAky107/OpenNexus/security/advisories/new&utm_source=gemini).
* **Code of Conduct**: All participants are expected to adhere to our [Code of Conduct](https://www.google.com/search?q=CODE_OF_CONDUCT.md&utm_source=gemini). Please engage with respect, professional rigor, and constructive candor.
