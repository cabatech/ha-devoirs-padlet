"""Accès à Home Assistant : liste de tâches ``todo.*`` et LLM via ``ai_task``.

- lecture : service ``todo.get_items`` (réponse avec ``uid``, ``status``, ``due``)
- écriture : ``todo.add_item``, ``todo.update_item`` (garde le statut coché), ``todo.remove_item``
- LLM : service ``ai_task.generate_data`` sur l'entité configurée

HA ne laisse pas choisir l'UID d'une tâche : chaque tâche porte une ligne marqueur
``devoir-id: <clé>`` dans sa description. Seules les tâches marquées sont touchées.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import httpx

from .config import Config
from .llm import DevoirSubItem
from .models import DevoirItem, ItemKind

logger = logging.getLogger(__name__)

MARKER = "devoir-id: "
LLM_TIMEOUT = 180  # une extraction MiniMax peut dépasser la minute


def _description(body: str, title: str, *, subject: str, source_url: str, key: str) -> str:
    lines = []
    if body and body != title:
        lines.append(body)
    if subject and subject != "default":
        lines.append(f"Matière : {subject}")
    if source_url:
        lines.append(f"Voir sur Padlet : {source_url}")
    lines.append(MARKER + key)
    return "\n".join(lines)


def payload_for_subitem(item: DevoirSubItem, *, source_url: str = "") -> dict:
    """Données ``todo.add_item`` pour un sous-devoir extrait par le LLM."""
    payload = {
        "item": item.title,
        "description": _description(
            item.body, item.title, subject=item.subject, source_url=source_url, key=item.uid,
        ),
    }
    if item.date_due:
        payload["due_date"] = item.date_due.isoformat()
    return payload


def payload_for_fallback(item: DevoirItem) -> dict:
    """Données ``todo.add_item`` pour un post sans pièce jointe (devoir court ou événement)."""
    due = item.event_start.date() if item.kind == ItemKind.EVENEMENT and item.event_start else item.due
    payload = {
        "item": item.title,
        "description": _description(
            item.description, item.title, subject="", source_url=item.raw_link, key=item.uid,
        ),
    }
    if due:
        payload["due_date"] = due.isoformat()
    return payload


def marker_of(description: str | None) -> str | None:
    """Clé devoir d'une description, ou None si la tâche n'est pas à nous."""
    for line in (description or "").splitlines():
        if line.startswith(MARKER):
            return line[len(MARKER):].strip()
    return None


def _fingerprint(summary: str, description: str, due: str | None) -> tuple:
    return (summary.strip(), "\n".join(description.strip().splitlines()), (due or "")[:10])


def plan_sync(
    wanted: dict[str, dict], existing: list[dict], *, today: date,
) -> tuple[list[dict], list[tuple[str, dict]], list[str]]:
    """Calcule (à créer, à modifier [(uid, payload)], uids à supprimer).

    - tâche non marquée : ignorée ;
    - doublon d'une clé : supprimé ;
    - clé voulue mais contenu différent : modifiée (le statut coché est conservé) ;
    - clé absente de la synchro (orphelin) : gardée, un Padlet ou un LLM en panne
      ne doit jamais effacer de devoirs ;
    - clé manquante : créée, sauf si l'échéance est déjà passée.
    """
    to_update: list[tuple[str, dict]] = []
    to_remove: list[str] = []
    seen: set[str] = set()
    for it in existing:
        key = marker_of(it.get("description"))
        if key is None:
            continue
        if key in seen:
            to_remove.append(it["uid"])
            continue
        seen.add(key)
        want = wanted.get(key)
        if want is None:
            continue
        if _fingerprint(it.get("summary", ""), it.get("description", ""), it.get("due")) != (
            _fingerprint(want["item"], want["description"], want.get("due_date"))
        ):
            to_update.append((it["uid"], want))
    to_create = [
        p for k, p in wanted.items()
        if k not in seen and (not p.get("due_date") or p["due_date"] >= today.isoformat())
    ]
    return to_create, to_update, to_remove


class HomeAssistant:
    """Client REST minimal vers HA (services avec ``return_response``)."""

    def __init__(self, config: Config) -> None:
        self.url = config.ha_url.rstrip("/")
        self.todo = config.ha_todo_entity
        self.ai_task = config.ha_ai_task_entity
        self.timeout = config.request_timeout
        self.client = httpx.Client(headers={"Authorization": f"Bearer {config.ha_token}"})

    def _call(self, domain: str, service: str, data: dict, *, response: bool = False,
              timeout: int | None = None) -> dict:
        r = self.client.post(
            f"{self.url}/api/services/{domain}/{service}" + ("?return_response" if response else ""),
            json=data,
            timeout=timeout or self.timeout,
        )
        r.raise_for_status()
        return r.json().get("service_response", {}) if response else {}

    def list_items(self) -> list[dict]:
        res = self._call("todo", "get_items", {"entity_id": self.todo}, response=True)
        return res.get(self.todo, {}).get("items", [])

    def add_item(self, payload: dict) -> None:
        self._call("todo", "add_item", {"entity_id": self.todo, **payload})

    def update_item(self, uid: str, payload: dict) -> None:
        data = {"entity_id": self.todo, "item": uid, "rename": payload["item"],
                "description": payload["description"]}
        if payload.get("due_date"):
            data["due_date"] = payload["due_date"]
        self._call("todo", "update_item", data)

    def remove_items(self, uids: list[str]) -> None:
        if uids:
            self._call("todo", "remove_item", {"entity_id": self.todo, "item": uids})

    def llm(self, system: str, user: str) -> str:
        """Appel LLM par ``ai_task.generate_data`` ; signature de ``llm.LLMCallable``."""
        res = self._call(
            "ai_task", "generate_data",
            {"entity_id": self.ai_task, "task_name": "devoirs-padlet",
             "instructions": f"{system}\n\n---\n\n{user}"},
            response=True, timeout=LLM_TIMEOUT,
        )
        data = res.get("data")
        if not isinstance(data, str):
            raise RuntimeError(f"ai_task : réponse inattendue {str(res)[:200]}")
        return data


@dataclass(slots=True)
class SyncStats:
    """Compteurs d'une synchronisation vers la liste HA."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    errors: int = 0


def publish_to_ha(
    subitems: list[DevoirSubItem],
    fallback_items: list[DevoirItem],
    config: Config,
    *,
    source_urls: dict[int, str] | None = None,
    today: date | None = None,
    ha: HomeAssistant | None = None,
) -> SyncStats:
    """Synchronise les devoirs vers la liste ``todo.*`` configurée."""
    ha = ha or HomeAssistant(config)
    wanted = {s.uid: payload_for_subitem(s, source_url=(source_urls or {}).get(s.source_wish_id, ""))
              for s in subitems}
    wanted.update({f.uid: payload_for_fallback(f) for f in fallback_items})
    to_create, to_update, to_remove = plan_sync(wanted, ha.list_items(), today=today or date.today())
    logger.info("Liste %s : %d à créer, %d à modifier, %d doublons à supprimer",
                ha.todo, len(to_create), len(to_update), len(to_remove))

    stats = SyncStats()
    try:
        ha.remove_items(to_remove)
        stats.deleted = len(to_remove)
    except httpx.HTTPError as exc:
        logger.warning("Échec suppression des doublons : %s", exc)
        stats.errors += 1
    for uid, payload in to_update:
        try:
            ha.update_item(uid, payload)
            stats.updated += 1
        except httpx.HTTPError as exc:
            logger.warning("Échec modification %s : %s", payload["item"], exc)
            stats.errors += 1
    for payload in to_create:
        try:
            ha.add_item(payload)
            stats.created += 1
        except httpx.HTTPError as exc:
            logger.warning("Échec création %s : %s", payload["item"], exc)
            stats.errors += 1
    logger.info("Liste HA : +%d, ~%d, -%d, %d erreur(s)",
                stats.created, stats.updated, stats.deleted, stats.errors)
    return stats
