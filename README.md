# OpenNexus

[简体中文](README.zh-CN.md) | **English**

[![Version](https://img.shields.io/badge/version-0.5.2--alpha1-5865f2)](https://github.com/KiriAky107/OpenNexus/releases/tag/v0.5.2-alpha1)
![Platform](https://img.shields.io/badge/platform-Windows%20x64-2563eb)
![Desktop](https://img.shields.io/badge/desktop-Tauri%202-f97316)
![Frontend](https://img.shields.io/badge/frontend-Vue%203-42b883)
![Core](https://img.shields.io/badge/core-FastAPI-05998b)
[![License](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

OpenNexus is a local-first AI notebook and knowledge workspace. It combines Markdown vaults, hybrid retrieval, grounded chat, auditable agents, media-to-notes workflows, an extension runtime, and optional multi-device synchronization in a Windows desktop application.

> The current release is **0.5.2-alpha1**. The project is usable for evaluation and demonstrations, but its storage schema and extension interfaces may still change. Back up important vaults before upgrading.

## Table of contents

- [Why OpenNexus](#why-opennexus)
- [Core workflows](#core-workflows)
- [Architecture](#architecture)
- [Data and persistence model](#data-and-persistence-model)
- [Repository boundaries](#repository-boundaries)
- [Installation](#installation)
- [Development](#development)
- [Configuration and data](#configuration-and-data)
- [Testing](#testing)
- [Packaging and release](#packaging-and-release)
- [Security and privacy](#security-and-privacy)
- [Community standards](#community-standards)
- [Contributing](#contributing)
- [License](#license)
- [Issue requirements](#issue-requirements)
- [Pull request requirements](#pull-request-requirements)

## Why OpenNexus

OpenNexus treats notes as portable files rather than records locked inside a hosted service. The desktop host owns privileged local operations, while the AI Core exposes a narrow local API for retrieval, generation, agents, transcription, and export. Remote services are optional and can be deployed independently.

The design focuses on four properties:

- **Local ownership:** notes remain ordinary Markdown files with relative, content-addressed attachments.
- **Traceable AI:** retrieved context, tool permissions, task states, and agent execution records remain inspectable.
- **Replaceable models:** local models and multiple remote providers can be selected without coupling the vault to one vendor.
- **Composable extensions:** Skills, Plugins, themes, and MCP servers use explicit manifests and permission review.

## Core workflows

### Course recordings to knowledge notes

Import real audio or video, produce timestamped transcript segments, correct the transcript, and generate a separate knowledge-point note. When the material requires it, generated notes may include code blocks, mathematical formulas, Mermaid diagrams, and function plots. Transcription and note generation are separate stages so that the source transcript remains reviewable.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Media UI
    participant Host as Tauri Host
    participant Core as AI Core
    participant ASR as Transcription Provider
    participant Model as Note Model
    participant Vault as Markdown Vault
    participant DB as app.db

    User->>UI: Select audio or video
    UI->>Host: Open native file picker
    Host->>Core: Register attachment and enqueue job
    Core->>DB: Persist media_jobs and media_events
    Core->>ASR: Transcribe with timestamps and speakers
    ASR-->>Core: Segments and recognized text
    Core->>DB: Save completed job and revision
    Core-->>UI: Stream replayable progress events
    User->>UI: Review and correct transcript
    UI->>Core: Save optimistic revision
    Core->>DB: Append media_revisions
    User->>UI: Generate transcript and knowledge note
    Core->>Model: Extract grounded course knowledge
    Model-->>Core: Markdown with optional code or diagrams
    Core->>Core: Validate Mermaid and function-plot blocks
    Core->>Host: Create two idempotent note artifacts
    Host->>Vault: Atomically write Markdown files
    Core->>DB: Link media_notes to created note IDs
    Core-->>UI: Return transcript note and knowledge note
```

### Personal planning agents

Create a goal-oriented Agent, let it produce a plan and actionable tasks, authorize only the tools it needs, and inspect the execution history. Task state is persisted so interrupted work can be diagnosed and resumed instead of silently disappearing.

```mermaid
stateDiagram-v2
    [*] --> queued: create run
    queued --> running: worker starts
    running --> waiting_permission: privileged tool needs approval
    waiting_permission --> running: permission granted
    waiting_permission --> cancelled: user rejects or cancels
    running --> completed: final result persisted
    running --> failed: model, tool, or timeout error
    running --> cancelled: cancellation requested
    queued --> cancelled: cancelled before start
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

Every transition is represented by a persisted run snapshot and ordered `agent_events`, allowing the UI to reconnect without treating an interrupted SSE connection as lost work.

### Knowledge workspace

- Edit Markdown with source and rich-writing modes.
- Index content with SQLite FTS5 and vector search.
- Combine retrieval results with reciprocal-rank fusion and optional reranking.
- Ask grounded questions against the selected vault.
- Export the current editor snapshot to PDF, HTML, or DOCX.
- Import Markdown and presentation material into reusable notes.

### Extensions and optional services

- Install Skills and Plugins at demonstration time instead of bundling them into a user vault.
- Connect MCP servers through supported transports and keep process lifecycle separate from the AI Core where appropriate.
- Apply community themes and packages only after reviewing their origin and requested permissions.
- Synchronize notes and attachments through the independently deployed Sync Server.

```mermaid
flowchart LR
    A[Package file or community URL] --> B[Stage archive]
    B --> C{Archive, manifest, hash and signer valid?}
    C -- No --> X[Reject and record reason]
    C -- Yes --> D[Prepare isolated package tree]
    D --> E[Calculate requested changes and permissions]
    E --> F{User confirms exact review fingerprint?}
    F -- No --> Y[Cancel without activation]
    F -- Yes --> G[Create extension transaction]
    G --> H[Atomically switch active slot]
    H --> I{Post-switch checks pass?}
    I -- Yes --> J[Commit receipt and active revision]
    I -- No --> K[Rollback previous state]
```

## Architecture

```mermaid
flowchart LR
    UI[Vue 3 desktop UI] --> HOST[Tauri 2 / Rust host]
    HOST --> VAULT[Local Markdown vault]
    HOST --> CORE[FastAPI AI Core]
    CORE --> INDEX[(SQLite / FTS5 / sqlite-vec)]
    CORE --> MODEL[Local or remote models]
    CORE --> EXT[Skills / Plugins / MCP]
    HOST <--> SYNC[Optional Sync Server]
    SYNC --> DB[(PostgreSQL)]
    SYNC --> OBJ[S3-compatible storage]
    COMMUNITY[Community prototype] --> EXT
```

| Layer | Responsibility | Trust boundary |
| --- | --- | --- |
| Vue frontend | Workspace, editor, search, chat, Agent, media, extension, and settings UI | No direct credential persistence |
| Tauri/Rust host | Filesystem access, native dialogs, process supervision, credential vault, and privileged commands | Desktop security boundary |
| FastAPI AI Core | Retrieval, model adapters, agents, transcription, indexing, and export orchestration | Authenticated local channel |
| Vault | Markdown notes, attachments, and local metadata | User-controlled directory |
| Optional services | Sync and community distribution | Separate repositories and deployment lifecycle |

The AI Core is supervised by the desktop host but is not the owner of unrelated MCP processes. Production behavior must not depend on development-only `stdio` assumptions.

### Desktop startup and authenticated local channel

```mermaid
sequenceDiagram
    autonumber
    participant UI as Vue WebView
    participant Host as Tauri Host
    participant Core as AI Core Sidecar
    participant Cred as Credential Vault
    participant Vault as Selected Vault

    Host->>Host: Acquire single-instance and state locks
    Host->>Core: Start matching packaged sidecar
    Core-->>Host: Bind loopback endpoint and health state
    Host->>Core: Establish authenticated local session
    Host->>Cred: Unlock provider credentials for this session
    Host->>Vault: Validate selected root and host database
    Host-->>UI: Expose narrow Tauri command surface
    UI->>Host: Request workspace or AI operation
    Host->>Core: Forward authorized request
    Core-->>Host: Structured result or replayable event
    Host-->>UI: Return sanitized response
```

The WebView does not receive raw provider secrets or unrestricted filesystem access. Native dialogs, selected-vault path checks, and sidecar version matching remain host responsibilities.

## Data and persistence model

OpenNexus deliberately separates user-authored content from rebuildable indexes and host transaction state. The following stores are related but are not one shared database:

```mermaid
flowchart TB
    subgraph UserData[User-selected Vault]
        MD[Markdown notes]
        ATT[Content-addressed attachments]
    end

    subgraph HostState[Tauri-managed state]
        HDB[(host.sqlite3)]
        EDB[(extensions.sqlite3)]
        HDB --> FILES[File identity, journal and outbox]
        HDB --> SYNCSTATE[Bindings, heads, inbox and conflicts]
        EDB --> EXTSTATE[Versions, trust and transactions]
    end

    subgraph CoreState[AI Core state]
        ADB[(app.db)]
        ADB --> SEARCH[Notes, blocks, FTS and vectors]
        ADB --> ACTIVITY[Tasks, Agents, media and chat]
    end

    MD -->|indexed projection| SEARCH
    ATT -->|metadata and links| ADB
    HDB -->|authorized host bridge| ADB
    EDB -->|active extension inventory| ADB
```

### AI Core database relationships

The diagram shows the principal migrated tables in `app.db`. FTS and vector tables are projections of `blocks`; `media_notes.note_id` is a cross-boundary logical reference because the Rust host is the authoritative writer for desktop Markdown files.

```mermaid
erDiagram
    NOTES ||--o{ BLOCKS : contains
    BLOCKS ||--o| BLOCKS_FTS : projects_to
    BLOCKS ||--o{ ROUTED_VECTORS : embeds_in
    NOTES o|--o{ TASKS : optionally_links
    AGENT_RUNS ||--o{ AGENT_EVENTS : emits
    MEDIA_JOBS ||--o{ MEDIA_EVENTS : emits
    MEDIA_JOBS ||--o{ MEDIA_REVISIONS : snapshots
    MEDIA_JOBS ||--o{ MEDIA_NOTES : produces
    CHAT_CONVERSATIONS ||--o{ CHAT_MESSAGES : contains
    CHAT_MESSAGES o|--o{ CHAT_MESSAGES : branches_from
    WORKSPACE_ASSETS ||--o{ WORKSPACE_ASSET_LINKS : referenced_by
    NOTES o|--o{ WORKSPACE_ASSET_LINKS : uses

    NOTES {
        string note_id PK
        string title
        string file_path UK
        string folder
        json tags
        datetime updated_at
    }
    BLOCKS {
        string block_id PK
        string note_id FK
        json heading_path
        int start_offset
        int end_offset
        string content_hash
        int position
        bool embedding_local_only
    }
    BLOCKS_FTS {
        string block_id
        string note_id
        string heading_path
        string content
    }
    ROUTED_VECTORS {
        string space_id PK
        string block_id PK, FK
        int dimensions PK
        json vector
    }
    TASKS {
        string task_id PK
        string note_id FK
        string status
        datetime due_at
        datetime updated_at
    }
    AGENT_RUNS {
        string run_id PK
        string status
        json run_json
        json request_json
        json config_snapshot_json
        datetime updated_at
    }
    AGENT_EVENTS {
        string run_id PK, FK
        int sequence PK
        string event
        json data_json
        datetime timestamp
    }
    MEDIA_JOBS {
        string job_id PK
        string status
        json job_json
        json request_json
        string idempotency_key UK
        string fingerprint
    }
    MEDIA_EVENTS {
        string job_id PK, FK
        int sequence PK
        string event
        json data_json
    }
    MEDIA_REVISIONS {
        string job_id PK, FK
        int revision PK
        json job_json
    }
    MEDIA_NOTES {
        string job_id PK, FK
        int revision PK
        string options_hash PK
        string note_id
    }
    CHAT_CONVERSATIONS {
        string conversation_id PK
        string title
        string active_leaf
        string active_response_id
        datetime updated_at
    }
    CHAT_MESSAGES {
        string message_id PK
        string conversation_id FK
        string parent_message_id
        int sequence
        string role
        string content
        json citations_json
        json tool_calls_json
    }
    WORKSPACE_ASSETS {
        string asset_id PK
        string path UK
        string content_hash UK
        string media_type
        int size
    }
    WORKSPACE_ASSET_LINKS {
        string asset_id PK, FK
        string note_id PK
        string note_path PK
        string source
    }
```

### Local sync state

The host uses an outbox/inbox model rather than allowing a remote service to write directly into the Vault:

```mermaid
sequenceDiagram
    autonumber
    participant Watcher as Vault observer
    participant HostDB as host.sqlite3
    participant Sync as Sync Server
    participant Resolver as Conflict resolver
    participant Vault as Markdown Vault

    Watcher->>HostDB: Record file identity and pending outbox operation
    HostDB->>Sync: Upload operation with base revision
    Sync-->>HostDB: Return remote revision or conflict
    alt accepted
        HostDB->>HostDB: Advance sync_heads and mark job complete
    else conflict
        HostDB->>Resolver: Persist local and remote variants
        Resolver->>Vault: Apply explicit keep-local, keep-remote, or copy choice
        Resolver->>HostDB: Record resolution and retry operation
    end
    Sync-->>HostDB: Download ordered remote inbox entries
    HostDB->>Vault: Apply journaled, idempotent file operation
    HostDB->>HostDB: Advance cursor only after durable completion
```

## Repository boundaries

| Path | Purpose |
| --- | --- |
| `frontend/` | Vue 3 application and the Tauri/Rust desktop host |
| `frontend/src-tauri/` | Native commands, capabilities, sidecar supervision, and NSIS bundle configuration |
| `backend/` | FastAPI AI Core, retrieval, agents, media processing, model adapters, and export |
| `backend/tests/` | Backend unit and integration tests |
| `scripts/` | Build, acceptance, verification, and release automation |
| `tools/` | Development and packaging utilities |

Public components are maintained separately:

| Component | Repository |
| --- | --- |
| Desktop application and AI Core | [KiriAky107/OpenNexus](https://github.com/KiriAky107/OpenNexus) |
| Sync Server | [KiriAky107/Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus) |
| Community prototype | [KiriAky107/Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus) |

This repository must not absorb Sync Server or community deployment code. Cross-repository changes should document compatible versions and be released independently.

## Installation

### Windows installer

1. Download `OpenNexus_0.5.2-alpha1_x64-setup.exe` from the [GitHub release](https://github.com/KiriAky107/OpenNexus/releases/tag/v0.5.2-alpha1).
2. Verify its SHA-256 checksum:

   ```powershell
   Get-FileHash .\OpenNexus_0.5.2-alpha1_x64-setup.exe -Algorithm SHA256
   ```

   Expected value:

   ```text
   F1B88FDF1D3AE0B48B3D2907818A52B843E39BF94E94B4A913EC6AFD26EC8786
   ```

3. Run the installer, launch OpenNexus from the Start menu, and select or create a Markdown vault.
4. Open **Settings → Model providers** and configure a local or remote provider.

The installer does not contain a user vault, downloaded model weights, a CUDA runtime, or preinstalled community packages. An in-place upgrade keeps the current Windows user's application data under `%APPDATA%\cc.kronecker.notesagent`.

## Development

### Prerequisites

| Tool | Supported baseline |
| --- | --- |
| Windows | Windows 10/11 x64 |
| Node.js | 22 or later |
| pnpm | 10.28.0 |
| Python | 3.11 or later |
| uv | 0.9.24 or compatible |
| Rust | Current stable toolchain with MSVC target |
| WebView2 | Current Microsoft Edge WebView2 runtime |

Media processing, local inference, and some export paths may require additional runtimes. Install them only for the feature under test; do not commit downloaded models or runtime archives.

### Clone and install dependencies

```powershell
git clone https://github.com/KiriAky107/OpenNexus.git
cd OpenNexus

cd backend
uv sync --frozen

cd ..\frontend
corepack enable
corepack prepare pnpm@10.28.0 --activate
pnpm install --frozen-lockfile
```

Dependency lock files are part of the build contract. Update them in the same pull request as the corresponding manifest change.

### Run the development stack

```powershell
# Terminal 1: AI Core
cd backend
uv run python scripts/dev-server.py

# Terminal 2: web frontend
cd frontend
pnpm dev
```

For desktop integration work, run the Tauri development command from `frontend/` with the required sidecar artifacts available. The packaged application must be tested separately because browser-only development mode does not cover native dialogs, credential storage, sidecar startup, or installer paths.

### Useful frontend commands

```powershell
cd frontend
pnpm type-check
pnpm test
pnpm build
pnpm build:report
```

## Configuration and data

- A vault is a user-selected directory and is not part of the application repository.
- Provider secrets belong in the desktop credential vault; never place API keys in source files, screenshots, logs, fixtures, or `localStorage`.
- Indexes and generated caches can be rebuilt and must not be treated as the source of truth for notes.
- Remote synchronization is opt-in. Review the endpoint, TLS configuration, device name, and selected sync categories before signing in.
- Extension packages are untrusted input until their manifests, checksums, permissions, and executable contents have been reviewed.
- Logs used in bug reports must be redacted. Preserve correlation IDs where useful, but remove vault content, credentials, personal paths, hostnames, and personal information.

## Testing

Run the checks relevant to the changed layer before opening a pull request:

```powershell
# Backend tests
cd backend
uv run pytest

# Frontend tests and production build
cd ..\frontend
pnpm test
pnpm type-check
pnpm build

# Native host checks
cd src-tauri
cargo fmt --check
cargo test --all-targets --features desktop
cargo clippy --all-targets --features desktop -- -D warnings
```

For changes that cross process or repository boundaries, also run the applicable scripts under `scripts/` and document the acceptance scenario. Release candidates should cover startup, vault selection, provider restart recovery, transcript-to-note generation, Agent planning, extension installation, native save dialogs, PDF/HTML/DOCX export, and optional sync authentication.

Tests must be deterministic, must not depend on a contributor's personal vault or credentials, and must clean up temporary processes and files.

## Packaging and release

Build the Windows NSIS installer from `frontend/`:

```powershell
pnpm desktop:build
```

The desktop build runs the production frontend build and uses `frontend/src-tauri/tauri.bundle.conf.json`. Before publishing a release:

1. Synchronize versions in the frontend package, Tauri configuration, Rust package, and Python package metadata.
2. Build the AI Core sidecar and verify that the packaged desktop host starts the matching artifact.
3. Run frontend, backend, Rust, and release acceptance checks.
4. Install the generated setup executable on a clean Windows user profile.
5. Verify native file dialogs and PDF, HTML, and DOCX exports from the installed application.
6. Calculate and publish SHA-256 checksums.
7. Create an annotated version tag and a non-draft GitHub Release.
8. Keep source archives free of vaults, credentials, personal documents, generated caches, and unrelated service repositories.

Version tags use the `v<version>` form. Alpha versions can be published as normal releases when that status is intentional, but their compatibility limitations must remain clear in the release notes.

## Security and privacy

- Vault content stays local unless the user explicitly enables a remote model, synchronization, or another network integration.
- Provider credentials are handled by the desktop credential vault and must not be persisted by the WebView.
- Extension permissions, network access, and privileged tool calls require explicit review and authorization.
- Native commands must validate paths and must not broaden filesystem access beyond the selected vault or explicit user action.
- Only install Skills, Plugins, themes, and MCP servers from sources you trust.

Do not publish exploitable security details or real secrets in a public issue. Use a private maintainer contact or GitHub's private vulnerability reporting when it is enabled.

## Community standards

OpenNexus uses repository-level community files so expectations are visible before a contribution is submitted:

| Document | Purpose |
| --- | --- |
| [Code of Conduct](CODE_OF_CONDUCT.md) | Participation and moderation expectations |
| [Contributing Guide](CONTRIBUTING.md) | Branch, commit, engineering, testing, documentation, and review workflow |
| [Security Policy](SECURITY.md) | Supported versions and private vulnerability reporting |
| [Bug report form](.github/ISSUE_TEMPLATE/bug_report.yml) | Required reproducibility and redaction fields |
| [Feature request form](.github/ISSUE_TEMPLATE/feature_request.yml) | Problem, outcome, component, and impact analysis |
| [Pull Request template](.github/PULL_REQUEST_TEMPLATE.md) | Verification evidence and reviewer checklist |

```mermaid
flowchart TD
    START[Question, defect, proposal, or vulnerability] --> KIND{What kind of report?}
    KIND -- Usage question --> DISCUSS[Search README and existing Issues]
    KIND -- Reproducible defect --> BUG[Complete Bug report form]
    KIND -- Scoped enhancement --> FEATURE[Complete Feature request form]
    KIND -- Unpatched vulnerability --> PRIVATE[Use private security reporting]
    BUG --> TRIAGE[Maintainer triage and repository routing]
    FEATURE --> TRIAGE
    TRIAGE --> ISSUE[Accepted Issue with scope and acceptance criteria]
    ISSUE --> BRANCH[Focused feature branch]
    BRANCH --> CHECKS[Tests, docs, privacy and license checks]
    CHECKS --> PR[Pull Request template and review]
    PR --> MERGE{Requirements satisfied?}
    MERGE -- No --> BRANCH
    MERGE -- Yes --> MAIN[Merge to main and include in release process]
```

Sync Server implementation reports belong in [Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus/issues). Community catalog, package, and prototype reports belong in [Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus/issues).

## Contributing

- Keep changes focused on one problem and respect the repository boundaries above.
- Keep frontend types, backend schemas, native commands, and tests synchronized when an interface changes.
- Use locked dependencies and explain new runtime dependencies, their licenses, and their packaging impact.
- Use Conventional Commits, for example:

  ```text
  feat(agent): persist interrupted planning tasks
  fix(export): restore PDF rendering in packaged builds
  test(media): cover transcript-to-note generation
  docs(readme): clarify Windows release verification
  ```

- Update both `README.md` and `README.zh-CN.md` when shared documentation changes.
- Do not commit user vaults, personal documents, credentials, model weights, build output, or identifying competition material.

## License

OpenNexus project code is licensed under the [MIT License](LICENSE). Bundled or downloaded models, libraries, fonts, icons, and other third-party components remain subject to their own licenses and notices. The project MIT License does not replace those terms.

## Issue requirements

Before opening an issue:

1. Search open and closed issues and confirm that the problem is not already tracked.
2. Use a specific title and identify whether the report is a bug, feature request, documentation problem, or compatibility question.
3. For bugs, include the OpenNexus version or commit, Windows version, installation type, affected component, expected result, actual result, and the smallest reproducible sequence.
4. Attach only the minimum necessary screenshots or logs. Redact API keys, tokens, vault text, personal paths, account details, hostnames, and personal information.
5. State whether the problem reproduces in the packaged desktop application, web development mode, or both.
6. Include relevant model/provider, extension, MCP transport, and Sync Server versions without exposing credentials.
7. Security vulnerabilities and leaked secrets must be reported privately, not through a public issue.

Issues that only say “does not work,” omit reproducible information, duplicate an existing report, or expose private data may be closed until corrected.

## Pull request requirements

A pull request must:

1. Reference the related issue or clearly explain the motivation and user-visible outcome.
2. Contain one reviewable logical change; unrelated refactors and generated files must be separated.
3. Use a feature branch and Conventional Commits. Do not rewrite other contributors' branches or force-update the default branch.
4. Include tests for behavior changes and list the exact commands and acceptance scenarios that passed.
5. Update both language versions of shared documentation and update release notes when behavior, configuration, or compatibility changes.
6. Document frontend/backend/native contract changes, data migrations, rollback behavior, and cross-repository version requirements.
7. Explain every new dependency, including its purpose, license, runtime size, and installer impact.
8. Include before/after screenshots for UI changes and redact all personal or sensitive information.
9. Exclude credentials, private vaults, personal documents, downloaded models, build artifacts, and competition-identifying material.
10. Pass formatting, type checking, tests, production builds, and applicable packaged-desktop acceptance checks before requesting review.

Draft pull requests are welcome for early technical discussion. Mark the pull request ready only when the checklist is complete and the branch can be reviewed without access to private infrastructure or personal data.
