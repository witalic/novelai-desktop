"""Read and change the persisted agent-access config (``settings.json`` → ``agent``)."""
from datetime import date

from app import appconfig
from app.agent.config import AgentConfig
from app.settings import Settings

_SECTIONS = ("permissions", "spending")  # merged key by key, so a patch can flip one switch


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


def spent_today(cfg: AgentConfig) -> int:
    return cfg.spent.anlas if cfg.spent.day == date.today().isoformat() else 0


def add_spent(settings: Settings, anlas: int) -> int:
    """Count a paid agent generation against today's budget (a new local day starts from zero) — or, negative, give
    back one NovelAI refused; returns today's total."""
    today = date.today().isoformat()

    def apply(data: dict) -> None:
        spent = data["agent"]["spent"]
        total = (spent["anlas"] if spent["day"] == today else 0) + anlas
        data["agent"]["spent"] = {"day": today, "anlas": max(total, 0)}

    return appconfig.update(settings, apply)["agent"]["spent"]["anlas"]
