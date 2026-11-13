"""Import des devoirs Padlet → liste de tâches Home Assistant.

Padlet API wishes → pièces jointes ODT/DOCX/DOC → texte brut
→ LLM (``ai_task`` de HA) → JSON {days: [{date, day_name, items}]}
→ une tâche par sous-devoir, échéance = jour précis.
"""

from __future__ import annotations

__version__ = "1.0.0"
