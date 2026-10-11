"""Etiket tek-kaynak sözleşmesi testleri (Codex v15-1).

Markdown → JSON aktarımı doğrulanmadan benchmark koşulamaz: yanlış/eski
JSON üzerinden değerlendirme sessiz benchmark hatasıdır.
"""
import json
from pathlib import Path

import pytest

from app.core.benchmark.annotation_io import (
    AnnotationError,
    apply_adjudications,
    apply_md_to_json,
    freeze_report,
    parse_primary_md,
    verify_adjudication_consistency,
    verify_consistency,
)

SCHEMA = {
    "intent_type": ["transactional", "commercial", "informational",
                    "navigational", "trend_worthy"],
    "gt": [0, 1],
    "ga": [0, 1],
    "ads_class": [2, 1, -1],
    "opinion_discussion": [0, 1],
    "curiosity_comparison": [0, 1],
    "agenda_theme": [0, 1],
    "relevance_band": ["high", "mid", "low"],
}


def _fill_labels():
    return {k: "FILL" for k in SCHEMA}


def _mask_for(labels):
    return {
        "intent": labels.get("intent_type") != "skip",
        "gt_ga": not any(labels.get(f) == "skip" for f in ("gt", "ga")),
        "ads_class": labels.get("ads_class") != "skip",
        "social_dims": not any(
            labels.get(f) == "skip"
            for f in ("opinion_discussion", "curiosity_comparison", "agenda_theme")
        ),
        "relevance": labels.get("relevance_band") != "skip",
    }


def _mk_json(tmp_path: Path, entries, name="annotation_sheet.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({
        "label_schema": SCHEMA,
        "entries": entries,
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _primary_row(item_id, kw, labels):
    cells = [item_id, "", kw] + labels
    return "| " + " | ".join(str(c) for c in cells) + " |"


HEADER = (
    "| item | Çift | Kelime | intent | GT | GA | ADS | opin | merak | gündem | rel |\n"
    "|------|------|--------|--------|----|----|-----|------|-------|--------|-----|\n"
)


def _mk_md(tmp_path: Path, rows, name="annotation_sheet.md") -> Path:
    p = tmp_path / name
    p.write_text(HEADER + "\n".join(rows), encoding="utf-8")
    return p


class TestApply:
    def test_valid_import_writes_labels(self, tmp_path):
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip",
            ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        stats = apply_md_to_json(md, js)
        assert stats["written"] == 8 and stats["pending_fill"] == 0
        doc = json.loads(js.read_text(encoding="utf-8"))
        assert doc["entries"][0]["labels"]["ads_class"] == 2
        assert doc["entries"][0]["labels"]["intent_type"] == "commercial"
        # Aktarım sonrası md ↔ json TUTARLI
        assert verify_consistency(md, js) == []

    def test_keyword_mismatch_rejected(self, tmp_path):
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "BAŞKA kelime", ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        with pytest.raises(AnnotationError, match="kelime uyuşmuyor"):
            apply_md_to_json(md, js)

    def test_invalid_label_rejected(self, tmp_path):
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip", ["commercial", 5, 1, 2, 0, 1, 0, "high"],
        )])
        with pytest.raises(AnnotationError, match="gt"):
            apply_md_to_json(md, js)

    def test_conflicting_existing_json_label_refused_without_force(self, tmp_path):
        labels = _fill_labels()
        labels["gt"] = 0  # JSON'da önceden yazılmış FARKLI değer
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": labels,
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip", ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        with pytest.raises(AnnotationError, match="üzerine yaz"):
            apply_md_to_json(md, js)
        # force ile ezilebilir
        stats = apply_md_to_json(md, js, force=True)
        assert stats["written"] == 8

    def test_missing_id_in_md_rejected(self, tmp_path):
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "a",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
            {"item_id": "K002", "keyword": "b",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "a", ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        with pytest.raises(AnnotationError, match="eksik kimlik"):
            apply_md_to_json(md, js)


class TestVerify:
    def test_divergence_detected(self, tmp_path):
        """Harness ön-koşulu: md güncellenip aktarım unutulursa RED."""
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": _fill_labels(),
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip", ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        diffs = verify_consistency(md, js)
        assert diffs  # md dolu, json FILL → uyumsuz
        apply_md_to_json(md, js)
        assert verify_consistency(md, js) == []


class TestFreeze:
    def _entry(self, item_id, kw, labels, *, double=False, adjudicated=None,
               notes=None, note="", mask=None):
        return {"item_id": item_id, "keyword": kw,
                "double_label_required": double, "labels": labels,
                "evaluation_mask": _mask_for(labels) if mask is None else mask,
                "adjudicated_labels": adjudicated,
                "adjudication_notes": notes or {},
                "adjudication_note": note}

    def test_freeze_blocks_pending_and_unresolved_disagreement(self, tmp_path):
        full = {k: v[0] for k, v in SCHEMA.items()}
        pending = dict(full)
        pending["gt"] = "?"
        js = _mk_json(tmp_path, [
            self._entry("K001", "a", pending),
            self._entry("K002", "b", full, double=True),
        ])
        second_full = dict(full)
        second_full["ads_class"] = -1  # 1. etiketle uyuşmuyor, adjudication yok
        sjs = _mk_json(tmp_path, [
            {"item_id": "K002", "keyword": "b", "labels": second_full},
        ], name="annotation_sheet_second.json")

        ok, problems = freeze_report(js, sjs)
        assert not ok
        text = "\n".join(problems)
        assert "K001.gt" in text
        assert "K002.ads_class" in text and "adjudicated_labels gerekli" in text

    def test_freeze_ok_when_complete_and_validly_adjudicated(self, tmp_path):
        full = {k: v[0] for k, v in SCHEMA.items()}
        js = _mk_json(tmp_path, [
            self._entry("K002", "b", full, double=True,
                        adjudicated={"ads_class": full["ads_class"]},
                        notes={"ads_class": "SERP araç gösteriyor"},
                        note="ads_class: SERP araç gösteriyor"),
        ])
        second = dict(full)
        second["ads_class"] = -1
        sjs = _mk_json(tmp_path, [
            {"item_id": "K002", "keyword": "b", "labels": second},
        ], name="annotation_sheet_second.json")
        ok, problems = freeze_report(js, sjs)
        assert ok, problems

    def test_freeze_rejects_invalid_adjudicated_value_and_empty_note(self, tmp_path):
        """Codex v16-1: adjudicated varlığı YETMEZ — değer şemaya uygun
        gerçek etiket olmalı, gerekçe dolu olmalı."""
        full = {k: v[0] for k, v in SCHEMA.items()}
        js = _mk_json(tmp_path, [
            # geçersiz değer (5 şemada yok) + boş not
            self._entry("K001", "a", full, double=True,
                        adjudicated={"ads_class": 5}, note=""),
            # geçerli değer ama boş not
            self._entry("K002", "b", full, double=True,
                        adjudicated={"ads_class": 2}, note=""),
        ])
        second = dict(full)
        second["ads_class"] = -1
        sjs = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "a", "labels": dict(second)},
            {"item_id": "K002", "keyword": "b", "labels": dict(second)},
        ], name="annotation_sheet_second.json")
        ok, problems = freeze_report(js, sjs)
        assert not ok
        text = "\n".join(problems)
        assert "K001.ads_class" in text and "geçersiz" in text
        assert "K002.ads_class" in text and "gerekçe zorunlu" in text

    def test_freeze_rejects_backdoor_adjudication(self, tmp_path):
        """Uyuşmazlık olmayan alanda / çift-olmayan maddede adjudication =
        arka kapı düzenleme — freeze reddeder."""
        full = {k: v[0] for k, v in SCHEMA.items()}
        js = _mk_json(tmp_path, [
            self._entry("K001", "a", full,  # çift DEĞİL ama adjudicated dolu
                        adjudicated={"gt": 1}, notes={"gt": "x"}, note="gt: x"),
            self._entry("K002", "b", full, double=True,
                        adjudicated={"gt": full["gt"]},
                        notes={"gt": "x"}, note="gt: x"),  # uyuşma var
        ])
        sjs = _mk_json(tmp_path, [
            {"item_id": "K002", "keyword": "b", "labels": dict(full)},
        ], name="annotation_sheet_second.json")
        ok, problems = freeze_report(js, sjs)
        assert not ok
        text = "\n".join(problems)
        assert "K001" in text and "arka kapı" in text
        assert "K002.gt" in text and "uyuşmazlık yok" in text

    def test_freeze_flags_skip_with_open_mask(self, tmp_path):
        full = {k: v[0] for k, v in SCHEMA.items()}
        skipped = dict(full)
        skipped["ads_class"] = "skip"
        js = _mk_json(tmp_path, [
            self._entry("K001", "a", skipped,
                        mask={"ads_class": True}),  # skip ama mask açık
        ])
        ok, problems = freeze_report(js, None)
        assert not ok
        assert any("ads_class" in p and "mask" in p for p in problems)

    def test_freeze_flags_closed_mask_without_skip(self, tmp_path):
        full = {k: v[0] for k, v in SCHEMA.items()}
        stale_mask = _mask_for(full)
        stale_mask["relevance"] = False
        js = _mk_json(tmp_path, [
            self._entry("K001", "a", full, mask=stale_mask),
        ])
        ok, problems = freeze_report(js, None)
        assert not ok
        assert any("evaluation_mask" in p for p in problems)

    def test_freeze_requires_note_for_each_disagreement(self, tmp_path):
        full = {k: v[0] for k, v in SCHEMA.items()}
        second = dict(full)
        second["ads_class"] = -1
        second["gt"] = 1
        js = _mk_json(tmp_path, [
            self._entry(
                "K005", "b", full, double=True,
                adjudicated={"ads_class": 2, "gt": 0},
                notes={"ads_class": "SERP araç gösteriyor"},
                note="ads_class: SERP araç gösteriyor",
            ),
        ])
        sjs = _mk_json(tmp_path, [
            {"item_id": "K005", "keyword": "b", "labels": second},
        ], name="annotation_sheet_second.json")
        ok, problems = freeze_report(js, sjs)
        assert not ok
        assert any("K005.gt" in p and "gerekçe" in p for p in problems)


class TestSkipMask:
    def test_import_skip_closes_group_mask(self, tmp_path):
        """Codex v16-4: skip'li grup evaluation_mask'te kapanır; skip
        gerçek etikete dönerse mask geri açılır (tam deterministik)."""
        js = _mk_json(tmp_path, [
            {"item_id": "K001", "keyword": "hisse takip",
             "double_label_required": False, "labels": _fill_labels(),
             "evaluation_mask": {"intent": True, "gt_ga": True,
                                 "ads_class": True, "social_dims": True,
                                 "relevance": True},
             "adjudicated_labels": None, "adjudication_note": ""},
        ])
        md = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip",
            ["commercial", "skip", 1, 2, "skip", 1, 0, "high"],
        )])
        apply_md_to_json(md, js)
        doc = json.loads(js.read_text(encoding="utf-8"))
        mask = doc["entries"][0]["evaluation_mask"]
        assert mask["gt_ga"] is False       # gt skip → grup kapalı
        assert mask["social_dims"] is False  # opinion skip → grup kapalı
        assert mask["intent"] is True and mask["ads_class"] is True

        # skip düzeltilirse mask geri açılır
        md2 = _mk_md(tmp_path, [_primary_row(
            "K001", "hisse takip",
            ["commercial", 1, 1, 2, 0, 1, 0, "high"],
        )])
        apply_md_to_json(md2, js, force=True)
        doc = json.loads(js.read_text(encoding="utf-8"))
        assert all(doc["entries"][0]["evaluation_mask"].values())


class TestAdjudicationImport:
    ADJ_HEADER = "| item | alan | karar | gerekçe |\n|--|--|--|--|\n"

    def _setup(self, tmp_path, *, primary_ads=1, second_ads=-1, double=True):
        full = {k: v[0] for k, v in SCHEMA.items()}
        primary = dict(full)
        primary["ads_class"] = primary_ads
        js = _mk_json(tmp_path, [
            {"item_id": "K005", "keyword": "b", "double_label_required": double,
             "labels": primary, "evaluation_mask": _mask_for(primary),
             "adjudicated_labels": None, "adjudication_notes": {},
             "adjudication_note": ""},
        ])
        second = dict(full)
        second["ads_class"] = second_ads
        sjs = _mk_json(tmp_path, [
            {"item_id": "K005", "keyword": "b", "labels": second},
        ], name="annotation_sheet_second.json")
        return js, sjs

    def _adj_md(self, tmp_path, rows):
        p = tmp_path / "adjudication.md"
        p.write_text(self.ADJ_HEADER + "\n".join(rows), encoding="utf-8")
        return p

    def test_valid_adjudication_applied(self, tmp_path):
        js, sjs = self._setup(tmp_path)
        adj = self._adj_md(tmp_path, [
            "| K005 | ads_class | 2 | SERP araç gösteriyor |",
        ])
        stats = apply_adjudications(adj, js, sjs)
        assert stats == {"applied": 1, "items": 1}
        doc = json.loads(js.read_text(encoding="utf-8"))
        assert doc["entries"][0]["adjudicated_labels"] == {"ads_class": 2}
        assert doc["entries"][0]["adjudication_notes"] == {
            "ads_class": "SERP araç gösteriyor"
        }
        assert "SERP" in doc["entries"][0]["adjudication_note"]
        assert verify_adjudication_consistency(adj, js, sjs) == []
        # Freeze artık geçer
        ok, problems = freeze_report(js, sjs)
        assert ok, problems

    def test_adjudication_refused_without_disagreement(self, tmp_path):
        js, sjs = self._setup(tmp_path, primary_ads=1, second_ads=1)
        adj = self._adj_md(tmp_path, [
            "| K005 | ads_class | 2 | keyfi değişiklik |",
        ])
        with pytest.raises(AnnotationError, match="arka kapı"):
            apply_adjudications(adj, js, sjs)

    def test_adjudication_refuses_skip_and_empty_note(self, tmp_path):
        js, sjs = self._setup(tmp_path)
        with pytest.raises(AnnotationError, match="geçerli bir"):
            apply_adjudications(self._adj_md(tmp_path, [
                "| K005 | ads_class | skip | metin bozuk |",
            ]), js, sjs)
        with pytest.raises(AnnotationError, match="gerekçe"):
            apply_adjudications(self._adj_md(tmp_path, [
                "| K005 | ads_class | 2 |  |",
            ]), js, sjs)

    def test_adjudication_refuses_non_double_item(self, tmp_path):
        js, sjs = self._setup(tmp_path, double=False)
        adj = self._adj_md(tmp_path, [
            "| K005 | ads_class | 2 | gerekçe |",
        ])
        with pytest.raises(AnnotationError, match="çift-etiket"):
            apply_adjudications(adj, js, sjs)

    def test_adjudication_sheet_is_complete_replacement(self, tmp_path):
        js, sjs = self._setup(tmp_path)
        populated = self._adj_md(tmp_path, [
            "| K005 | ads_class | 2 | SERP araç gösteriyor |",
        ])
        apply_adjudications(populated, js, sjs)

        empty = self._adj_md(tmp_path, [])
        diffs = verify_adjudication_consistency(empty, js, sjs)
        assert diffs
        stats = apply_adjudications(empty, js, sjs)
        assert stats == {"applied": 0, "items": 0}
        doc = json.loads(js.read_text(encoding="utf-8"))
        entry = doc["entries"][0]
        assert entry["adjudicated_labels"] is None
        assert entry["adjudication_notes"] == {}
        assert entry["adjudication_note"] == ""
        assert verify_adjudication_consistency(empty, js, sjs) == []

    def test_duplicate_adjudication_row_rejected(self, tmp_path):
        js, sjs = self._setup(tmp_path)
        duplicate = self._adj_md(tmp_path, [
            "| K005 | ads_class | 2 | bir |",
            "| K005 | ads_class | 1 | iki |",
        ])
        with pytest.raises(AnnotationError, match="yinelenen adjudication"):
            apply_adjudications(duplicate, js, sjs)


def test_duplicate_primary_markdown_id_rejected():
    row = _primary_row(
        "K001", "hisse takip", ["commercial", 1, 1, 2, 0, 1, 0, "high"]
    )
    with pytest.raises(AnnotationError, match="yinelenen kimlik"):
        parse_primary_md(HEADER + row + "\n" + row, SCHEMA)
