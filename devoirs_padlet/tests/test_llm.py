"""Tests pour ``devoirs_padlet.llm`` — avec mock du LLM (zéro réseau)."""

from __future__ import annotations

import json

import pytest

from devoirs_padlet.llm import (
    SYSTEM_PROMPT,
    DevoirDay,
    DevoirSubItem,
    build_user_prompt,
    extract_devoirs,
    parse_llm_response,
    _extract_first_json,
    _normalize_subject,
)


# ---------- Mock LLM ----------

class MockLLM:
    """Mock simple qui renvoie une réponse préconfigurée."""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


SAMPLE_LLM_OUTPUT = """
```json
{
  "days": [
    {
      "date": "2026-09-07",
      "day_name": "lundi",
      "items": [
        {"title": "Fiche lecture a", "body": "fiche lecture ligne a", "subject": "lecture"},
        {"title": "Signer livret", "body": "faire signer le livret", "subject": "signature"}
      ]
    },
    {
      "date": "2026-09-08",
      "day_name": "mardi",
      "items": [
        {"title": "Fiche lecture b", "body": "fiche lecture ligne b", "subject": "lecture"}
      ]
    }
  ]
}
```
"""


class TestExtractFirstJson:
    def test_code_block(self):
        assert _extract_first_json("```json\n{\"a\":1}\n```") == '{"a":1}'

    def test_no_markdown(self):
        assert _extract_first_json('salut {"a":1} bye') == '{"a":1}'

    def test_no_json(self):
        assert _extract_first_json("rien") is None

    def test_nested(self):
        s = '{"a": {"b": 2}, "c": [3, 4]}'
        assert _extract_first_json(s) == s


class TestParseLlm:
    def test_normal(self):
        days = parse_llm_response(SAMPLE_LLM_OUTPUT, source_wish_id=42)
        assert len(days) == 2
        assert days[0].date.isoformat() == "2026-09-07"
        assert days[0].day_name == "lundi"
        assert len(days[0].items) == 2
        assert days[0].items[0].title == "Fiche lecture a"
        assert days[0].items[0].subject == "lecture"
        assert days[0].items[0].uid_seed == "42-2026-09-07-00"

    def test_pas_de_json(self):
        assert parse_llm_response("aucun json ici", source_wish_id=1) == []

    def test_json_invalide(self):
        assert parse_llm_response("```json\n{not valid}\n```", source_wish_id=1) == []

    def test_items_vides_ignores(self):
        out = '{"days":[{"date":"2026-09-07","day_name":"lundi","items":[{"title":"","body":""}]}]}'
        assert parse_llm_response(out, source_wish_id=1) == []

    def test_date_invalide_ignoree(self):
        out = '{"days":[{"date":"pas-date","day_name":"lundi","items":[{"title":"x","body":"y"}]}]}'
        assert parse_llm_response(out, source_wish_id=1) == []

    def test_days_vide_accepte(self):
        """Un ODT sans dates structurées doit renvoyer [] sans crash."""
        out = '{"days":[]}'
        assert parse_llm_response(out, source_wish_id=1) == []


class TestNormalizeSubject:
    @pytest.mark.parametrize("raw,expected", [
        ("lecture", "lecture"),
        ("LECTURE", "lecture"),
        ("maths", "maths"),
        ("math", "maths"),
        ("calcul", "maths"),
        ("français", "francais"),
        ("francais", "francais"),
        ("dictée", "francais"),
        ("grammaire", "francais"),
        ("sport", "sport"),
        ("EPS", "sport"),
        ("signature", "signature"),
        ("apprentissage", "apprentissage"),
        ("réviser", "apprentissage"),
        ("default", "default"),
        ("inconnu", "default"),
        ("", "default"),
    ])
    def test_mappings(self, raw, expected):
        assert _normalize_subject(raw) == expected


class TestExtractDevoirs:
    """Pipeline complet avec mock LLM."""

    def test_pipeline_with_mock(self):
        mock = MockLLM(SAMPLE_LLM_OUTPUT)
        days = extract_devoirs(
            "POUR LUNDI 7/09\n-fiche lecture ligne a/",
            source_wish_id=42,
            school_year=2026,
            llm=mock,
        )
        assert len(days) == 2
        assert mock.calls  # on a appelé le LLM au moins une fois
        system_msg, user_msg = mock.calls[0]
        assert "extracteur de devoirs" in system_msg
        assert "school_year=2026" in user_msg
        assert "fiche lecture" in user_msg

    def test_prompt_user_construction(self):
        user = build_user_prompt("dummy", school_year=2026)
        assert "school_year=2026" in user
        assert "dummy" in user
