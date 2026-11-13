"""Configuration chargée depuis l'environnement (fichier .env supporté).

``.env`` minimal : clé=valeur, # commentaire. Pas de guillemets/escape
— suffisant pour notre cas. Dans l'add-on, ``run.sh`` exporte les options.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


@dataclass(slots=True)
class Config:
    """Paramètres de l'import Padlet → liste de tâches Home Assistant."""

    padlet_rss_url: str = ""  # legacy v1 (RSS), conservé pour debug
    padlet_wall_hashid: str = ""  # v2 : API wishes
    # Dans l'add-on, le Supervisor fournit SUPERVISOR_TOKEN et le proxy http://supervisor/core.
    ha_url: str = "http://supervisor/core"
    ha_token: str = ""
    ha_todo_entity: str = ""
    ha_ai_task_entity: str = ""
    timezone: str = "Europe/Paris"
    request_timeout: int = 30
    school_year: int = 0  # 0 = calculé via ``pipeline.current_school_year``
    data_dir: str = "data"  # cache des réponses LLM

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "Config":
        overrides = _parse_env_file(Path(env_file)) if env_file else {}
        merged = {**os.environ, **overrides}

        def _get(key: str, default: str = "") -> str:
            return merged.get(key, default)

        if not _get("PADLET_RSS_URL") and not _get("PADLET_WALL_HASHID"):
            raise ValueError("PADLET_WALL_HASHID (ou PADLET_RSS_URL legacy) manquant.")
        required = {
            "HA_TODO_ENTITY": _get("HA_TODO_ENTITY"),
            "HA_AI_TASK_ENTITY": _get("HA_AI_TASK_ENTITY"),
            "SUPERVISOR_TOKEN ou HA_TOKEN": _get("SUPERVISOR_TOKEN") or _get("HA_TOKEN"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Variables manquantes : {', '.join(missing)}.")

        return cls(
            padlet_rss_url=_get("PADLET_RSS_URL"),
            padlet_wall_hashid=_get("PADLET_WALL_HASHID"),
            ha_url=_get("HA_URL", "http://supervisor/core") or "http://supervisor/core",
            ha_token=required["SUPERVISOR_TOKEN ou HA_TOKEN"],
            ha_todo_entity=required["HA_TODO_ENTITY"],
            ha_ai_task_entity=required["HA_AI_TASK_ENTITY"],
            timezone=_get("TIMEZONE", "Europe/Paris") or "Europe/Paris",
            request_timeout=int(_get("REQUEST_TIMEOUT", "30") or "30"),
            school_year=int(_get("SCHOOL_YEAR", "0") or "0"),
            data_dir=_get("DEVOIRS_DATA_DIR", "data") or "data",
        )

    def validate(self) -> None:
        if not self.ha_todo_entity.startswith("todo."):
            raise ValueError(f"HA_TODO_ENTITY doit être une entité todo.*, pas {self.ha_todo_entity!r}.")
        if not self.ha_ai_task_entity.startswith("ai_task."):
            raise ValueError(
                f"HA_AI_TASK_ENTITY doit être une entité ai_task.*, pas {self.ha_ai_task_entity!r}."
            )
