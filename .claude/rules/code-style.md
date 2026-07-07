# Code style

Loaded every session (global rule — no `paths`).

- **Python** ≥ 3.11 with type hints; `async` signatures for I/O-bound code. Match the surrounding code's idiom, naming, and comment density.
- **Frontend:** Vue 3 SFC + TypeScript, Composition API (`<script setup>`). Avoid `any` without a reason.
- Code, comments, UI copy, and docs are **English** — the human `README` is the one exception (Ukrainian).
- Operational output goes through `logging`, never `print` (print only for CLI usage / report rows).
- No backward-compatibility shims, dead code, or unused artifacts in landed code.
- Secrets never appear as literals in code — read them from the keychain / env. (→ `rules/security.md`)
