"""
HYPERION QUANT: MULTILINGUAL NEWS ALPHA & SENTIMENT DRIFT ENGINE
================================================================
Engine for fast multilingual sentiment parsing and directional skew.
Monitors crypto and macro headlines across key Asian & Western markets:
  - Korean (KRW crypto listing alerts, Upbit/Bithumb announcements)
  - Chinese (Regulatory announcements, mining/exchange policy, macro)
  - Japanese (FSA regulatory policy, Bank of Japan macro shifts)
  - English (SEC, Fed rate decisions, ETF inflows/outflows)

Calculates instantaneous reservation price drift:
  Delta_r(t) = mu_news * sigma * e^(-Delta_t / tau)

Includes dataset generation specification formatted for Adaption Labs
"Invent a Dataset" (242 languages) and Tiny AutoScientist (0.8B-1.5B).
"""

import json
import math
import re
import time
from dataclasses import dataclass
from typing import List, Dict, Tuple

@dataclass
class NewsHeadlineEvent:
    timestamp_ns: int
    source: str
    language: str # 'ko', 'zh', 'ja', 'en'
    headline: str
    expected_impact: float # [-1.0 (extremely bearish) to +1.0 (extremely bullish)]
    confidence: float      # [0.0 to 1.0]
    half_life_sec: float   # Decay half life in seconds (e.g. 60.0s)

class MultilingualNewsAlphaEngine:
    def __init__(self, default_half_life=60.0):
        self.default_half_life = default_half_life
        self.active_news_events: List[NewsHeadlineEvent] = []
        
        # High-impact linguistic keywords across regional crypto markets
        self.bullish_lexicon = {
            # Upbit's own notice wording: "신규 거래지원" (new trading support),
            # "디지털 자산 추가" (asset added), and a caution designation lifted.
            "ko": ["상장", "입금 개시", "파트너십", "투자 유치", "거래 지원", "승인", "신규 상장",
                   "신규 거래지원", "디지털 자산 추가", "유의 종목 지정 해제"],
            "zh": ["上线", "通过", "批准", "战略合作", "融资", "利好", "支持交易", "持仓增加"],
            "ja": ["上場", "承認", "提携", "資金調達", "認可", "取扱開始", "買い増し"],
            "en": ["listed", "approved", "sec approval", "etf net inflow", "acquisition", "strategic partnership", "mainnet launch"]
        }
        
        self.bearish_lexicon = {
            # Upbit: "거래지원 종료" (trading support ends — a delisting),
            # "유의 종목 지정" (designated a caution item), "유의 촉구" (warning).
            "ko": ["유의종목", "상장폐지", "출금 중단", "해킹", "규제", "압수수색", "기소",
                   "거래지원 종료", "유의 종목 지정", "유의 촉구"],
            "zh": ["下架", "清退", "暂停提现", "黑客攻击", "立案调查", "制裁", "爆仓", "禁止"],
            "ja": ["廃止", "停止", "ハッキング", "捜査", "制裁", "警告", "不正流出", "上場廃止"],
            "en": ["delisted", "sec lawsuit", "investigation", "enforcement action", "exploit", "hack", "insolvency", "outflows", "ban"]
        }

    @staticmethod
    def _normalise(text: str, language: str) -> str:
        # Korean spacing is inconsistent — Upbit writes "거래지원" where a
        # dictionary writes "거래 지원" — so Korean is matched without spaces.
        if language == "ko":
            return re.sub(r"\s+", "", text)
        return text.lower()

    def match_terms(self, headline: str, language: str) -> List[Tuple[str, int]]:
        """Lexicon terms found in a headline, as (term, +1 bullish / -1 bearish).

        Longest phrase first, and a matched phrase uses up its text. Without
        that, every phrase that contains a shorter one of the opposite sign
        cancels itself out: "상장폐지" (delisting) also contains "상장"
        (listing), "delisted" contains "listed", "上場廃止" contains "上場" —
        and each scored +0.45 - 0.50, too weak to make any call at all.

        English terms must also stand as whole words, or "ban" fires on
        "bank" and "hack" on "hackathon". Korean, Chinese and Japanese don't
        mark word boundaries with spaces, so they rely on longest-match alone.
        """
        work = self._normalise(headline, language)
        candidates = [(t, 1) for t in self.bullish_lexicon.get(language, [])] + \
                     [(t, -1) for t in self.bearish_lexicon.get(language, [])]
        candidates.sort(key=lambda c: -len(self._normalise(c[0], language)))
        found: List[Tuple[str, int]] = []
        for term, sign in candidates:
            needle = re.escape(self._normalise(term, language))
            pattern = re.compile(rf"(?<![a-z0-9]){needle}(?![a-z0-9])" if language == "en" else needle)
            if pattern.search(work):
                found.append((term, sign))
                work = pattern.sub(lambda m: "\x00" * len(m.group(0)), work)
        return found

    def evaluate_headline(self, headline: str, language: str, source: str = "LiveWire") -> NewsHeadlineEvent:
        """Parses headline and produces structured NewsHeadlineEvent."""
        score = 0.0
        confidence = 0.5

        for _term, sign in self.match_terms(headline, language):
            if sign > 0:
                score += 0.45
                confidence = min(0.95, confidence + 0.20)
            else:
                score -= 0.50
                confidence = min(0.95, confidence + 0.25)

        impact = max(-1.0, min(1.0, score))
        event = NewsHeadlineEvent(
            timestamp_ns=time.time_ns(),
            source=source,
            language=language,
            headline=headline,
            expected_impact=impact,
            confidence=confidence,
            half_life_sec=self.default_half_life
        )
        if abs(impact) > 0.1:
            self.active_news_events.append(event)
        return event

    def compute_current_alpha_skew(self, current_time_ns: int, current_volatility: float = 0.02) -> float:
        """
        Computes the exponential time-decayed directional alpha skew in USD/basis points.
        Front-runs market price shifts before slow participants react.
        """
        total_skew = 0.0
        active_survivors = []
        
        for ev in self.active_news_events:
            dt_sec = (current_time_ns - ev.timestamp_ns) / 1e9
            if dt_sec < ev.half_life_sec * 4.0: # Keep events alive for 4 half-lives
                decay = math.exp(- (dt_sec * math.log(2)) / ev.half_life_sec)
                # Skew reservation price proportional to impact, confidence, and current volatility
                skew = ev.expected_impact * ev.confidence * (current_volatility * 100.0) * decay
                total_skew += skew
                active_survivors.append(ev)

        self.active_news_events = active_survivors
        return total_skew

    @staticmethod
    def get_adaption_dataset_spec() -> Dict:
        """
        Returns the dataset specification prompt ready for Adaption Labs
        'Invent a Dataset' and 'Tiny AutoScientist' training.
        """
        return {
            "task": "Multilingual High-Frequency Financial & Crypto Sentiment Classifier",
            "model_architecture": "Tiny AutoScientist 1.1B SLM",
            "target_languages": ["Korean (ko)", "Mandarin (zh)", "Japanese (ja)", "English (en)"],
            "prompt_for_invent_a_dataset": (
                "Generate 25,000 realistic breaking financial and crypto market news headlines across "
                "Korean (Upbit, Bithumb announcements), Chinese (Weibo, Caixin, regulatory filings), "
                "Japanese (Nikkei, FSA circulars), and English (Reuters, Bloomberg, SEC). "
                "For each headline, provide: "
                "1. Exact regional text with natural colloquial phrasing. "
                "2. Directional market impact label (-1.0 to +1.0). "
                "3. Urgency / Half-life decay duration (seconds). "
                "4. Confidence score (0.0 to 1.0)."
            ),
            "output_format": "JSONL: {'text': str, 'lang': str, 'impact': float, 'half_life': float, 'confidence': float}"
        }

if __name__ == "__main__":
    print("=" * 80)
    print("  HYPERION QUANT: MULTILINGUAL NEWS ALPHA & RESERVATION SKEW TEST")
    print("=" * 80)
    engine = MultilingualNewsAlphaEngine(default_half_life=60.0)

    test_headlines = [
        ("ko", "업비트(Upbit) 신규 디지털 자산 거래 지원 안내 (신규 상장)", "Bullish KRW Listing Alert"),
        ("zh", "某巨鲸地址被清退，大量BTC流向交易所准备抛售", "Bearish Chinese Inflow Dump"),
        ("ja", "日本の金融庁(FSA)、暗号資産ETFの取引認可を正式発表", "Bullish Japan Regulatory Approval"),
        ("en", "SEC files emergency enforcement action against major market maker", "Bearish SEC Enforcement Alert")
    ]

    now_ns = time.time_ns()
    print("\n[*] Ingesting Streaming Multilingual Regional Headlines:")
    for lang, hl, desc in test_headlines:
        event = engine.evaluate_headline(hl, lang)
        print(f"  [{lang.upper()}] \"{hl}\"")
        print(f"       Description:       {desc}")
        print(f"       Evaluated Impact:  \x1b[1m{event.expected_impact:+.2f}\x1b[0m (Confidence: {event.confidence*100:.0f}%)")
        print(f"       Decay Half-Life:   {event.half_life_sec:.0f}s\n")

    skew = engine.compute_current_alpha_skew(now_ns, current_volatility=0.03)
    print(f"[*] Aggregated Real-Time Reservation Price Skew: \x1b[1;32m{skew:+.4f} USD\x1b[0m")
    print(f"[*] Dataset spec ready for Adaption Labs 'Invent a Dataset':\n    {json.dumps(engine.get_adaption_dataset_spec(), indent=2)}")
    print("=" * 80 + "\n")

