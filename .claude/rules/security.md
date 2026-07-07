# Security

Loaded every session (global rule — no `paths`). This app holds the user's paid NovelAI access and their
private creative work — leaking a token or the vault is the core risk.

- **Two secrets, keychain-only:** the NovelAI **persistent token** and the **Anthropic API key** live in the
  OS keychain (via `keyring`), never in source, logs, or git. A dev `.env` is tolerated locally but is
  gitignored and never read into general responses. Never `echo` / `cat` / commit them.
- **Prefer token over password:** authenticate to NovelAI with the persistent API token from Account settings,
  so the app never handles the user's password. Argon2 login-key derivation is a last resort, not the default.
- **The vault is private:** prompts and generated images are the user's content. Never transmit them anywhere
  except the API they are explicitly bound for (NovelAI for generation, Anthropic for prompt authoring). Never
  commit vault data or generated images to the repo — they live outside version control.
- **Be a good API citizen:** NovelAI's image endpoint is **unofficial and undocumented**. Respect rate limits
  (back off on 429), mind **Anlas** cost (402 = out of Anlas), and never generate in tight loops or bulk-hammer.
- **Transparency to the user:** anything sent to an external service (a prompt to Claude, an image for img2img)
  leaves the machine — surface it in the UI; don't silently exfiltrate the vault.
