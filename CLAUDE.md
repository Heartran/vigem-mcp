# Repository Guidelines

Template developed by [Heartran](https://github.com/heartran)

## Project Structure

- `vigem_server.py`: MCP server for virtual gamepad emulation via ViGEm Bus Driver + vgamepad.
- `manifest.json`: Claude Desktop MCP extension manifest.
- `requirements.txt`: Python dependencies.
- `settings.json`: MCP server settings/configuration.

## Build & Development

- Python 3.12+ required. Install deps: `pip install -r requirements.txt`.
- The server runs as an MCP extension for Claude Desktop; no standalone dev server command.
- ViGEm Bus Driver must be installed and device node must exist for vgamepad to work.

## Coding Style & Naming Conventions

- Default to 4-space indentation in Python, UTF-8 text, and trailing newlines. Use PascalCase for classes, camelCase for MCP tool names, snake_case for functions/variables, kebab-case for asset filenames.

- Add type hints to function signatures. Keep docstrings concise.

## Testing Guidelines

- Keep tests deterministic; mock vgamepad/ViGEm interactions where possible.
- Document manual verification steps for gamepad features.

## Commit & Pull Request Guidelines

- Use short, imperative commit messages (e.g., `Add trigger support`, `Fix lazy import crash`). Keep changes scoped and commit frequently.

- PRs should include intent, key changes, and testing performed; link related tasks/issues.

- All commits should be done using **your own git identity**.

- Do not work directly on `main`: create a dedicated branch with agent prefix before committing or pushing.
  - Examples: `claude/feature-name`, `codex/fix-description`, `gemini/refactor-module`

- Never delete branches (no `--delete-branch` on merges) unless explicitly instructed.

- **Merge policy (MANDATORY for all agents): unless explicitly specified otherwise by the user, the merge target is ALWAYS `main`.**
  - If the target branch is not written clearly in the request, assume `main`.
  - Do not infer a different merge target from recent context/history.

## Identity & Git Hygiene

- Author/committer identity is managed by the repo owner; do not change git config locally (no `git config` commands). Use the existing configuration as-is. Use $ENV variables for agent-specific commits.

- Never use the Heartran git identity for commits or pushes.

- Keep commits small and topical; prefer multiple commits over one large drop when touching orthogonal areas.

## Git Identity

- Every agent should have his own git identity when committing changes in order to have a more clear and readable history

| Agent | GIT_COMMITTER_NAME / GIT_AUTHOR_NAME | GIT_COMMITTER_EMAIL / GIT_AUTHOR_EMAIL |
| --- | :---: | --- |
| Claude | Claude | noreply@anthropic.com |
| Codex | Codex | 199175422+chatgpt-codex-connector[bot]@users.noreply.github.com |
| Gemini | Gemini | 176961590+gemini-code-assist[bot]@users.noreply.github.com |
| Cascade | Cascade | 272510577+windsurf-cascade-agent[bot]@users.noreply.github.com |
| GitHub Copilot | Copilot[bot] | 198982749+Copilot[bot]@users.noreply.github.com |
