"""Vault — the on-disk store (works, images, library blocks) + a rebuildable SQLite index.

Files on disk are the source of truth; the index is throwaway (``rules/security.md``: private user
content, loopback-only, paths confined under the vault root).
"""
