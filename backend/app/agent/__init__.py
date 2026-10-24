"""Agent access: an MCP endpoint inside the running app, so AI agents (Claude Code, …) can work with the
vault through the same services the UI uses — one writer to the vault, gated by per-action permissions
set in Settings → Agent access. Off by default; loopback only; its own bearer token (``keychain``)."""
