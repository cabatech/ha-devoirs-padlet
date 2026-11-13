"""Couche d'extraction des devoirs par LLM (MiniMax-M3 via ``ai_task`` de Home Assistant).

Stratégie :
- On extrait le texte brut de l'ODT/DOCX (cf. ``attachments.py``).
- On l'envoie à un LLM avec un prompt structuré.
- Le LLM renvoie du JSON ``{"days":[{"date","day_name","items":[...]}]}``
  qu'on parse et valide (``DevoirDay``).

Le LLM est injecté (``LLMCallable``) : ``HomeAssistant.llm`` en production,
une fonction qui renvoie le JSON souhaité dans les tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Prompt système : strict sur le format de sortie, refuse tout bavardage.
SYSTEM_PROMPT = """\
Tu es un extracteur de devoirs scolaires français.
À partir d'un texte brut issu d'un fichier ODT/DOCX partagé par
l'enseignant(e), tu renvoies UNIQUEMENT un objet JSON valide, sans
texte avant/après, sans Markdown, sans commentaire.

Format attendu (UN objet JSON, rien d'autre) :
{
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_name": "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche",
      "items": [
        {
          "title": "Titre court du devoir (≤ 60 caractères)",
          "body": "Consigne complète telle que lue dans le document",
          "subject": "lecture|maths|francais|sport|signature|apprentissage|default"
        }
      ]
    }
  ]
}

Règles :
- "date" est la date de RENDU du jour (celle mentionnée dans la section
  « POUR LUNDI 7/09 » → lundi 7 septembre de l'année courante).
- Si une section mentionne un mois/jour sans année, utilise l'année
  fournie dans le contexte utilisateur (paramètre "school_year").
- Si un item est réparti sur plusieurs jours (« et 2 fois mercredi »),
  ne le DUPLIQUE pas : garde-le dans le jour où il apparaît en premier.
- Si tu ne trouves aucune date précise dans le texte, renvoie
  {"days": []}.
- "subject" ∈ {lecture, maths, francais, sport, signature, apprentissage,
  default}. Mets "default" si hésitation.
- Ne traduis PAS, ne corrige PAS l'orthographe : recopie la consigne
  originale fidèlement.
"""


def build_user_prompt(text: str, school_year: int) -> str:
    """Prompt user avec le texte brut + contexte année scolaire.

    L'année scolaire sert pour les dates sans année explicite
    (« POUR LUNDI 7/09 » → 7 septembre de ``school_year``).
    """
    return (
        f"school_year={school_year}\n"
        "Texte des devoirs :\n"
        f"{text}"
    )


@dataclass(slots=True)
class DevoirSubItem:
    """Un sous-devoir unitaire (une tâche de la liste)."""

    title: str
    body: str
    subject: str = "default"
    # UID déterministe (somme du hash du padlet + jour + position)
    uid_seed: str = ""
    source_wish_id: int = 0
    date_due: Optional[date] = None

    @property
    def uid(self) -> str:
        return f"padlet-devoir-{self.uid_seed}@devoirs-padlet" if self.uid_seed else ""


@dataclass(slots=True)
class DevoirDay:
    """Un jour de devoirs avec ses items."""

    date: date
    day_name: str
    items: list[DevoirSubItem] = field(default_factory=list)


_SUBJECTS = {
    "lecture", "maths", "francais", "sport", "signature",
    "apprentissage", "default",
}


def _normalize_subject(raw: str) -> str:
    raw = (raw or "").strip().lower()
    # tolère « français » / « francais » / « francais » etc.
    raw = raw.replace("ç", "c").replace("é", "e").replace("è", "e")
    raw = raw.replace(" ", "")
    if raw in _SUBJECTS:
        return raw
    # alias courants
    if "lecture" in raw or "lire" in raw or "livre" in raw:
        return "lecture"
    if "math" in raw or "calcul" in raw or "nombre" in raw or "table" in raw:
        return "maths"
    if "franc" in raw or "dict" in raw or "grammair" in raw or "conjug" in raw:
        return "francais"
    if "sport" in raw or "tenue" in raw or "eps" in raw:
        return "sport"
    if "signature" in raw or "signer" in raw or "carnet" in raw or "livret" in raw:
        return "signature"
    if "apprendre" in raw or "memoris" in raw or "lecon" in raw or "reviser" in raw:
        return "apprentissage"
    return "default"


def _extract_first_json(text: str) -> Optional[str]:
    """Trouve le premier bloc JSON équilibré dans ``text`` (entre ```json ...``` ou nu)."""
    # Si balisage ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Sinon, premier { et matching }
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_llm_response(raw: str, *, source_wish_id: int) -> list[DevoirDay]:
    """Parse la sortie brute du LLM en ``list[DevoirDay]``.

    Tolérant : accepte du texte autour du JSON, extrait le premier objet
    valide. Renvoie ``[]`` si impossible.
    """
    blob = _extract_first_json(raw)
    if not blob:
        logger.warning("LLM: aucun JSON trouvé dans la réponse")
        return []
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError as exc:
        logger.warning("LLM: JSON invalide : %s", exc)
        return []

    days: list[DevoirDay] = []
    for d in payload.get("days") or []:
        ddate = _parse_iso_date(d.get("date"))
        if not ddate:
            continue
        day_name = (d.get("day_name") or "").strip().lower()
        items_raw = d.get("items") or []
        items: list[DevoirSubItem] = []
        for idx, it in enumerate(items_raw):
            title = (it.get("title") or "").strip()
            body = (it.get("body") or "").strip()
            if not title and not body:
                continue
            items.append(
                DevoirSubItem(
                    title=title[:120] if title else body[:60],
                    body=body or title,
                    subject=_normalize_subject(it.get("subject", "")),
                    uid_seed=f"{source_wish_id}-{ddate.isoformat()}-{idx:02d}",
                    source_wish_id=source_wish_id,
                    date_due=ddate,
                )
            )
        if not items:
            continue
        days.append(DevoirDay(date=ddate, day_name=day_name, items=items))
    return days


def _parse_iso_date(text: str | None) -> Optional[date]:
    if not text:
        return None
    s = str(text).strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# Function type pour qu'on puisse mocker dans les tests.
LLMCallable = Callable[[str, str], str]


def extract_devoirs(
    text: str,
    source_wish_id: int,
    *,
    school_year: int,
    llm: LLMCallable,
) -> list[DevoirDay]:
    """Pipeline complet : prompt → LLM → JSON → ``list[DevoirDay]``.

    ``llm`` : en production ``HomeAssistant.llm`` (ai_task), en test un mock.
    """
    user = build_user_prompt(text, school_year=school_year)
    raw = llm(SYSTEM_PROMPT, user)
    return parse_llm_response(raw, source_wish_id=source_wish_id)


def cached_llm(llm: LLMCallable, path: Path) -> LLMCallable:
    """Mémorise les réponses par empreinte du prompt dans ``path`` (JSON).

    Une fiche déjà lue n'est plus renvoyée au LLM : moins d'appels, et des titres
    stables d'une synchro à l'autre (le LLM ne répond pas deux fois pareil).
    """
    def call(system: str, user: str) -> str:
        key = hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            return cache[key]
        raw = llm(system, user)
        if _extract_first_json(raw):  # une réponse illisible n'est pas gardée
            cache[key] = raw
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return raw
    return call
