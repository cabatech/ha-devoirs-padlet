"""Modèles de données pour les items Padlet.

Architecture à deux niveaux (refactorée) :

- ``PadletItem`` (cf. ``padlet.py``) — un post brut du padlet.
- ``DevoirDay`` / ``DevoirSubItem`` (cf. ``llm.py``) — sortie structurée
  du LLM appliqué au texte d'un ODT/DOCX.

Ce module ici ne contient plus que la **classification de repli** pour
les posts qui n'ont pas de PJ (texte court sans date, VEVENT, etc.).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ItemKind(str, Enum):
    """Type d'item Padlet après classification."""

    DEVOIR_FICHIER = "devoir_fichier"  # PJ → VTODO + DUE
    DEVOIR_TEXTE = "devoir_texte"      # Texte court sans PJ → VTODO + DUE
    EVENEMENT = "evenement"            # Date précise dans le titre → VEVENT
    TEXTE_LIBRE = "texte_libre"        # Pas de PJ ni de date, à ignorer


@dataclass(slots=True)
class DevoirItem:
    """Item Padlet normalisé, prêt à être converti en composant iCal.

    Conservé pour les items **non-ODT** (textes courts ou événements
    datés). Le pipeline principal (ODT) utilise ``DevoirSubItem``.
    """

    padlet_guid: str           # URL wish unique, sert d'UID CalDAV
    title: str
    description: str = ""
    attachment_url: str = ""
    published_at: datetime = field(default_factory=datetime.now)

    kind: ItemKind = ItemKind.TEXTE_LIBRE
    due: Optional[date] = None
    event_start: Optional[datetime] = None
    event_end: Optional[datetime] = None

    raw_link: str = ""

    @property
    def uid(self) -> str:
        return f"padlet-{self.padlet_guid.split('/')[-1]}@devoirs-padlet"


# Regex de date en français. On capture jour/mois/année si présente,
# sinon jour/mois avec année implicite (= année de publication).
_DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})"),
    re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?!\d)"),
    re.compile(
        r"(\d{1,2})\s+("
        r"janv(?:ier)?|f[ée]vr(?:ier)?|mars|avr(?:il)?|mai|juin|"
        r"juil(?:let)?|ao[ûu]t|sept(?:embre)?|oct(?:obre)?|nov(?:embre)?|"
        r"d[ée]c(?:embre)?"
        r")",
        re.IGNORECASE,
    ),
]

_FR_DAYS = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

_TIME_PATTERN = re.compile(r"\b(\d{1,2})[h:](\d{2})\b|\b(\d{1,2})h\b")


def _parse_day_in_text(text: str) -> Optional[str]:
    """Trouve le premier nom de jour français mentionné."""
    m = re.search(
        r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(1).lower()


_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # abréviations
    "janv": 1, "janv.": 1, "févr": 2, "fev": 2, "févr.": 2,
    "avr": 4, "avr.": 4, "juil": 7, "juil.": 7,
    "sept": 9, "sept.": 9, "oct": 10, "oct.": 10,
    "nov": 11, "nov.": 11, "déc": 12, "dec": 12, "déc.": 12,
}


def _parse_date_in_text(text: str, *, published_year: int) -> Optional[tuple[date, str]]:
    """Cherche la première date plausible dans ``text``.

    Essaie successivement :
    1) ``jj/mm/aaaa`` (prioritaire),
    2) ``jj/mm`` (année = ``published_year``),
    3) ``jj mois_en_lettres`` (FR, avec ou sans année accolée).
    """
    m = _DATE_PATTERNS[0].search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d), m.group(0)
        except ValueError:
            pass

    m = _DATE_PATTERNS[1].search(text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            return date(published_year, mo, d), m.group(0)
        except ValueError:
            pass

    # Pattern 3 : jour + mois en lettres
    m = _DATE_PATTERNS[2].search(text)
    if m:
        d = int(m.group(1))
        mo_raw = m.group(2).lower().rstrip(".")
        mo = _FR_MONTHS.get(mo_raw)
        if mo:
            try:
                return date(published_year, mo, d), m.group(0)
            except ValueError:
                pass

    return None


def _parse_time_in_text(text: str) -> Optional[tuple[int, int]]:
    m = _TIME_PATTERN.search(text)
    if not m:
        return None
    if m.group(3):
        return int(m.group(3)), 0
    return int(m.group(1)), int(m.group(2))


def _parse_dt_in_text(text: str, *, fallback_year: int) -> Optional[tuple[datetime, str]]:
    res = _parse_date_in_text(text, published_year=fallback_year)
    if not res:
        return None
    d, matched = res
    t = _parse_time_in_text(text) or (0, 0)
    return datetime(d.year, d.month, d.day, t[0], t[1]), matched


def classify(
    title: str,
    *,
    attachment_url: str = "",
    published_at: datetime | None = None,
) -> tuple[ItemKind, Optional[date], Optional[datetime], Optional[datetime]]:
    """Classifie un item et calcule DUE / event_start.

    Idempotent : pour les items avec ODT/DOCX, l'orchestrateur appellera
    directement ``llm.extract_devoirs`` au lieu de cette classification —
    on ne sert ici que pour les posts non-ODT.
    """
    published = published_at or datetime.now()
    title = title.strip()

    # 1) événement daté explicite
    dt_match = _parse_dt_in_text(title, fallback_year=published.year)
    if dt_match:
        start, _ = dt_match
        from datetime import timedelta
        end = start + timedelta(hours=1, minutes=30)
        return ItemKind.EVENEMENT, None, start, end

    # 2) PJ présente
    if attachment_url:
        return ItemKind.DEVOIR_FICHIER, None, None, None

    # 3) Texte court
    if len(title) <= 80:
        return ItemKind.DEVOIR_TEXTE, None, None, None

    return ItemKind.TEXTE_LIBRE, None, None, None


__all__ = [
    "ItemKind",
    "DevoirItem",
    "classify",
    "_parse_day_in_text",
    "_FR_DAYS",
]
