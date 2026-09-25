"""
The multilingual keyword engine, checked against real Upbit notice titles.

Before these fixes the engine called 1 of 80 real Upbit notices and 0 of 98
measured events. The failures were structural, not just vocabulary: a phrase
containing a shorter phrase of the opposite sign cancelled itself out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from event_study import events as ev
from multilingual_alpha_engine import MultilingualNewsAlphaEngine

NOTICES = json.loads((Path(__file__).parent / "fixtures" / "upbit_notices.json").read_text(encoding="utf-8"))
engine = MultilingualNewsAlphaEngine()


def impact(headline: str, language: str = "ko") -> float:
    return engine.evaluate_headline(headline, language).expected_impact


def call(headline: str, language: str = "ko") -> int:
    x = impact(headline, language)
    return 0 if abs(x) <= 0.1 else (1 if x > 0 else -1)


@pytest.mark.parametrize("title, expected", [
    ("클러스터프로토콜(CP) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", 1),
    ("바이프로스트(BFC) KRW, USDT 마켓 디지털 자산 추가", 1),
    ("아이콘(ICX) 거래지원 종료 안내 (10/19 15:00)", -1),
    ("소폰(SOPH) 거래 유의 종목 지정 안내", -1),
    ("만트라(MANTRA) 거래 유의 종목 지정 기간 연장 안내", -1),
    ("인젝티브(INJ) 거래 유의 종목 지정 해제 안내", 1),      # lifted: contains "designated" too
    ("샌드박스(SAND) 유의 촉구 안내", -1),
])
def test_upbit_templates_get_the_call_their_wording_implies(title, expected):
    assert call(title) == expected


@pytest.mark.parametrize("headline, language", [
    ("상장폐지 안내", "ko"),                      # contains 상장 (listing)
    ("Token XYZ delisted from exchange", "en"),   # contains "listed"
    ("ABC 上場廃止のお知らせ", "ja"),              # contains 上場 (listing)
])
def test_a_phrase_does_not_cancel_itself_out(headline, language):
    assert call(headline, language) == -1


@pytest.mark.parametrize("headline", ["Bank earnings beat estimates", "Global hackathon opens today"])
def test_english_terms_are_whole_words(headline):
    assert call(headline, "en") == 0              # "ban" is not in "bank", "hack" is not "hackathon"


def test_korean_spacing_does_not_matter():
    assert engine.match_terms("거래 지원 안내", "ko") == engine.match_terms("거래지원 안내", "ko")


def test_each_term_counts_once():
    assert impact("hack hack hack", "en") == pytest.approx(-0.50)


def test_every_classified_fixture_notice_now_gets_a_call():
    """All 79 classified notices in the fixture — before the fix, 1 did."""
    kinds = [(ev.classify(n["title"]), n["title"]) for n in NOTICES]
    missed = [t for k, t in kinds if k != "other" and call(t) == 0]
    assert missed == []


def test_call_direction_follows_notice_kind():
    expected = {"listing": 1, "caution_off": 1, "delisting": -1, "caution_on": -1,
                "caution_extended": -1, "warning": -1}
    for n in NOTICES:
        kind = ev.classify(n["title"])
        if kind in expected:
            assert call(n["title"]) == expected[kind], n["title"]
