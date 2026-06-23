"""MindScope GEO — 최적화 가이드 엔진."""

from __future__ import annotations

from typing import Any


class GEOOptimizer:
    """스캔 결과를 분석하여 GEO 점수 개선을 위한 구체적 액션 아이템을 자동 생성."""

    def generate_recommendations(
        self,
        brand_name: str,
        visibility_score: float,
        sov_score: float,
        sentiment_score: float,
        scan_results: list[dict],
        competitors: list[str],
    ) -> list[dict]:
        """점수 및 스캔 결과 기반 최적화 추천 리스트 생성.

        Args:
            brand_name: 브랜드명
            visibility_score: 가시성 점수 (0~100)
            sov_score: Share of Voice 점수 (0~100)
            sentiment_score: 감성 점수 (-1.0 ~ +1.0)
            scan_results: 스캔 결과 리스트 (각각 engine, brand_mentioned, prompt_text 등 포함)
            competitors: 경쟁사 목록

        Returns:
            추천 액션 아이템 리스트
        """
        recommendations: list[dict] = []

        # 1. Visibility가 낮을 때 (< 30%)
        if visibility_score < 30:
            recommendations.extend(self._low_visibility_recommendations())

        # 2. SoV가 경쟁사보다 낮을 때 (< 25%)
        if sov_score < 25:
            recommendations.extend(self._low_sov_recommendations(competitors))

        # 3. Sentiment가 낮을 때 (< 0.3)
        if sentiment_score < 0.3:
            recommendations.extend(self._low_sentiment_recommendations())

        # 4. 엔진별 분석 — 특정 엔진에서만 낮을 때
        engine_recs = self._engine_specific_recommendations(scan_results)
        recommendations.extend(engine_recs)

        # 5. 누락 프롬프트 패턴 분석
        pattern_recs = self._missing_prompt_pattern_recommendations(
            brand_name, scan_results
        )
        recommendations.extend(pattern_recs)

        # 중복 제거 (title 기준)
        seen_titles: set[str] = set()
        unique_recs: list[dict] = []
        for rec in recommendations:
            if rec["title"] not in seen_titles:
                seen_titles.add(rec["title"])
                unique_recs.append(rec)

        # priority 순서로 정렬 (high > medium > low)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        unique_recs.sort(key=lambda r: priority_order.get(r["priority"], 99))

        return unique_recs

    # -------------------------------------------------------------------------
    # Private: 추천 생성 규칙별 메서드
    # -------------------------------------------------------------------------

    def _low_visibility_recommendations(self) -> list[dict]:
        """Visibility < 30% 일 때 추천."""
        return [
            {
                "priority": "high",
                "category": "technical",
                "title": "FAQPage Schema Markup 추가",
                "description": (
                    "웹사이트에 FAQPage Schema Markup을 추가하세요. "
                    "AI 엔진은 구조화된 데이터를 우선 참조하여 답변을 생성합니다. "
                    "FAQ 형식으로 브랜드 관련 핵심 질문과 답변을 마크업하면 인용 확률이 높아집니다."
                ),
                "expected_impact": "AI 엔진의 브랜드 인용률 15~30% 향상 기대",
                "effort": "medium",
            },
            {
                "priority": "high",
                "category": "technical",
                "title": "AI 크롤러 접근 허용 (robots.txt)",
                "description": (
                    "AI가 크롤링할 수 있도록 robots.txt에서 GPTBot, ClaudeBot을 허용하세요. "
                    "많은 웹사이트가 기본적으로 AI 봇을 차단하고 있어 콘텐츠가 학습에 반영되지 않습니다. "
                    "명시적으로 허용하면 AI 답변에 브랜드 정보가 포함될 가능성이 크게 높아집니다."
                ),
                "expected_impact": "AI 학습 데이터에 브랜드 콘텐츠 포함 가능성 확대",
                "effort": "low",
            },
            {
                "priority": "high",
                "category": "content",
                "title": "통계 데이터 포함 고품질 콘텐츠 제작",
                "description": (
                    "브랜드 관련 통계 데이터를 포함한 고품질 콘텐츠를 제작하세요. "
                    "AI는 신뢰할 수 있는 수치 데이터가 포함된 콘텐츠를 답변 소스로 우선 활용합니다. "
                    "업계 리서치, 사용자 만족도 조사 결과 등을 정기적으로 발행하면 효과적입니다."
                ),
                "expected_impact": "AI 답변 내 브랜드 통계 인용 빈도 증가",
                "effort": "high",
            },
            {
                "priority": "medium",
                "category": "content",
                "title": "Q&A 형식 콘텐츠 페이지 구축",
                "description": (
                    "Q&A 형식의 콘텐츠 페이지를 만들어 AI가 답변으로 인용하기 쉽게 하세요. "
                    "사용자들이 자주 묻는 질문에 대한 명확한 답변을 구조화하면 "
                    "AI 엔진이 해당 내용을 그대로 인용할 확률이 높아집니다."
                ),
                "expected_impact": "프롬프트-답변 매칭률 향상으로 가시성 10~20% 개선",
                "effort": "medium",
            },
        ]

    def _low_sov_recommendations(self, competitors: list[str]) -> list[dict]:
        """SoV < 25% 일 때 추천."""
        competitor_str = ", ".join(competitors[:3]) if competitors else "경쟁사"
        return [
            {
                "priority": "high",
                "category": "content",
                "title": "경쟁사 대비 차별화 콘텐츠 작성",
                "description": (
                    f"경쟁사({competitor_str}) 대비 차별화 포인트를 강조하는 콘텐츠를 작성하세요. "
                    "AI는 비교 질문에 답할 때 명확한 차별점이 기술된 소스를 우선 인용합니다. "
                    "'vs' 키워드를 포함한 비교 콘텐츠가 특히 효과적입니다."
                ),
                "expected_impact": "비교 프롬프트에서의 브랜드 언급률 향상",
                "effort": "medium",
            },
            {
                "priority": "high",
                "category": "authority",
                "title": "업계 순위/비교 기사 PR 강화",
                "description": (
                    "업계 순위 리스트, 비교 기사에 브랜드가 포함되도록 PR 활동을 강화하세요. "
                    "AI 엔진은 '추천', '순위', 'best' 등의 프롬프트에 대해 "
                    "언론사 기사와 리뷰 사이트의 리스트를 주요 소스로 활용합니다."
                ),
                "expected_impact": "추천/순위 프롬프트에서 SoV 5~15%p 개선",
                "effort": "high",
            },
            {
                "priority": "medium",
                "category": "authority",
                "title": "제3자 리뷰 사이트 평판 관리",
                "description": (
                    "제3자 리뷰 사이트에서 브랜드 평판을 관리하세요. "
                    "Google 리뷰, 네이버 블로그, 전문 리뷰 사이트 등에서의 "
                    "긍정적 평가가 AI 답변에 직접적으로 반영됩니다."
                ),
                "expected_impact": "AI 엔진 답변에서의 긍정적 언급 비율 증가",
                "effort": "medium",
            },
        ]

    def _low_sentiment_recommendations(self) -> list[dict]:
        """Sentiment < 0.3 일 때 추천."""
        return [
            {
                "priority": "high",
                "category": "content",
                "title": "부정적 콘텐츠 대응 전략 수립",
                "description": (
                    "부정적 리뷰/기사에 대한 대응 콘텐츠를 준비하세요. "
                    "AI가 참조하는 소스에 부정적 내용이 많으면 답변도 부정적으로 생성됩니다. "
                    "문제점 인정 + 개선 조치 내용을 공식 채널에 게시하여 균형을 맞추세요."
                ),
                "expected_impact": "AI 답변의 부정적 톤 완화, 감성 점수 0.2~0.4 개선",
                "effort": "medium",
            },
            {
                "priority": "medium",
                "category": "authority",
                "title": "신뢰 시그널 구조화 게시",
                "description": (
                    "고객 후기, 수상 이력, 인증 정보를 웹사이트에 구조화하여 게시하세요. "
                    "Schema.org의 Review, AggregateRating 마크업을 활용하면 "
                    "AI가 신뢰 지표를 쉽게 파악하여 긍정적 맥락으로 브랜드를 소개합니다."
                ),
                "expected_impact": "브랜드 신뢰도 인식 개선, 추천 답변 빈도 증가",
                "effort": "medium",
            },
        ]

    def _engine_specific_recommendations(self, scan_results: list[dict]) -> list[dict]:
        """엔진별 성과 차이 분석 후 특화 추천."""
        if not scan_results:
            return []

        # 엔진별 mention rate 계산
        engine_stats: dict[str, dict[str, int]] = {}
        for r in scan_results:
            engine = r.get("engine", "unknown")
            if engine not in engine_stats:
                engine_stats[engine] = {"total": 0, "mentioned": 0}
            engine_stats[engine]["total"] += 1
            if r.get("brand_mentioned"):
                engine_stats[engine]["mentioned"] += 1

        if not engine_stats:
            return []

        # 전체 평균 대비 특정 엔진이 현저히 낮은 경우
        rates = {}
        for engine, stats in engine_stats.items():
            if stats["total"] > 0:
                rates[engine] = stats["mentioned"] / stats["total"] * 100

        if not rates:
            return []

        avg_rate = sum(rates.values()) / len(rates)
        recommendations: list[dict] = []

        for engine, rate in rates.items():
            # 전체 평균보다 20%p 이상 낮은 엔진
            if rate < avg_rate - 20:
                rec = self._get_engine_specific_rec(engine, rate)
                if rec:
                    recommendations.append(rec)

        return recommendations

    def _get_engine_specific_rec(self, engine: str, rate: float) -> dict | None:
        """특정 엔진에 대한 맞춤 추천."""
        engine_recs = {
            "chatgpt": {
                "priority": "medium",
                "category": "content",
                "title": "ChatGPT 최적화: 롱폼 콘텐츠 강화",
                "description": (
                    f"ChatGPT에서의 가시성이 {rate:.1f}%로 낮습니다. "
                    "ChatGPT는 깊이 있는 롱폼 콘텐츠를 선호합니다. "
                    "2,000자 이상의 상세한 가이드, 튜토리얼, 심층 분석 콘텐츠를 작성하세요."
                ),
                "expected_impact": "ChatGPT 답변 내 브랜드 인용률 향상",
                "effort": "high",
            },
            "gemini": {
                "priority": "medium",
                "category": "technical",
                "title": "Google AI Overview 최적화: Schema Markup 강화",
                "description": (
                    f"Gemini에서의 가시성이 {rate:.1f}%로 낮습니다. "
                    "Google AI Overview는 자체 검색 인덱스와 Schema Markup을 적극 활용합니다. "
                    "Google Search Console에서 구조화 데이터 오류를 점검하고 "
                    "Product, FAQ, HowTo 스키마를 추가하세요."
                ),
                "expected_impact": "Google AI Overview에서의 브랜드 노출 증가",
                "effort": "medium",
            },
            "perplexity": {
                "priority": "medium",
                "category": "authority",
                "title": "Perplexity 최적화: 인용 가능 소스 확대",
                "description": (
                    f"Perplexity에서의 가시성이 {rate:.1f}%로 낮습니다. "
                    "Perplexity는 실시간 웹 검색 기반으로 최신 소스를 인용합니다. "
                    "최근 날짜의 블로그, 뉴스, 포럼 콘텐츠를 활발히 게시하여 "
                    "인용 소스풀에 포함되도록 하세요."
                ),
                "expected_impact": "Perplexity 답변에서의 브랜드 소스 인용 확률 증가",
                "effort": "medium",
            },
            "claude": {
                "priority": "medium",
                "category": "content",
                "title": "Claude 최적화: 정확하고 간결한 정보 제공",
                "description": (
                    f"Claude에서의 가시성이 {rate:.1f}%로 낮습니다. "
                    "Claude는 정확하고 검증 가능한 정보를 중시합니다. "
                    "공식 문서, 백서, 데이터 기반 보고서를 강화하고 "
                    "명확한 팩트 중심의 브랜드 설명을 제공하세요."
                ),
                "expected_impact": "Claude 답변에서의 브랜드 정확도 및 언급률 향상",
                "effort": "medium",
            },
        }
        return engine_recs.get(engine)

    def _missing_prompt_pattern_recommendations(
        self, brand_name: str, scan_results: list[dict]
    ) -> list[dict]:
        """brand_mentioned=False인 프롬프트들의 공통 패턴 분석."""
        if not scan_results:
            return []

        # 미언급 프롬프트 수집
        missed_prompts = [
            r.get("prompt_text", "")
            for r in scan_results
            if not r.get("brand_mentioned") and r.get("prompt_text")
        ]

        if not missed_prompts:
            return []

        # 패턴 키워드 분석
        pattern_keywords = {
            "추천": "추천 관련",
            "비교": "비교 관련",
            "순위": "순위/랭킹 관련",
            "가격": "가격/비용 관련",
            "후기": "후기/리뷰 관련",
            "장단점": "장단점 비교",
            "대안": "대안/대체제 관련",
            "best": "베스트/탑 관련",
            "어디": "구매처/선택 관련",
        }

        found_patterns: dict[str, int] = {}
        for prompt in missed_prompts:
            prompt_lower = prompt.lower()
            for keyword, label in pattern_keywords.items():
                if keyword in prompt_lower:
                    found_patterns[label] = found_patterns.get(label, 0) + 1

        recommendations: list[dict] = []

        # 가장 빈번한 미언급 패턴 Top 2에 대해 추천 생성
        sorted_patterns = sorted(found_patterns.items(), key=lambda x: x[1], reverse=True)

        for pattern_label, count in sorted_patterns[:2]:
            total_missed = len(missed_prompts)
            ratio = count / total_missed * 100 if total_missed > 0 else 0

            recommendations.append({
                "priority": "high" if ratio > 40 else "medium",
                "category": "content",
                "title": f"'{pattern_label}' 프롬프트 대응 콘텐츠 부족",
                "description": (
                    f"AI가 '{brand_name}'을(를) 언급하지 않은 프롬프트 중 "
                    f"'{pattern_label}' 유형이 {count}건({ratio:.0f}%)으로 가장 많습니다. "
                    f"해당 유형의 질문에 브랜드가 답변으로 선택될 수 있도록 "
                    f"관련 콘텐츠를 보강하세요."
                ),
                "expected_impact": f"'{pattern_label}' 유형 프롬프트에서의 가시성 개선",
                "effort": "medium",
            })

        # 전체 미언급 비율이 높으면 종합 추천 추가
        total = len(scan_results)
        missed_ratio = len(missed_prompts) / total * 100 if total > 0 else 0

        if missed_ratio > 70:
            recommendations.append({
                "priority": "high",
                "category": "monitoring",
                "title": "AI 가시성 종합 진단 필요",
                "description": (
                    f"전체 스캔 중 {missed_ratio:.0f}%에서 브랜드가 언급되지 않았습니다. "
                    "웹사이트의 AI 크롤링 접근성, 콘텐츠 품질, 외부 인용 현황을 "
                    "종합적으로 점검하고 우선순위를 재설정할 필요가 있습니다."
                ),
                "expected_impact": "전반적인 AI 가시성 전략 재정립",
                "effort": "high",
            })

        return recommendations
