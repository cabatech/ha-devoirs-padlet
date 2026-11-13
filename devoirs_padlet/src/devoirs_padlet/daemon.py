"""Mode daemon : lance la sync via APScheduler, en boucle infinie.

Dans l'add-on Home Assistant : une synchro au démarrage, puis selon le cron.
La sync tourne dans le thread principal (APScheduler BlockingScheduler) ; un
fichier sentinel garde le résultat de la dernière synchro.

Variables d'env utiles :
- ``DEVOIRS_CRON`` : cron-style (défaut ``0 17 * * *`` = 17h chaque jour)
- ``DEVOIRS_HEALTH_FILE`` : chemin du sentinel (défaut ``/tmp/devoirs-health``)
- ``DEVOIRS_TZ`` : timezone pour APScheduler (défaut ``Europe/Paris``)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .ha import HomeAssistant, SyncStats, publish_to_ha
from .llm import cached_llm
from .pipeline import current_school_year, run_pipeline
from .padlet import fetch_items

logger = logging.getLogger("devoirs.daemon")


def _touch_health(path: Path, ok: bool, *, stats: SyncStats | None = None) -> None:
    """Met à jour le sentinel de santé.

    Format JSON sur 1 ligne (lisible par un healthcheck basique).
    """
    import json
    payload = {
        "ok": ok,
        "timestamp": datetime.now().isoformat(),
        "stats": {
            "created": stats.created if stats else 0,
            "updated": stats.updated if stats else 0,
            "deleted": stats.deleted if stats else 0,
            "errors": stats.errors if stats else 0,
        } if stats else None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_once(config: Config, *, school_year: int) -> SyncStats:
    """Une itération de sync : fetch → LLM → liste HA."""
    logger.info("Démarrage sync (school_year=%s)", school_year)
    items = fetch_items(
        config.padlet_wall_hashid,
        timeout=config.request_timeout,
    )
    logger.info("Padlet : %d wishes", len(items))
    ha = HomeAssistant(config)
    llm = cached_llm(ha.llm, Path(config.data_dir) / "llm_cache.json")
    result = run_pipeline(items, school_year=school_year, llm=llm)
    source_urls = {it.id: it.permalink for it in items if it.is_devoir_file}
    subitems = [it for d in result.days for it in d.items]
    # Les orphelins ne sont jamais supprimés (cf. ``ha.plan_sync``).
    stats = publish_to_ha(subitems, result.fallback_items, config,
                          source_urls=source_urls, ha=ha)
    logger.info(
        "Sync terminée : +%d, ~%d, -%d, %d err",
        stats.created, stats.updated, stats.deleted, stats.errors,
    )
    return stats


def cmd_daemon(args: argparse.Namespace) -> int:
    """Boucle infinie : sync selon le cron, jusqu'à SIGTERM.

    Le scheduler APScheduler est en mode Blocking (thread principal).
    SIGTERM/SIGINT → arrêt propre (attend la fin de la sync en cours).
    """
    config = Config.from_env(env_file=args.env_file)
    config.validate()

    cron = os.environ.get("DEVOIRS_CRON", "0 17 * * *")
    tz = os.environ.get("DEVOIRS_TZ", "Europe/Paris")
    health_file = Path(os.environ.get("DEVOIRS_HEALTH_FILE", "/tmp/devoirs-health"))

    school_year = args.school_year or config.school_year or current_school_year()
    logger.info("Daemon start : cron=%r tz=%s school_year=%s", cron, tz, school_year)

    def job() -> None:
        # Garde-fou : si on a reçu SIGTERM juste avant le déclenchement du
        # job (race APScheduler), on annule plutôt que d'attaquer une sync
        # qui sera forcément interrompue (et potentiellement destructrice).
        if _shutting_down["flag"]:
            logger.warning("Sync annulée : daemon en cours d'arrêt")
            return
        try:
            stats = _run_once(config, school_year=school_year)
            if _shutting_down["flag"]:
                # SIGTERM pendant la sync : on ne touche PAS au sentinel
                # (sinon on dirait que c'est passé alors que la sync est
                # tronquée).
                logger.warning("Sync interrompue par SIGTERM, sentinel non touché")
                return
            _touch_health(health_file, ok=True, stats=stats)
        except Exception as exc:  # noqa: BLE001
            if _shutting_down["flag"]:
                logger.warning("Sync interrompue par SIGTERM (exception: %s)", exc)
                return
            logger.exception("Sync échouée : %s", exc)
            _touch_health(health_file, ok=False)

    scheduler = BlockingScheduler(timezone=tz)
    trigger = CronTrigger.from_crontab(cron, timezone=tz)
    scheduler.add_job(job, trigger, id="devoirs-sync", replace_existing=True,
                      misfire_grace_time=600, coalesce=True)

    # Sentinel initial : indique que le daemon est UP mais n'a pas encore tourné
    _touch_health(health_file, ok=True)

    # Drapeau d'arrêt : posé par le handler de signal, vérifié par le job
    # avant d'attaquer la sync. Empêche de lancer une sync pendant qu'on
    # reçoit SIGTERM (sinon : sync partielle + delete d'items existants,
    # → données perdues).
    _shutting_down = {"flag": False}

    def _shutdown(signum, frame):  # noqa: ARG001
        logger.info("Signal %s reçu, arrêt…", signum)
        _shutting_down["flag"] = True
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # `next_run_time` n'est disponible qu'après `scheduler.start()`. On
    # calcule ici la prochaine exécution via le trigger directement.
    sched_job = scheduler.get_job("devoirs-sync")
    next_run = None
    if sched_job and sched_job.trigger:
        try:
            from apscheduler.util import astimezone
            now = datetime.now(tz=astimezone(__import__("datetime").timezone.utc))
            next_run = sched_job.trigger.get_next_fire_time(None, now)
        except Exception:  # noqa: BLE001
            pass
    logger.info("Scheduler prêt, prochaine exécution : %s", next_run)
    job()  # synchro au démarrage : un redémarrage de l'add-on suffit à la relancer
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0
