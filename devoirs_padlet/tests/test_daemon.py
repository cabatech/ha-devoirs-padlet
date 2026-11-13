"""Tests pour ``devoirs_padlet.daemon`` (mode APScheduler).

Note : on ne lance pas vraiment APScheduler en boucle dans les tests
(risque de bloquer). On teste plutôt :
- la fonction ``_run_once`` (mocks pour fetch + LLM)
- le sentinel ``_touch_health``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devoirs_padlet.daemon import _run_once, _touch_health


class TestTouchHealth:
    def test_creates_file(self, tmp_path: Path):
        f = tmp_path / "health"
        _touch_health(f, ok=True)
        assert f.is_file()
        d = json.loads(f.read_text())
        assert d["ok"] is True
        assert "timestamp" in d
        assert d["stats"] is None

    def test_with_stats(self, tmp_path: Path):
        f = tmp_path / "health"
        stats = MagicMock(created=1, updated=2, deleted=3, errors=0)
        _touch_health(f, ok=True, stats=stats)
        d = json.loads(f.read_text())
        assert d["stats"] == {"created": 1, "updated": 2, "deleted": 3, "errors": 0}

    def test_ko(self, tmp_path: Path):
        f = tmp_path / "health"
        _touch_health(f, ok=False)
        d = json.loads(f.read_text())
        assert d["ok"] is False


class TestRunOnce:
    """``_run_once`` fait fetch + LLM + liste HA. On mock le réseau."""

    def _make_config(self, tmp_path: Path):
        from devoirs_padlet.config import Config
        return Config(
            padlet_rss_url="",
            padlet_wall_hashid="board_test",
            ha_url="http://ha.invalid",
            ha_token="t",
            ha_todo_entity="todo.devoirs",
            ha_ai_task_entity="ai_task.test",
            data_dir=str(tmp_path),
            request_timeout=30,
            school_year=2026,
        )

    def test_run_once_calls_everything(self, monkeypatch, tmp_path: Path):
        # Mock fetch_items : 1 wish ODT
        from datetime import datetime, timezone
        from devoirs_padlet.daemon import _run_once
        from devoirs_padlet.padlet import PadletItem
        from importlib import reload
        import devoirs_padlet.pipeline as pipeline_mod
        import devoirs_padlet.daemon as daemon_mod

        fake_item = PadletItem(
            id=42, hashid="h42", subject="S1", body="",
            permalink="https://padlet.com/wish/42",
            wall_section_hashid="sec_x",
            published_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
            attachment_url="https://signed.url/s1.odt",
            attachment_filename="s1.odt", extension="odt",
        )

        monkeypatch.setattr(daemon_mod, "fetch_items", lambda *a, **kw: [fake_item])

        # Mock extract (le LLM) pour ne pas dépendre de mmx en test
        from devoirs_padlet.llm import DevoirDay, DevoirSubItem
        from datetime import date
        def fake_extract(text, wish_id, *, school_year, llm=None):
            return [DevoirDay(
                date=date(2026, 9, 7), day_name="lundi",
                items=[DevoirSubItem(
                    title="x", body="x", subject="default",
                    uid_seed=f"{wish_id}-2026-09-07-00",
                    source_wish_id=wish_id, date_due=date(2026, 9, 7),
                )],
            )]
        monkeypatch.setattr(daemon_mod, "run_pipeline",
                            lambda items, *, school_year, llm: pipeline_mod.PipelineResult(
                                days=fake_extract("dummy", items[0].id, school_year=school_year),
                            ))

        # Mock publish_to_ha
        fake_stats = MagicMock(created=1, updated=0, deleted=0, errors=0)
        monkeypatch.setattr(daemon_mod, "publish_to_ha",
                            lambda *a, **kw: fake_stats)

        cfg = self._make_config(tmp_path)
        stats = _run_once(cfg, school_year=2026)
        assert stats.created == 1
        assert stats.errors == 0
