"""Orchestrateur : ``PadletItem`` + LLM → ``DevoirSubItem`` (puis liste HA)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from . import attachments as _attachments
from .llm import DevoirDay, DevoirSubItem, extract_devoirs
from .models import DevoirItem, ItemKind, classify
from .padlet import PadletItem, fetch_items

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineResult:
    """Résultat de l'orchestration : items LLM-extraits + items de repli."""

    days: list[DevoirDay] = field(default_factory=list)
    fallback_items: list[DevoirItem] = field(default_factory=list)
    skipped: list[PadletItem] = field(default_factory=list)

    @property
    def total_subitems(self) -> int:
        return sum(len(d.items) for d in self.days)

    @property
    def total_todos(self) -> int:
        return self.total_subitems + len(self.fallback_items)


def current_school_year(today: date | None = None) -> int:
    """Renvoie l'année scolaire courante.

    Avant juillet (``month < 7``), on est dans l'année scolaire N-1/N.
    À partir de juillet, dans N/N+1. Ex : 2026-09 → année 2026.
    """
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def _school_year_date(year: int, *, default: int | None = None) -> int:
    """Helper idempotent (utilisé par les tests)."""
    return year if year else (default or current_school_year())


def run_pipeline(
    padlet_items: list[PadletItem],
    *,
    school_year: int,
    llm=None,
    extract_fn=extract_devoirs,
) -> PipelineResult:
    """Orchestration des posts Padlet.

    Logique de tri :
    - PJ ODT/DOCX (``is_devoir_file``) → téléchargement, extraction
      texte, LLM → 0..N sous-items. Si le LLM ne détecte aucune date
      de devoir (``days == []``), c'est sans doute un texte à étudier
      (pas une liste) → on skip silencieusement.
    - PJ autres extensions (PDF, DOC ancien, image…) → skip
      (``is_devoir_file`` est False).
    - Pas de PJ mais titre contient une date (``classify`` →
      ``EVENEMENT``) → VEVENT direct (ex : réunion de rentrée).
    - Texte court sans PJ mais pertinent (rare) → VTODO avec DUE par
      défaut via ``compute_due``.
    - Texte long sans PJ ni date → skip.
    """
    result = PipelineResult()
    for item in padlet_items:
        # Cas 1 : PJ ODT/DOCX → LLM
        if item.is_devoir_file:
            # Appel via le module (et non via l'import) pour faciliter
            # le monkeypatch dans les tests.
            text = _attachments.fetch_attachment_text(item)
            if not text.strip():
                logger.warning("Texte vide pour %s, on skip", item.uid_seed)
                result.skipped.append(item)
                continue
            try:
                days = extract_fn(text, item.id, school_year=school_year, llm=llm)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM échec pour %s : %s", item.uid_seed, exc)
                result.skipped.append(item)
                continue
            if not days:
                logger.info("LLM : 0 jour détecté pour %s (texte non pertinent), skip", item.uid_seed)
                continue
            result.days.extend(days)
            continue

        # Cas 2 : autre PJ (PDF, image, .doc…) → skip
        if item.attachment_url:
            logger.info("Skip PJ %s : %s", item.extension or "?", item.subject[:60])
            result.skipped.append(item)
            continue

        # Cas 3 : pas de PJ → classify() (titre)
        kind, due, ev_s, ev_e = classify(
            item.subject,
            attachment_url=False,
            published_at=item.published_at,
        )
        if kind == ItemKind.TEXTE_LIBRE:
            logger.info("Skip (texte long sans PJ ni date) : %r", item.subject)
            result.skipped.append(item)
            continue
        result.fallback_items.append(
            DevoirItem(
                padlet_guid=item.permalink or item.hashid or str(item.id),
                title=item.subject,
                description=item.body,
                attachment_url="",
                published_at=item.published_at,
                kind=kind,
                due=due,
                event_start=ev_s,
                event_end=ev_e,
                raw_link=item.permalink,
            )
        )
    return result
