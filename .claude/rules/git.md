# Git & commits

Loaded every session (global rule — no `paths`).

- **Commit only logically-complete, tested blocks.** Never a partial sub-step of a larger unit, and never a
  snapshot that references not-yet-built pieces. A multi-step change accumulates in the working tree and lands
  as one commit once it is whole and the owner has confirmed it.
- The owner tests + confirms before the commit; then push (no separate push approval).
- Conventional Commits (`feat:` / `fix:` / `refactor:` / `perf:` / `chore:` / `docs:` / `test:`); one logical change per commit.
- `.env`, keychain data, the vault, generated images, `node_modules/`, `.venv/`, and build output (`dist/`) are
  never committed — enforced by `.gitignore` and `rules/security.md`. Stage specific paths, not the world.
