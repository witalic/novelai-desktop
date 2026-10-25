"""Agent access settings, persisted in ``settings.json`` under ``agent`` (``appconfig``).

Pure models + coercion — no app imports, so ``appconfig`` can use it without an import cycle. The port is
not a setting: the listener picks it (``server.PORTS``).
"""
from pydantic import BaseModel, Field, ValidationError


class AgentPermissions(BaseModel):
    """One switch per kind of action (Settings → Agent access), checked on every tool call. Everything but
    deleting is on by default (owner decision)."""

    read: bool = True  # browse the Library and works
    blocks_write: bool = True  # create and edit Library blocks
    blocks_delete: bool = False
    works_write: bool = True  # create works, rename them, edit image metadata
    combos_write: bool = True  # create and edit a work's block combinations
    works_delete: bool = False  # delete images and combinations
    generate: bool = True  # generate images into a work (free ones; paid ones also need AgentSpending)


class AgentSpending(BaseModel):
    """Anlas for agent generations. Free ones (Opus) need nothing more; a paid one needs ``paid`` on here, the call
    itself asking to spend (the user told the agent to), and to fit both limits (owner decision)."""

    paid: bool = False
    max_per_request: int = Field(default=20, ge=1, le=100_000)
    daily_budget: int = Field(default=200, ge=1, le=1_000_000)


class AgentSpent(BaseModel):
    """What paid agent generations spent on ``day`` (a local date) — the daily budget's meter. State, not a
    setting: only generations change it."""

    day: str = ""
    anlas: int = Field(default=0, ge=0)


class AgentConfig(BaseModel):
    enabled: bool = False
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)
    spending: AgentSpending = Field(default_factory=AgentSpending)
    spent: AgentSpent = Field(default_factory=AgentSpent)


def coerce(raw: object) -> dict:
    """A stored value → a valid config dict. A bad field falls back to its default; the rest survive."""
    if not isinstance(raw, dict):
        return AgentConfig().model_dump()
    good: dict = {}
    for key in AgentConfig.model_fields:
        if key in raw:
            try:
                AgentConfig.model_validate({key: raw[key]})
                good[key] = raw[key]
            except ValidationError:
                pass
    return AgentConfig.model_validate(good).model_dump()
