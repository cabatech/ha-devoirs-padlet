# HA - Devoirs Padlet

Add-on Home Assistant qui lit les devoirs publiés sur le Padlet d'une classe et les range dans une liste de tâches HA (`todo.*`), une tâche par devoir, avec le jour où il est à rendre comme échéance.

## Principe

```
Padlet (API wishes, publique)
  → pièces jointes ODT / DOCX / DOC → texte brut (stdlib, catdoc pour .doc)
  → LLM via le service HA ai_task.generate_data → JSON {days: [{date, items}]}
  → todo.add_item / todo.update_item sur la liste configurée
```

- Une synchro au démarrage de l'add-on, puis selon `cron` (défaut : 17 h chaque jour).
- Chaque tâche porte une ligne `devoir-id: …` dans sa description. Seules ces tâches sont touchées : la liste peut contenir d'autres tâches.
- Une tâche cochée reste cochée. Une consigne modifiée sur le Padlet met la tâche à jour.
- Rien n'est supprimé, sauf les doublons : un Padlet ou un LLM en panne n'efface jamais un devoir.
- Les devoirs dont l'échéance est passée ne sont pas créés.
- Les réponses du LLM sont gardées dans `/data/llm_cache.json` : une fiche déjà lue ne repart pas au LLM.

## Options

| Option | Exemple | Rôle |
|---|---|---|
| `padlet_wall_hashid` | `board_XXXXXXXX` | ID du Padlet (dans le HTML de la page) |
| `todo_entity` | `todo.devoirs` | Liste cible (Local To-do conseillée) |
| `ai_task_entity` | `ai_task.mon_llm` | Entité ai_task qui lit les fiches |
| `cron` | `0 17 * * *` | Heure des synchros (Europe/Paris) |

## Développement

```bash
uv sync --extra scheduler
uv run pytest
# hors add-on : HA_URL=https://ha.example HA_TOKEN=… dans .env
uv run devoirs sync --dry-run
uv run devoirs list
uv run devoirs extract-debug <wish_id>
```
