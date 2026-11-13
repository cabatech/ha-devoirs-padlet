"""CLI principale : ``devoirs sync`` / ``list`` / ``check`` / ``extract-debug`` / ``daemon``.

Nouvelles options liées au pipeline LLM :
- ``devoirs sync`` orchestre padlet → attachments → LLM → liste HA
- ``devoirs extract-debug <padlet_wish_id>`` : télécharge l'attachment
  du post, l'extrait, le passe au LLM, affiche le résultat JSON sans
  toucher la liste HA. Idéal pour itérer sur le prompt.

Variables d'env utiles :
- ``PADLET_WALL_HASHID`` : ID du padlet (récupéré dans le HTML ou la
  page d'export). Ex : ``board_XXXXXXXXXXXX``.
- ``SCHOOL_YEAR`` (optionnel, défaut = année courante via
  ``pipeline.current_school_year``)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from typing import Sequence

from pathlib import Path

from .config import Config
from .ha import HomeAssistant, marker_of, publish_to_ha
from .llm import cached_llm, extract_devoirs
from .pipeline import current_school_year, run_pipeline
from .padlet import fetch_items

try:
    from .daemon import cmd_daemon
except ImportError:  # APScheduler optionnel non installé
    cmd_daemon = None

logger = logging.getLogger("devoirs")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------- Sous-commandes ----------

def cmd_check(args: argparse.Namespace) -> int:
    """Test connectivité Padlet + HA sans pousser."""
    config = Config.from_env(env_file=args.env_file)
    config.validate()
    if not config.padlet_wall_hashid:
        print("PADLET_WALL_HASHID non défini (récupérer depuis le HTML du padlet)")
        return 1
    items = fetch_items(config.padlet_wall_hashid, timeout=config.request_timeout)
    print(f"# Padlet : {len(items)} wishes")
    for it in items:
        flag = "📄" if it.is_devoir_file else "·"
        print(f"  {flag} {it.subject[:60]} (ext={it.extension or '-'})")

    return cmd_list(args)


def cmd_sync(args: argparse.Namespace) -> int:
    """Pipeline complet : fetch → extract texte → LLM → liste HA."""
    config = Config.from_env(env_file=args.env_file)
    config.validate()
    if not config.padlet_wall_hashid:
        print("PADLET_WALL_HASHID non défini.")
        return 1

    items = fetch_items(config.padlet_wall_hashid, timeout=config.request_timeout)
    logger.info("Padlet : %d wishes (%d avec attachment devoirs)",
                len(items), sum(1 for it in items if it.is_devoir_file))

    school_year = args.school_year or config.school_year or current_school_year()
    ha = HomeAssistant(config)
    llm = cached_llm(ha.llm, Path(config.data_dir) / "llm_cache.json")
    result = run_pipeline(items, school_year=school_year, llm=llm)

    # Source URLs : wish_id → permalink (utilisé pour le back-link dans le VTODO)
    source_urls = {it.id: it.permalink for it in items if it.is_devoir_file}

    print(f"# Pipeline : {len(items)} wishes")
    print(f"           {result.total_subitems} sous-devoirs (LLM)")
    print(f"           {len(result.fallback_items)} items fallback")
    print(f"           {len(result.skipped)} ignorés")

    if args.dry_run:
        print()
        for day in result.days:
            print(f"  📅 {day.date.isoformat()} ({day.day_name})")
            for it in day.items:
                print(f"    - [{it.subject}] {it.title}")
                if it.body and it.body != it.title:
                    print(f"      ↳ {it.body[:120]}")
        for it in result.fallback_items:
            extra = f"DTSTART={it.event_start}" if it.kind.value == "evenement" else f"DUE={it.due or '?'}"
            print(f"  ⮕ ({it.kind.value}) {it.title} — {extra}")
        if args.json:
            print()
            print(json.dumps(
                {"days": [
                    {"date": d.date.isoformat(), "day_name": d.day_name,
                     "items": [asdict(it) for it in d.items]}
                    for d in result.days
                ], "fallback": [asdict(it) for it in result.fallback_items]},
                indent=2, default=str,
            ))
        return 0

    stats = publish_to_ha(
        [it for d in result.days for it in d.items],
        result.fallback_items,
        config,
        source_urls=source_urls,
        ha=ha,
    )
    print(
        f"Sync terminée : +{stats.created} créés, ~{stats.updated} maj, "
        f"-{stats.deleted} doublons supprimés, {stats.errors} erreur(s)"
    )
    return 0 if stats.errors == 0 else 1


def cmd_list(args: argparse.Namespace) -> int:
    config = Config.from_env(env_file=args.env_file)
    config.validate()
    rows = [it for it in HomeAssistant(config).list_items() if marker_of(it.get("description"))]
    print(f"\n# {config.ha_todo_entity} : {len(rows)} devoir(s)")
    for it in rows:
        print(f"  [{it.get('status', '?')}] {it.get('due', '-')}  {it.get('summary', '')}")
    return 0


def cmd_extract_debug(args: argparse.Namespace) -> int:
    """Télécharge attachment d'un wish, extrait, passe au LLM, affiche JSON.

    Sans toucher CalDAV. Utile pour itérer sur le prompt.
    """
    config = Config.from_env(env_file=args.env_file)
    config.validate()
    items = fetch_items(config.padlet_wall_hashid, timeout=config.request_timeout)
    target = None
    try:
        wid = int(args.wish_id)
        target = next((it for it in items if it.id == wid), None)
    except ValueError:
        pass
    if target is None:
        print(f"Wish {args.wish_id!r} introuvable parmi {len(items)} wishes")
        return 1

    print(f"# Wish {target.id} : {target.subject!r}")
    print(f"# URL signée : {target.attachment_url[:120]}…")
    from .attachments import fetch_attachment_text
    text = fetch_attachment_text(target, timeout=config.request_timeout)
    print()
    print(f"# Texte extrait ({len(text)} caractères) :")
    print("=" * 70)
    print(text)
    print("=" * 70)
    print()

    school_year = args.school_year or current_school_year()
    days = extract_devoirs(
        text, source_wish_id=target.id, school_year=school_year,
        llm=HomeAssistant(config).llm,
    )
    print(f"# Sortie LLM parsée : {len(days)} jours")
    print(json.dumps([
        {"date": d.date.isoformat(), "day_name": d.day_name,
         "items": [asdict(it) for it in d.items]}
        for d in days
    ], indent=2, default=str))
    return 0


# ---------- Parser ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="devoirs", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--school-year", type=int, default=0,
                   help="override année scolaire (défaut = année courante)")
    sub = p.add_subparsers(dest="command", required=True)

    sync_p = sub.add_parser("sync", help="Importer Padlet → liste HA via LLM")
    sync_p.add_argument("--dry-run", action="store_true")
    sync_p.add_argument("--json", action="store_true")
    sync_p.set_defaults(func=cmd_sync)

    check_p = sub.add_parser("check", help="Tester connectivité")
    check_p.set_defaults(func=cmd_check)

    list_p = sub.add_parser("list", help="Lister les devoirs de la liste HA")
    list_p.set_defaults(func=cmd_list)

    ex_p = sub.add_parser("extract-debug",
                          help="Affiche texte + JSON LLM pour un wish précis")
    ex_p.add_argument("wish_id", help="ID entier du wish (visible via 'check')")
    ex_p.set_defaults(func=cmd_extract_debug)

    if cmd_daemon is not None:
        dae_p = sub.add_parser(
            "daemon",
            help="Boucle infinie avec sync planifiée via APScheduler (mode container)",
        )
        dae_p.set_defaults(func=cmd_daemon)
    else:
        dae_p = sub.add_parser(
            "daemon",
            help="(indisponible : installer `pip install '.[scheduler]'`)",
        )

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
