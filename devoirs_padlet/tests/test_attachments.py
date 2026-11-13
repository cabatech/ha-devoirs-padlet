"""Tests pour ``devoirs_padlet.attachments``."""

from __future__ import annotations

import io
import zipfile

import pytest

from devoirs_padlet.attachments import (
    extract_text,
    extract_text_odt,
)


LIGNES = [
    "POUR LUNDI 7/09",
    "- fiche lecture de nombres",
    "- lire le chapitre 1 des apprentis sorciers",
    "POUR MARDI 8/09",
    "- réviser la table de 2",
    "- faire signer le cahier de liaison",
    "POUR JEUDI 10/09",
    "- dictée de mots n°1",
    "- apporter la tenue de sport",
    "POUR VENDREDI 11/09",
    "- relire la leçon de grammaire",
    "- couvrir les livres",
]


@pytest.fixture(scope="module")
def real_odt_bytes() -> bytes:
    """ODT synthétique au format des fiches hebdomadaires (aucune donnée réelle)."""
    paras = "".join(
        f"<text:p>{ln[:6]}<text:span>{ln[6:]}</text:span></text:p>" for ln in LIGNES
    )
    content = (
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{paras}<text:p/></office:text></office:body>"
        "</office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("content.xml", content)
    return buf.getvalue()


class TestExtractODT:
    def test_odt_extraction_reelles(self, real_odt_bytes: bytes):
        text = extract_text_odt(real_odt_bytes)
        assert "POUR LUNDI 7/09" in text
        assert "fiche lecture de nombres" in text
        assert "apprentis sorciers" in text

    def test_format_lignes(self, real_odt_bytes: bytes):
        text = extract_text_odt(real_odt_bytes)
        # Chaque paragraphe doit être sur sa propre ligne
        lines = text.split("\n")
        assert len(lines) > 10  # au moins 10 paragraphes
        for ln in lines[:5]:
            assert ln  # pas de ligne vide intercalée

    def test_odt_invalide(self):
        with pytest.raises(Exception):
            extract_text_odt(b"not a zip")

    def test_dispatch_odt(self, real_odt_bytes: bytes):
        text = extract_text("odt", real_odt_bytes)
        assert text == extract_text_odt(real_odt_bytes)


class TestExtractDocx:
    def test_dispatch_inconnu(self):
        assert extract_text("pdf", b"") == ""
        assert extract_text("xyz", b"") == ""


class TestExtractDoc:
    def test_dispatch_inclut_doc(self):
        import shutil
        if not (shutil.which("catdoc") or shutil.which("antiword")):
            import pytest
            pytest.skip("Ni catdoc, ni antiword installé")
        # On vérifie que `extract_text("doc", ...)` n'échoue pas, peu importe l'output
        result = extract_text("doc", b"not a doc")
        assert isinstance(result, str)


class TestExtractionDispatch:
    """Vérifie que le dispatch par extension marche."""

    def test_dispatch_inconnu_renvoie_vide(self):
        assert extract_text("pdf", b"") == ""
        assert extract_text("xyz", b"") == ""
        assert extract_text("", b"") == ""
