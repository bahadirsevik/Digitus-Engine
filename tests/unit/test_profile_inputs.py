# -*- coding: utf-8 -*-
"""Dondurulmuş profil girdileri: DB'siz, ağsız."""
import json

import pytest

from app.core.benchmark import profile_inputs as pi

TEXT = "Örnek Mobilya masif ahşap mobilya üretir. " * 12  # >= 200 karakter


@pytest.fixture(autouse=True)
def tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "PROFILE_INPUTS_DIR", tmp_path / "pi")
    return tmp_path / "pi"


def ok_crawl(url):
    return {"site_content": TEXT, "source_pages": [{"url": url}],
            "error": None, "content_quality": {"usable": True}}


def test_freeze_writes_once_and_refuses_overwrite(tmp_dir):
    p = pi.freeze_profile_input(7, company_url="https://x.example",
                                preliminary_info="B2C", crawl=ok_crawl)
    assert p == tmp_dir / "7.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["workspace_id"] == 7 and doc["site_content"] == TEXT
    assert b"\r\n" not in p.read_bytes()
    with pytest.raises(FileExistsError):
        pi.freeze_profile_input(7, company_url="u", preliminary_info=None, crawl=ok_crawl)
    pi.freeze_profile_input(7, company_url="u", preliminary_info=None,
                            crawl=ok_crawl, overwrite=True)
    assert pi.load_profile_input(7)["preliminary_info"] is None


def test_crawl_error_writes_nothing(tmp_dir):
    bad = lambda u: {"site_content": "", "source_pages": [], "error": "boom"}
    with pytest.raises(ValueError):
        pi.freeze_profile_input(8, company_url="u", preliminary_info=None, crawl=bad)
    assert not tmp_dir.exists() or not list(tmp_dir.iterdir())


def test_load_verifies_sha_and_missing(tmp_dir):
    with pytest.raises(FileNotFoundError):
        pi.load_profile_input(9)
    p = pi.freeze_profile_input(9, company_url="u", preliminary_info="a", crawl=ok_crawl)
    assert pi.load_profile_input(9)["site_content"] == TEXT
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["site_content"] += "x"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        pi.load_profile_input(9)


def test_extract_profile_frozen_passes_literal_strings():
    class Fake:
        def extract_profile_from_site_content(self, content, info):
            return (content, info)

    frozen = {"site_content": "S", "preliminary_info": "I"}
    assert pi.extract_profile_frozen(Fake(), frozen) == ("S", "I")


@pytest.mark.parametrize("info", ["Hedef: ofisler", None])
def test_build_profile_prompt_matches_extractor(info):
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    class FakeAI:
        prompts = []

        def complete_json(self, prompt, max_tokens=0, response_schema=None):
            self.prompts.append(prompt)
            return json.dumps({"company_name": "Örnek", "sector": "mobilya",
                               "products": ["masa"], "services": [],
                               "target_audience": "ev", "use_cases": [],
                               "problems_solved": [], "brand_terms": [],
                               "exclude_themes": []})

    ai = FakeAI()
    ProfileExtractor(ai).extract_profile_from_site_content(TEXT, info)
    assert ai.prompts == [pi.build_profile_prompt(
        {"site_content": TEXT, "preliminary_info": info})]
