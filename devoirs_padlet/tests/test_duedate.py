"""Tests unitaires pour ``devoirs_padlet.duedate``."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from devoirs_padlet.duedate import compute_due


class TestComputeDue:
    """Vérification de la règle métier « DUE = vendredi semaine suivante »."""

    def test_vendredi_4_septembre_2026(self):
        """Cas emblématique : publication le vendredi 04/09 → DUE vendredi 11/09."""
        published = datetime(2026, 9, 4, 8, 46)
        assert compute_due(published) == date(2026, 9, 11)

    def test_lundi_7_septembre_2026(self):
        """Lundi 07/09 = début de semaine en cours, DUE = vendredi 11/09 (fin de semaine)."""
        published = datetime(2026, 9, 7, 9, 0)
        assert compute_due(published) == date(2026, 9, 11)

    def test_mercredi(self):
        """Mercredi 09/09 → DUE = vendredi 18/09 (semaine prochaine).

        L'heuristique « lundi ≥ publication + 4 jours » est linéaire, pas
        conditionnelle. Pour les publications en milieu de semaine, on
        obtient la fin de la semaine *suivante* — comportement intentionnel :
        même si la maîtresse publie en théorie le vendredi, ce calcul
        garantit une DUE toujours ≥ publication + 4j minimum.
        """
        published = datetime(2026, 9, 9, 12, 0)
        assert compute_due(published) == date(2026, 9, 18)

    def test_samedi_26_decembre(self):
        """Cas limite fin d'année : publiée le 26/12 (samedi), DUE = 01/01/2027."""
        published = datetime(2026, 12, 26, 17, 0)
        # samedi 26/12/2026 → prochain lundi = 28/12 → vendredi = 01/01/2027
        assert compute_due(published) == date(2027, 1, 1)

    def test_samedi(self):
        """Cas dégradé : publication le samedi → DUE vendredi de la semaine prochaine (= samedi + 6)."""
        published = datetime(2026, 9, 5, 10, 0)
        # samedi 05/09 → prochain lundi = 07/09 → vendredi = 11/09
        assert compute_due(published) == date(2026, 9, 11)

    def test_input_date_pas_datetime(self):
        """``compute_due`` doit accepter aussi un objet ``date``."""
        assert compute_due(date(2026, 9, 4)) == date(2026, 9, 11)

    def test_mercredi_30_septembre(self):
        """Cas variante : 30/09 est un mercredi → DUE vendredi 09/10 (lundi + 4 jours)."""
        published = datetime(2026, 9, 30, 8, 0)
        # mercredi 30/09 → prochain lundi = 05/10 (lundi suivant)
        # vendredi = 09/10
        assert compute_due(published) == date(2026, 10, 9)
