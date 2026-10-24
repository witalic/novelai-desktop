"""Read and change the persisted agent-access config (``settings.json`` → ``agent``)."""
from app import appconfig
from app.agent.config import AgentConfig
from app.settings import Settings

_SECTIONS = ("permissions",)  # merged key by key, so a patch can flip one switch


def load_config(settings: Settings) -> AgentConfig:
    return AgentConfig.model_validate(appconfig.load(settings)["agent"])


def update_config(settings: Settings, changes: dict) -> AgentConfig:
    """Apply a partial change. An invalid value raises pydantic's ValidationError and nothing is written."""
    def apply(data: dict) -> None:
        cur = data["agent"]
        merged = {**cur, **{k: v for k, v in changes.items() if k not in _SECTIONS}}
        for key in _SECTIONS:
            if isinstance(changes.get(key), dict):
                merged[key] = {**cur[key], **changes[key]}
        data["agent"] = AgentConfig.model_validate(merged).model_dump()

    return AgentConfig.model_validate(appconfig.update(settings, apply)["agent"])
