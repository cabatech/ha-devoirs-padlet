"""Synchro vers la liste HA : planification et cache LLM (zéro réseau)."""

from __future__ import annotations

from datetime import date

from devoirs_padlet.ha import MARKER, plan_sync, publish_to_ha
from devoirs_padlet.llm import DevoirSubItem, cached_llm

TODAY = date(2026, 11, 13)


def _want(title: str, key: str, due: str = "2026-11-20") -> dict:
    return {"item": title, "description": f"consigne\n{MARKER}{key}", "due_date": due}


def _have(uid: str, title: str, key: str | None, due: str = "2026-11-20",
          status: str = "needs_action") -> dict:
    desc = "consigne" + (f"\n{MARKER}{key}" if key else "")
    return {"uid": uid, "summary": title, "description": desc, "due": due, "status": status}


def test_plan_sync():
    wanted = {
        "k-same": _want("Lire p. 12", "k-same"),
        "k-changed": _want("Dictée 4", "k-changed"),
        "k-new": _want("Table de 7", "k-new"),
        "k-past": _want("Vieux devoir", "k-past", due="2026-11-12"),
    }
    existing = [
        _have("u1", "Lire p. 12", "k-same", status="completed"),
        _have("u2", "Dictée 3", "k-changed", status="completed"),
        _have("u3", "Lire p. 12", "k-same"),          # doublon
        _have("u4", "Ancien devoir", "k-orphan"),     # orphelin : gardé
        _have("u5", "Acheter du pain", None),         # tâche étrangère : intacte
    ]
    to_create, to_update, to_remove = plan_sync(wanted, existing, today=TODAY)
    assert [p["item"] for p in to_create] == ["Table de 7"]
    assert [(uid, p["item"]) for uid, p in to_update] == [("u2", "Dictée 4")]
    assert to_remove == ["u3"]


def test_plan_sync_is_stable():
    wanted = {"k": _want("Lire", "k")}
    # HA peut renvoyer une description avec des fins de ligne différentes
    existing = [{**_have("u1", "Lire", "k"), "description": f"consigne\r\n{MARKER}k\n"}]
    assert plan_sync(wanted, existing, today=TODAY) == ([], [], [])


class FakeHA:
    todo = "todo.devoirs"

    def __init__(self, items):
        self.items, self.added, self.updated, self.removed = items, [], [], []

    def list_items(self):
        return self.items

    def add_item(self, p):
        self.added.append(p)

    def update_item(self, uid, p):
        self.updated.append(uid)

    def remove_items(self, uids):
        self.removed.extend(uids)


def test_publish_to_ha_end_to_end():
    item = DevoirSubItem(title="Lire", body="Lire la page 12", subject="lecture",
                         uid_seed="42-2026-11-20-00", source_wish_id=42,
                         date_due=date(2026, 11, 20))
    ha = FakeHA([])
    stats = publish_to_ha([item], [], config=None, source_urls={42: "https://p/42"},
                          today=TODAY, ha=ha)
    assert stats.created == 1 and stats.errors == 0
    p = ha.added[0]
    assert p["due_date"] == "2026-11-20"
    assert "Matière : lecture" in p["description"]
    assert p["description"].endswith(MARKER + item.uid)
    # deuxième passe : rien à faire
    ha2 = FakeHA([{"uid": "u1", "summary": p["item"], "description": p["description"],
                   "due": p["due_date"], "status": "completed"}])
    stats2 = publish_to_ha([item], [], config=None, source_urls={42: "https://p/42"},
                           today=TODAY, ha=ha2)
    assert (stats2.created, stats2.updated, stats2.deleted) == (0, 0, 0)


def test_cached_llm(tmp_path):
    calls = []

    def llm(system, user):
        calls.append(user)
        return '{"days": []}' if user == "ok" else "pas du json"

    cached = cached_llm(llm, tmp_path / "cache.json")
    assert cached("s", "ok") == '{"days": []}'
    assert cached("s", "ok") == '{"days": []}'
    cached("s", "ko")
    cached("s", "ko")
    assert calls == ["ok", "ko", "ko"]  # la réponse illisible n'est pas mise en cache
