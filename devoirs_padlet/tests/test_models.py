"""Tests unitaires pour ``devoirs_padlet.models``."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from devoirs_padlet.models import (
    DevoirItem,
    ItemKind,
    _parse_date_in_text,
    _parse_dt_in_text,
    _parse_time_in_text,
    classify,
)


class TestParseDate:
    def test_dd_mm_yyyy_slash(self):
        assert _parse_date_in_text("le 18/09/2026", published_year=2026) == (
            date(2026, 9, 18),
            "18/09/2026",
        )

    def test_dd_mm_yyyy_dash(self):
        assert _parse_date_in_text("18-09-2026", published_year=2026)[0] == date(2026, 9, 18)

    def test_dd_mm_no_year(self):
        # Sans année : on utilise published_year.
        d, _ = _parse_date_in_text("le 18/09", published_year=2026)
        assert d == date(2026, 9, 18)

    def test_jour_mois_francais(self):
        d, _ = _parse_date_in_text("le 18 septembre", published_year=2026)
        assert d == date(2026, 9, 18)

    def test_jour_septembre_court(self):
        d, _ = _parse_date_in_text("le 18 sept", published_year=2026)
        assert d == date(2026, 9, 18)

    def test_annee_2_digits(self):
        d, _ = _parse_date_in_text("le 18/09/26", published_year=2026)
        assert d == date(2026, 9, 18)

    def test_aucune_date(self):
        assert _parse_date_in_text("Devoirs de français", published_year=2026) is None


class TestParseTime:
    def test_heure_h_minutes(self):
        assert _parse_time_in_text("à 17h30") == (17, 30)

    def test_heure_colon(self):
        assert _parse_time_in_text("à 17:30") == (17, 30)

    def test_heure_h_only(self):
        assert _parse_time_in_text("à 9h") == (9, 0)

    def test_aucune_heure(self):
        assert _parse_time_in_text("demain soir") is None


class TestParseDt:
    def test_date_et_heure(self):
        dt, _ = _parse_dt_in_text("vendredi 18/09 à 17h30", fallback_year=2026)
        assert dt == datetime(2026, 9, 18, 17, 30)


class TestClassify:
    """Vérifie la classification des items Padlet en kind."""

    def test_pj_obligation(self):
        kind, due, ev_s, ev_e = classify(
            "textes de grammaire",
            attachment_url="https://example.com/file.docx",
            published_at=datetime(2026, 9, 4),
        )
        assert kind == ItemKind.DEVOIR_FICHIER
        assert ev_s is None and ev_e is None

    def test_texte_court_sans_pj(self):
        kind, due, *_ = classify(
            "Signer le carnet",
            attachment_url="",
            published_at=datetime(2026, 9, 4),
        )
        assert kind == ItemKind.DEVOIR_TEXTE

    def test_evenement_date_dans_titre(self):
        kind, due, ev_s, ev_e = classify(
            "La réunion de rentrée aura lieu le vendredi 18/09 à 17h30 dans la classe",
            attachment_url="",
            published_at=datetime(2026, 9, 4),
        )
        assert kind == ItemKind.EVENEMENT
        assert ev_s == datetime(2026, 9, 18, 17, 30)
        assert ev_e == datetime(2026, 9, 18, 19, 0)  # +1h30

    def test_texte_long_sans_pj_ignore(self):
        kind, *_ = classify(
            "Voici une longue note explicative sur ce qui va se passer cette année..." * 4,
            attachment_url="",
            published_at=datetime(2026, 9, 4),
        )
        assert kind == ItemKind.TEXTE_LIBRE

    def test_evenement_meme_sans_pj(self):
        """Un événement daté reste un événement, peu importe la PJ."""
        kind, *_ = classify(
            "Sortie scolaire 25/10",
            attachment_url="",
            published_at=datetime(2026, 9, 4),
        )
        assert kind == ItemKind.EVENEMENT


class TestDevoirItemUid:
    def test_uid_extrait_de_guid(self):
        item = DevoirItem(
            padlet_guid="https://padlet.com/enseignant/classe/wish/4032687548",
            title="x",
        )
        assert item.uid == "padlet-4032687548@devoirs-padlet"

    def test_uid_unique_par_item(self):
        a = DevoirItem(padlet_guid="...wish/12345", title="a")
        b = DevoirItem(padlet_guid="...wish/67890", title="b")
        assert a.uid != b.uid
