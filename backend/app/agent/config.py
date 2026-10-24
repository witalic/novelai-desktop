"""Agent access settings, persisted in ``settings.json`` under ``agent`` (``appconfig``).

Pure models + coercion — no app imports, so ``appconfig`` can use it without an import cycle. The port is
not a setting: the listener picks it (``server.PORTS``).
"""
from pydantic import BaseModel, Field, ValidationError


class AgentPermissions(BaseModel):
    """One switch per kind of action (Settings → Agent access), checked on every tool call."""

    read: bool = True  # browse the Library and works


class AgentConfig(BaseModel):
    enabled: bool = False
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)


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
