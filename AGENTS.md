# Repository Agent Rules

These instructions apply to the entire repository. User instructions always take precedence.

## Project boundaries

- This repository contains the maintainable source for LADA Agent, not a distributable Lada
  runtime, model files, personal media, or machine-local state.
- Preserve unrelated user changes in a dirty worktree. Never reset, discard, overwrite, or
  reformat unrelated files.
- Use `apply_patch` for hand-authored file changes. Use project tools only for generated,
  formatted, or build output.
- Prefer cross-platform behavior. The monitor and Web service must continue to work on a
  headless Linux server without `DISPLAY` or `WAYLAND_DISPLAY`.

## Git intent and publishing

- `commit` means create a local commit only.
- `push` means publish the requested commits directly to `origin/main` unless the user explicitly
  names another branch. It authorizes switching to local `main` only for this purpose, but does
  not authorize opening a pull request, merging a PR, tagging, or publishing a release.
- Before pushing `main`, fetch `origin`, fast-forward local `main`, and verify it is not behind or
  diverged. If the requested commits currently live on another branch, cherry-pick only those
  commits onto `main`; do not push the side branch as a substitute.
- Never create a pull request unless the user explicitly asks for a PR. Never invoke the
  `github:yeet` full publish workflow for an ordinary commit or push request.
- Do not create a branch for routine commit/push work. Stay on the current branch for local-only
  work; the `push` rule above is the explicit exception that permits switching to `main`.
- Never force-push, delete a remote branch, merge, tag, or create a GitHub Release without
  explicit authorization.
- Use local `git` for status, diff, staging, commits, and pushes. Use the GitHub connector for
  issues, pull requests, reviews, and repository metadata only when relevant to the request.
- Before a network write, restate the exact remote, branch, and action. Afterward, report the
  resulting commit SHA and remote branch.

## Mandatory privacy and secret check before staging, committing, or pushing

1. Inspect `git status -sb`, tracked changes, staged changes, and untracked files. Understand
   why every candidate file belongs to the requested change.
2. Review both the full diff and the staged diff. Prefer explicit paths with `git add -- <paths>`;
   do not use `git add -A` or `git add .` unless the user has confirmed the whole worktree.
3. Run an available secret scanner against candidate tracked content and the staged diff.
   Prefer `gitleaks git --pre-commit --redact --staged --verbose` when available. If it is
   unavailable, perform a targeted, redacted search for common credentials and private-key
   headers. Never print a discovered secret value into tool output or chat.
4. Treat these as private by default and do not stage them:
   - `.env`, `.env.*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, SSH keys, cloud credentials,
     `.git-credentials`, auth cookies, session databases, and token caches;
   - real `config/config.json`, `config/*.local.json`, `config/.web_session_secret`, lock files,
     logs, processing history, crash dumps, and files containing access tokens or passwords;
   - personal videos, downloads, model weights, the `cli/` runtime, `dist/`, build output, and
     other large generated artifacts unless the user explicitly requests their publication.
5. Confirm examples and documentation contain placeholders rather than usable credentials,
   private hostnames, personal paths, or identifying media names.
6. If a possible secret or private file is found, stop before commit/push. If it is staged,
   unstage only that path without deleting or modifying the user's file. Report the path and
   credential category, never the secret value, and ask the user how to proceed.
7. Never assume `.gitignore` is a privacy scanner: ignored files can already be tracked, and
   secrets can appear inside otherwise legitimate source or documentation files.

## Validation

- Run checks proportional to the change. For a normal source release, use:

  ```text
  uv lock --check --offline
  python -m pytest -q
  ruff check .
  ruff format --check .
  node --check src/lada_agent/static/app.js
  ```

- Run `uv build` when package metadata, package data, CLI entry points, or release contents
  change. Verify the resulting wheel contains the expected files.
- Do not claim Linux, CPU, CUDA, encoder, or Lada-version runtime support merely because unit
  tests pass. Distinguish source-contract checks, simulated tests, and real hardware smoke tests.
- Record skipped platforms and failed or timed-out smoke tests honestly.

## Lada integration

- Treat the installed Lada CLI as an external runtime. Discover its version, devices, models,
  presets, and encoders rather than maintaining stale hard-coded enumerations.
- Build subprocess argument arrays and execute without a shell. Keep Agent-owned input/output
  boundaries protected from advanced argument overrides.
- Keep global options, directory profiles, one-off profiles, command preview, and executed argv
  consistent.
- Smoke tests must be bounded by time, use temporary copies, validate output with `ffprobe`,
  clean up child processes and temporary files, and never alter the source video.

## Documentation and communication

- Update compatibility and usage documentation when command-line behavior or deployment
  support changes.
- Lead status reports with the outcome. Clearly separate implementation completion from real
  runtime acceptance.
- Do not expose secrets, tokens, private media paths, or authentication output in summaries,
  commit messages, pull-request bodies, logs, or screenshots.
