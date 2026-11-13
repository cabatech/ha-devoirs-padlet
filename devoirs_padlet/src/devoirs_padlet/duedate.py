"""Calcul de la date d'échéance des devoirs.

Règle métier :

> La maîtresse pose les devoirs sur le padlet **le vendredi** pour
> l'ensemble de la semaine suivante. « Semaine 1 actuellement posté
> correspond à la semaine débutant le lundi 07/09/2026 » (publié le
> 04/09/2026, un vendredi).

→ DUE = vendredi de la semaine contenant le **lundi suivant la
publication**. Si la publication est déjà le vendredi, « lundi
suivant » = lundi de la semaine prochaine, DUE = vendredi de cette
semaine-là.

Cas particulier : publication **après** le vendredi (rattrapage) → DUE
= vendredi de la semaine suivant le lundi de la publication.

Cas particulier : publication **le lundi matin** = DUE vendredi de la
même semaine (= fin de semaine en cours).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


def _next_monday(d: date) -> date:
    """Retourne le prochain lundi ≥ ``d`` (lui-même si déjà lundi)."""
    return d + timedelta(days=(7 - d.weekday()) % 7)


def compute_due(published: datetime | date) -> date:
    """Calcule la date d'échéance (vendredi de la semaine du devoir).

    >>> from datetime import datetime
    >>> compute_due(datetime(2026, 9, 4, 8, 46))  # vendredi 04/09
    datetime.date(2026, 9, 11)
    >>> compute_due(datetime(2026, 9, 7, 9, 0))   # lundi 07/09
    datetime.date(2026, 9, 11)
    >>> compute_due(datetime(2026, 9, 9, 12, 0))  # mercredi 09/09
    datetime.date(2026, 9, 11)
    """
    if isinstance(published, datetime):
        d = published.date()
    else:
        d = published

    monday = _next_monday(d)
    friday = monday + timedelta(days=4)
    logger.debug(
        "compute_due(published=%s) → monday=%s → friday=%s",
        d, monday, friday,
    )
    return friday
