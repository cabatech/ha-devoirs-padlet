"""Tests pour ``devoirs_padlet.pipeline`` — orchestrateur avec mocks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pytest

from devoirs_padlet.attachments import fetch_attachment_text
from devoirs_padlet.llm import DevoirDay, DevoirSubItem
from devoirs_padlet.models import ItemKind
from devoirs_padlet.pipeline import current_school_year, run_pipeline
from devoirs_padlet.padlet import PadletItem


# ---------- Helpers ----------

def make_item(
    *,
    id: int,
    subject: str = "Semaine N",
    body: str = "",
    extension: str = "",
    attachment_url: str = "https://signed.url/file",
    permalink: str = "",
    published_at: datetime | None = None,
    wall_section_hashid: str = "sec_X",
) -> PadletItem:
    """Helper : si extension précisée, attachment_url doit être non-vide
    (sinon is_devoir_file=False). On met l'URL par défaut dans ce cas.
    Pour les items sans PJ, passer ``extension=""`` et ``attachment_url=""``.
    """
    if extension and not attachment_url:
        attachment_url = "https://signed.url/file"
    if not extension:
        attachment_url = ""  # pas de PJ => pas d'URL
    return PadletItem(
        id=id,
        hashid=f"post_{id}",
        subject=subject,
        body=body,
        permalink=permalink or f"https://padlet.com/wish/{id}",
        wall_section_hashid=wall_section_hashid,
        published_at=published_at or datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        attachment_url=attachment_url,
        attachment_filename=f"file{id}",
        extension=extension,
    )


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def fake_text_extractor(monkeypatch):
    """Évite la vraie extraction en remplaçant ``fetch_attachment_text``.

    Renvoie un texte ODT minimal contenant « POUR LUNDI 7/09 ».
    """
    def fake(item, *, timeout=60):
        if item.extension not in ("odt", "docx"):
            return ""
        if "ignore" in item.subject:
            return ""  # texte vide → déclenche skip
        return "POUR LUNDI 7/09\n-fiche lecture ligne a/\n-apprendre les mots"

    # On patche dans le module ``attachments`` (source), pas ``pipeline``
    # (qui a déjà fait l'import).
    monkeypatch.setattr(
        "devoirs_padlet.attachments.fetch_attachment_text", fake,
    )
    return fake


def fake_llm_response(text: str, source_wish_id: int, *, school_year: int, llm=None) -> list[DevoirDay]:
    """LLM mocké : renvoie un jour quand le texte contient LUNDI, vide sinon.

    La signature doit matcher ``llm.extract_devoirs``.
    """
    if "POUR LUNDI" not in text and "Lundi 7/09" not in text:
        return []
    return [
        DevoirDay(
            date=__import__("datetime").date(2026, 9, 7),
            day_name="lundi",
            items=[
                DevoirSubItem(
                    title="Fiche lecture a",
                    body="fiche lecture ligne a",
                    subject="lecture",
                    uid_seed=f"{source_wish_id}-2026-09-07-00",
                    source_wish_id=source_wish_id,
                    date_due=__import__("datetime").date(2026, 9, 7),
                ),
            ],
        )
    ]


# ---------- Tests pipeline ----------

class TestRunPipeline:
    def test_odt_devoir_extrait(self, fake_text_extractor):
        item = make_item(id=1, subject="Semaine 1", extension="odt")
        result = run_pipeline([item], school_year=2026, extract_fn=fake_llm_response)
        assert len(result.days) == 1
        assert result.days[0].items[0].title == "Fiche lecture a"
        assert result.days[0].items[0].subject == "lecture"

    def test_odt_texte_non_pertinent_skip(self, fake_text_extractor, monkeypatch):
        """Un ODT qui ne contient pas de dates de devoirs → skip."""
        def empty_llm(text, source_wish_id):
            return []
        item = make_item(id=2, subject="textes de grammaire", extension="docx")
        result = run_pipeline([item], school_year=2026, extract_fn=empty_llm)
        assert result.days == []
        assert result.total_subitems == 0

    def test_pdf_skip(self):
        item = make_item(id=3, subject="Fiche lecture", extension="pdf")
        result = run_pipeline([item], school_year=2026, extract_fn=fake_llm_response)
        assert result.days == []
        assert result.total_subitems == 0
        assert len(result.skipped) == 1

    def test_doc_skip(self):
        item = make_item(id=4, subject="Livret écolier", extension="doc")
        result = run_pipeline([item], school_year=2026, extract_fn=fake_llm_response)
        assert result.days == []
        assert len(result.skipped) == 1

    def test_event_datee(self):
        item = make_item(
            id=5,
            subject="La réunion aura lieu le 25/10/2026 à 18h",
        )
        result = run_pipeline([item], school_year=2026, extract_fn=fake_llm_response)
        assert len(result.fallback_items) == 1
        assert result.fallback_items[0].kind == ItemKind.EVENEMENT

    def test_texte_long_sans_pj_skip(self):
        item = make_item(
            id=6,
            subject=("Longue explication " * 30),
        )
        result = run_pipeline([item], school_year=2026, extract_fn=fake_llm_response)
        assert result.days == []
        assert result.fallback_items == []
        assert len(result.skipped) == 1

    def test_mix_items(self):
        items = [
            make_item(id=10, subject="Semaine 2", extension="odt"),
            make_item(id=11, subject="Sortie scolaire 25/10"),
            make_item(id=12, subject="Calendrier", extension="pdf"),
        ]
        result = run_pipeline(
            items,
            school_year=2026,
            extract_fn=fake_llm_response,
        )
        assert len(result.days) == 1  # semaine 2 → LLM
        assert len(result.fallback_items) == 1  # sortie → VEVENT
        assert len(result.skipped) == 1  # PDF


class TestCurrentSchoolYear:
    def test_septembre(self):
        from datetime import date
        assert current_school_year(date(2026, 9, 15)) == 2026

    def test_janvier(self):
        from datetime import date
        assert current_school_year(date(2027, 1, 15)) == 2026

    def test_juillet(self):
        from datetime import date
        assert current_school_year(date(2026, 7, 1)) == 2026

    def test_juin(self):
        from datetime import date
        assert current_school_year(date(2026, 6, 30)) == 2025
