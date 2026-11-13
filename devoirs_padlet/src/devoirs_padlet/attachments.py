"""Extraction de texte depuis les pièces jointes ODT/DOCX/DOC.

Formats supportés :
- ODT : ZIP + ``content.xml`` (namespaced ``text:p``, ``text:h``)
- DOCX : ZIP + ``word/document.xml`` (namespaced ``w:p``, ``w:t``)
- DOC : binaire Word 97-2003 → via ``catdoc`` (subprocess système)

Le .doc est le seul qui sort de la stdlib pure : on pipe les bytes dans
``catdoc -`` (équivalent d'antiword, souvent préinstallé). Si catdoc
n'est pas dispo, l'extraction renvoie ``""`` et un warning.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from typing import Callable
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


# Tags de paragraphes qu'on déconcatène avec ``\\n``.
# Tous les autres tags descendants (``text:span``, ``w:r``) sont
# parcourus récursivement et leur texte concaténé.

_ODT_TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_NS = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
}

_DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def _para_text_odt(p: ET.Element) -> str:
    """Texte d'un paragraphe ODT. Concatène tout le contenu descendant."""
    return "".join(p.itertext()).strip()


def _para_text_docx(p: ET.Element) -> str:
    """Texte d'un paragraphe DOCX. Concatène tous les ``w:t`` descendants."""
    parts: list[str] = []
    for t in p.iter("{%s}t" % _DOCX_NS["w"]):
        if t.text:
            parts.append(t.text)
    return "".join(parts).strip()


def extract_text_odt(data: bytes) -> str:
    """Extrait le texte d'un ODT (zip → content.xml → concat paragraphes)."""
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
        if "content.xml" not in zf.namelist():
            logger.warning("ODT sans content.xml")
            return ""
        with zf.open("content.xml") as f:
            tree = ET.parse(f)
    root = tree.getroot()

    # Stratégie : on énumère tous les paragraphes <text:p> dans l'ordre
    # du document, peu importe leur parent. Ça couvre les en-têtes (text:h
    # contient un text:p), les cellules de tableau (table:table-cell), etc.
    lines: list[str] = []
    for p in root.iter(f"{_ODT_TEXT_NS}p"):
        line = _para_text_odt(p)
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_text_docx(data: bytes) -> str:
    """Extrait le texte d'un DOCX (zip → word/document.xml → concat paragraphes)."""
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            logger.warning("DOCX sans word/document.xml")
            return ""
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)
    root = tree.getroot()

    lines: list[str] = []
    for p in root.iter(f"{{{_DOCX_NS['w']}}}p"):
        line = _para_text_docx(p)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _find_doc_extractor() -> str | None:
    """Trouve un binaire capable d'extraire du texte depuis un DOC.

    On préfère ``catdoc`` (souvent préinstallé) à ``antiword`` (équivalent
    historique). Renvoie le nom du binaire ou ``None``.
    """
    for bin_name in ("catdoc", "antiword"):
        if shutil.which(bin_name):
            return bin_name
    return None


def extract_text_doc(data: bytes) -> str:
    """Extrait le texte d'un DOC binaire via ``catdoc -`` (ou antiword).

    Le DOC est un format binaire CFB (Compound File Binary), pas un ZIP
    — d'où l'outil externe. ``catdoc`` lit sur stdin et écrit le texte
    sur stdout, parfait pour un pipe.
    """
    bin_name = _find_doc_extractor()
    if not bin_name:
        logger.warning(
            "Pas d'extracteur DOC disponible (ni catdoc, ni antiword). "
            "Installer catdoc : apt install catdoc"
        )
        return ""
    try:
        proc = subprocess.run(
            [bin_name, "-"],
            input=data,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Atelier DOC timeout après 30s")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Extracteur DOC erreur : %s", exc)
        return ""

    if proc.returncode != 0:
        logger.warning("Extracteur DOC rc=%d : %s", proc.returncode, proc.stderr[:200])
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


_EXTRACTORS: dict[str, Callable[[bytes], str]] = {
    "odt": extract_text_odt,
    "docx": extract_text_docx,
    "doc": extract_text_doc,
}


def extract_text(extension: str, data: bytes) -> str:
    """Dispatch par extension. Renvoie ``""`` si format non supporté."""
    ext = extension.lower().lstrip(".")
    extractor = _EXTRACTORS.get(ext)
    if not extractor:
        logger.info("Pas d'extracteur pour l'extension %r", ext)
        return ""
    return extractor(data)


def download_attachment(url: str, *, timeout: int = 60) -> bytes:
    """Télécharge l'URL (signée Padlet). Lève une exception réseau en cas d'échec."""
    logger.info("Download attachment : %s…", url[:80])
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers={"User-Agent": "devoirs-padlet/0.2"})
        r.raise_for_status()
        return r.content


def fetch_attachment_text(item, *, timeout: int = 60) -> str:
    """Pipeline complet pour un ``PadletItem`` : download + extraction.

    Renvoie ``""`` si pas d'attachment ou extension non supportée.
    """
    if not item.is_devoir_file:
        return ""
    try:
        data = download_attachment(item.attachment_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Download échec pour %s : %s", item.uid_seed, exc)
        return ""
    text = extract_text(item.extension, data)
    logger.info("Texte extrait (%s) pour %s : %d caractères",
                item.extension, item.uid_seed, len(text))
    return text
