"""Récupération et parsing du padlet (postes avec attachments).

On utilise l'API JSON **wishes** (``/api/10/wishes``) plutôt que le RSS :
les URLs renvoyées sont des URLs signées (``expiry_token=…``) qui donnent
accès aux attachments sans s'authentifier (le bucket GCS sous-jacent
étant privé, l'URL directe ne marche pas).

L'API wishes expose aussi ``wall_section_hashid`` (= section du padlet
dans laquelle le post est rangé), utile pour filtrer si besoin. Pour
l'instant on garde tout.

Chaque « wish » devient un ``PadletItem`` avec :
- ``id`` (entier, utilisé pour dériver un UID stable)
- ``hashid`` (slug alphanumérique)
- ``subject``, ``headline``, ``body``
- ``published_at`` (ISO UTC)
- ``wall_section_hashid``
- ``attachment_url`` (URL signée) + ``attachment_filename`` + ``extension``
  (``odt`` / ``docx`` / autres)

Méthode publique :
- ``fetch_items(wall_hashid, *, timeout)`` → liste de ``PadletItem``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# URL de l'API wishes. ``v=N`` est un cache-buster, pas obligatoire.
WISHES_URL = "https://padlet.com/api/10/wishes"


def _parse_iso(text: str | None) -> Optional[datetime]:
    if not text:
        return None
    try:
        # Padlet émet « 2026-09-04T13:49:09.788Z » (ISO 8601 avec Z).
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        logger.warning("Date ISO invalide : %r", text)
        return None


@dataclass(slots=True)
class PadletItem:
    """Représentation normalisée d'un post (wish) Padlet."""

    id: int
    hashid: str
    subject: str
    body: str = ""
    permalink: str = ""
    wall_section_hashid: str = ""
    published_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    attachment_url: str = ""
    attachment_filename: str = ""
    extension: str = ""

    @property
    def is_devoir_file(self) -> bool:
        """Vrai si l'attachment est un ODT/DOCX/DOC susceptible de lister des devoirs."""
        return self.extension in ("odt", "docx", "doc") and bool(self.attachment_url)

    @property
    def uid_seed(self) -> str:
        """Graine d'UID CalDAV stable (entier Padlet → chaîne)."""
        return f"wish-{self.id}"


def _extract_attachment(attrs: dict) -> tuple[str, str, str]:
    """Renvoie ``(signed_url, filename, extension)`` depuis les attrs wishes.

    L'API renvoie plusieurs URLs redondantes :
    - ``attachment`` (URL signée simple)
    - ``wish_content.attachment_props.url`` (idem)
    - ``attachment_link.canonical_url`` (bucket privé, sans token)
    - ``attachment_link.display_url`` (prévisualisation Office)

    On prend l'URL signée.
    """
    url = attrs.get("attachment") or ""
    fn = ""
    ext = ""
    link = attrs.get("attachment_link") or {}
    if isinstance(link, dict):
        fn = link.get("provider_name") or ""
    if not fn and url:
        fn = url.split("/")[-1].split("?")[0]
    ext = link.get("extension", "") if isinstance(link, dict) else ""
    return url, fn, ext


def _parse_wish(w: dict) -> PadletItem | None:
    """Parse un wish brut de l'API. Renvoie ``None`` si wish vide/invalide."""
    if not isinstance(w, dict) or w.get("type") != "wish":
        return None
    attrs = w.get("attributes") or {}
    if not attrs.get("published", True):
        return None
    wid = attrs.get("id")
    if not isinstance(wid, int):
        return None
    url, fn, ext = _extract_attachment(attrs)
    subject = (attrs.get("subject") or attrs.get("headline") or "").strip()
    body = (attrs.get("body") or "").strip()
    return PadletItem(
        id=wid,
        hashid=attrs.get("hashid") or "",
        subject=subject or "(sans titre)",
        body=body,
        permalink=attrs.get("permalink") or "",
        wall_section_hashid=attrs.get("wall_section_hashid") or "",
        published_at=_parse_iso(attrs.get("published_at")) or datetime.now(tz=timezone.utc),
        attachment_url=url,
        attachment_filename=fn,
        extension=ext,
    )


def fetch_items(wall_hashid: str, *, timeout: int = 30) -> list[PadletItem]:
    """Récupère tous les items (wishes) du padlet identifié par ``wall_hashid``.

    L'API wishes renvoie le tout en une fois paginé normalement (ici 100
    max). On n'a actuellement pas vu de gros padlet à scroller.
    """
    url = f"{WISHES_URL}?wall_hashid={wall_hashid}"
    logger.info("Fetch wishes : %s", url)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers={
            "User-Agent": "devoirs-padlet/1.0",
            "Accept": "application/json",
        })
        r.raise_for_status()
        payload = r.json()

    items: list[PadletItem] = []
    data = payload.get("data") or []
    for raw in data:
        item = _parse_wish(raw)
        if item is not None:
            items.append(item)

    logger.info("API wishes : %d items (publiés), %d avec attachment devoirs",
                len(items), sum(1 for i in items if i.is_devoir_file))
    return items
