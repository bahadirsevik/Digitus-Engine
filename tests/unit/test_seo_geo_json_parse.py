"""SEOGEOGenerator._parse_content_json dayaniklilik testleri.

Canli hata: Gemini "Expecting property name enclosed in double quotes"
uretecek bozuk JSON dondurdu, tum kelimeler atlandi ve bulk task 0 icerikle
"completed" gorundu. Bu testler kurtarma zincirini kilitler.
"""
import pytest

from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator

REQUIRED = ['title', 'intro_paragraph', 'subheadings', 'body_sections']

VALID = (
    '{"title": "Baslik", "intro_paragraph": "Giris.", '
    '"subheadings": ["A"], "body_sections": ["Metin"]}'
)


def test_plain_valid_json():
    content = SEOGEOGenerator._parse_content_json(VALID, REQUIRED)
    assert content['title'] == 'Baslik'


def test_markdown_fence_recovered():
    raw = f"```json\n{VALID}\n```"
    content = SEOGEOGenerator._parse_content_json(raw, REQUIRED)
    assert content['intro_paragraph'] == 'Giris.'


def test_trailing_comma_recovered():
    # json.loads bunu "Expecting property name enclosed in double quotes"
    # ile reddeder — canli hatanin birebir sinifi.
    raw = (
        '{"title": "Baslik", "intro_paragraph": "Giris.", '
        '"subheadings": ["A"], "body_sections": ["Metin"],\n}'
    )
    content = SEOGEOGenerator._parse_content_json(raw, REQUIRED)
    assert content['body_sections'] == ['Metin']


def test_unbalanced_braces_recovered():
    raw = (
        '{"title": "Baslik", "intro_paragraph": "Giris.", '
        '"subheadings": ["A"], "body_sections": ["Metin"]'
    )
    content = SEOGEOGenerator._parse_content_json(raw, REQUIRED)
    assert content['title'] == 'Baslik'


def test_missing_required_field_raises():
    raw = '{"title": "Baslik", "intro_paragraph": "Giris."}'
    with pytest.raises(ValueError):
        SEOGEOGenerator._parse_content_json(raw, REQUIRED)


def test_garbage_raises():
    with pytest.raises(Exception):
        SEOGEOGenerator._parse_content_json('tamamen bozuk yanit', REQUIRED)
