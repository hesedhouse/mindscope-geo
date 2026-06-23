"""MindScope GEO — HTML 리포트 생성기."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Brand, ScanPrompt, ScanResult, VisibilityScore
from app.analysis.optimizer import GEOOptimizer


class ReportGenerator:
    """MindScope GEO 디자인 시스템 기반 standalone HTML 리포트 생성."""

    def __init__(self):
        self.optimizer = GEOOptimizer()

    async def generate_html_report(self, brand_id: int, session: AsyncSession) -> str:
        """brand_id에 대한 전체 분석 HTML 리포트를 생성.

        Returns:
            standalone HTML 문자열 (인라인 스타일, 외부 의존 없음)
        """
        # 브랜드 조회
        result = await session.execute(
            select(Brand)
            .where(Brand.id == brand_id)
            .options(selectinload(Brand.scan_prompts), selectinload(Brand.visibility_scores))
        )
        brand = result.scalar_one_or_none()
        if not brand:
            return self._error_html("브랜드를 찾을 수 없습니다.")

        # 최신 점수 (엔진별)
        latest_scores: dict[str, dict] = {}
        for vs in sorted(
            brand.visibility_scores,
            key=lambda v: v.calculated_at or datetime.min,
            reverse=True,
        ):
            if vs.engine not in latest_scores:
                latest_scores[vs.engine] = {
                    "engine": vs.engine,
                    "score": vs.score,
                    "share_of_voice": vs.share_of_voice,
                    "avg_sentiment": vs.avg_sentiment,
                    "total_prompts": vs.total_prompts,
                    "mentioned_prompts": vs.mentioned_prompts,
                }

        # 전체 평균 계산
        if latest_scores:
            avg_visibility = sum(v["score"] for v in latest_scores.values()) / len(latest_scores)
            avg_sov = sum(v["share_of_voice"] for v in latest_scores.values()) / len(latest_scores)
            avg_sentiment = sum(v["avg_sentiment"] for v in latest_scores.values()) / len(latest_scores)
        else:
            avg_visibility = avg_sov = avg_sentiment = 0

        # 스캔 결과 조회
        scan_result = await session.execute(
            select(ScanResult)
            .join(ScanPrompt)
            .where(ScanPrompt.brand_id == brand_id)
            .order_by(ScanResult.scanned_at.desc())
            .limit(100)
        )
        scan_rows = scan_result.scalars().all()

        # 프롬프트 텍스트 매핑
        prompt_ids = {r.scan_prompt_id for r in scan_rows}
        if prompt_ids:
            prompt_result = await session.execute(
                select(ScanPrompt).where(ScanPrompt.id.in_(prompt_ids))
            )
            prompt_map = {p.id: p.prompt_text for p in prompt_result.scalars().all()}
        else:
            prompt_map = {}

        scan_results_for_optimizer = [
            {
                "engine": r.engine,
                "brand_mentioned": r.brand_mentioned,
                "prompt_text": prompt_map.get(r.scan_prompt_id, ""),
                "sentiment_score": r.sentiment_score,
            }
            for r in scan_rows
        ]

        # 경쟁사 SoV 계산
        competitors = brand.competitors or []
        competitor_mentions: dict[str, int] = {brand.name: 0}
        for c in competitors:
            competitor_mentions[c] = 0

        for r in scan_rows:
            resp_lower = r.response_text.lower()
            if brand.name.lower() in resp_lower:
                competitor_mentions[brand.name] += 1
            for c in competitors:
                if c.lower() in resp_lower:
                    competitor_mentions[c] += 1

        total_mentions = sum(competitor_mentions.values())
        competitor_sov: dict[str, float] = {}
        for name, count in competitor_mentions.items():
            competitor_sov[name] = round((count / total_mentions * 100), 1) if total_mentions > 0 else 0

        # 최적화 추천 생성
        recommendations = self.optimizer.generate_recommendations(
            brand_name=brand.name,
            visibility_score=avg_visibility,
            sov_score=avg_sov,
            sentiment_score=avg_sentiment,
            scan_results=scan_results_for_optimizer,
            competitors=competitors,
        )

        # HTML 생성
        report_date = datetime.now().strftime("%Y년 %m월 %d일")
        html = self._build_html(
            brand_name=brand.name,
            report_date=report_date,
            avg_visibility=avg_visibility,
            avg_sov=avg_sov,
            avg_sentiment=avg_sentiment,
            latest_scores=latest_scores,
            competitor_sov=competitor_sov,
            scan_results_for_table=scan_results_for_optimizer[:50],
            recommendations=recommendations,
        )
        return html

    def _build_html(
        self,
        brand_name: str,
        report_date: str,
        avg_visibility: float,
        avg_sov: float,
        avg_sentiment: float,
        latest_scores: dict[str, dict],
        competitor_sov: dict[str, float],
        scan_results_for_table: list[dict],
        recommendations: list[dict],
    ) -> str:
        """HTML 리포트 문자열 조립."""

        # 엔진별 분석 테이블
        engine_rows = ""
        for engine, data in latest_scores.items():
            engine_rows += f"""
            <tr>
                <td style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06);">{self._engine_label(engine)}</td>
                <td style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06); text-align:center;">{data['score']:.1f}%</td>
                <td style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06); text-align:center;">{data['share_of_voice']:.1f}%</td>
                <td style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06); text-align:center;">{data['avg_sentiment']:+.2f}</td>
                <td style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06); text-align:center;">{data['mentioned_prompts']}/{data['total_prompts']}</td>
            </tr>"""

        # 경쟁사 비교 바 차트
        max_sov = max(competitor_sov.values()) if competitor_sov else 1
        competitor_bars = ""
        for name, sov in sorted(competitor_sov.items(), key=lambda x: x[1], reverse=True):
            bar_width = (sov / max_sov * 100) if max_sov > 0 else 0
            color = "#C6FF3D" if name == brand_name else "#FF2D5F"
            competitor_bars += f"""
            <div style="margin-bottom:12px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="color:#E2E8F0; font-size:0.85rem;">{self._escape(name)}</span>
                    <span style="color:#A0AEC0; font-size:0.85rem;">{sov:.1f}%</span>
                </div>
                <div style="background:rgba(255,255,255,0.06); border-radius:4px; height:24px; overflow:hidden;">
                    <div style="background:{color}; height:100%; width:{bar_width:.1f}%; border-radius:4px; transition:width 0.3s;"></div>
                </div>
            </div>"""

        # 프롬프트별 상세 결과
        prompt_rows = ""
        for r in scan_results_for_table:
            mentioned_icon = '<span style="color:#C6FF3D;">&#10003;</span>' if r["brand_mentioned"] else '<span style="color:#FF2D5F;">&#10007;</span>'
            sent = r.get("sentiment_score", 0) or 0
            sent_color = "#C6FF3D" if sent >= 0 else "#FF2D5F"
            prompt_rows += f"""
            <tr>
                <td style="padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.04); max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{self._escape(r.get('prompt_text', ''))}</td>
                <td style="padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.04); text-align:center;">{self._engine_label(r.get('engine', ''))}</td>
                <td style="padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.04); text-align:center;">{mentioned_icon}</td>
                <td style="padding:10px 12px; border-bottom:1px solid rgba(255,255,255,0.04); text-align:center; color:{sent_color};">{sent:+.2f}</td>
            </tr>"""

        # 최적화 추천
        recommendation_cards = ""
        for rec in recommendations:
            priority = rec["priority"]
            if priority == "high":
                badge_color = "#FF2D5F"
                badge_bg = "rgba(255,45,95,0.15)"
            elif priority == "medium":
                badge_color = "#C6FF3D"
                badge_bg = "rgba(198,255,61,0.15)"
            else:
                badge_color = "#A0AEC0"
                badge_bg = "rgba(160,174,192,0.1)"

            category_labels = {
                "technical": "기술",
                "content": "콘텐츠",
                "authority": "권위",
                "monitoring": "모니터링",
            }
            cat_label = category_labels.get(rec["category"], rec["category"])

            recommendation_cards += f"""
            <div style="background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:20px; margin-bottom:12px;">
                <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                    <span style="background:{badge_bg}; color:{badge_color}; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; text-transform:uppercase;">{priority}</span>
                    <span style="background:rgba(255,255,255,0.06); color:#A0AEC0; padding:3px 10px; border-radius:12px; font-size:0.72rem;">{cat_label}</span>
                </div>
                <h4 style="color:#FAFBFC; font-size:0.95rem; margin:0 0 8px 0;">{self._escape(rec['title'])}</h4>
                <p style="color:#A0AEC0; font-size:0.83rem; line-height:1.6; margin:0 0 8px 0;">{self._escape(rec['description'])}</p>
                <p style="color:#718096; font-size:0.78rem; margin:0;"><strong style="color:#A0AEC0;">예상 효과:</strong> {self._escape(rec['expected_impact'])}</p>
            </div>"""

        # 감성 점수 색상
        sent_color = "#C6FF3D" if avg_sentiment >= 0 else "#FF2D5F"

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MindScope GEO Report — {self._escape(brand_name)}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0B0F19;
            color: #E2E8F0;
            line-height: 1.6;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        .card {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            padding: 28px;
            margin-bottom: 24px;
        }}
        .section-title {{
            color: #FAFBFC;
            font-size: 1.1rem;
            font-weight: 700;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th {{
            padding: 12px 16px;
            text-align: center;
            color: #A0AEC0;
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        td {{
            color: #E2E8F0;
            font-size: 0.85rem;
        }}
        @media print {{
            body {{
                background: #fff;
                color: #333;
                padding: 20px;
            }}
            .card {{
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                break-inside: avoid;
            }}
            .section-title {{
                color: #1a202c;
                border-bottom-color: #dee2e6;
            }}
            th {{ color: #4a5568; border-bottom-color: #dee2e6; }}
            td {{ color: #333; border-bottom-color: #edf2f7 !important; }}
        }}
        @page {{
            size: A4;
            margin: 15mm;
        }}
    </style>
</head>
<body>
    <div class="container">

        <!-- Header -->
        <div style="text-align:center; margin-bottom:40px; padding-top:20px;">
            <div style="display:inline-flex; align-items:center; gap:10px; margin-bottom:12px;">
                <div style="width:36px; height:36px; background:linear-gradient(135deg, #C6FF3D, #00D4AA); border-radius:8px; display:flex; align-items:center; justify-content:center; font-weight:900; color:#0B0F19; font-size:1.1rem;">G</div>
                <span style="font-size:1.4rem; font-weight:800; color:#FAFBFC;">MindScope GEO</span>
            </div>
            <h1 style="font-size:1.8rem; font-weight:800; color:#FAFBFC; margin-bottom:6px;">{self._escape(brand_name)} — AI Visibility Report</h1>
            <p style="color:#718096; font-size:0.9rem;">{report_date}</p>
        </div>

        <!-- Executive Summary -->
        <div class="card">
            <div class="section-title">Executive Summary</div>
            <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:24px; text-align:center;">
                <div>
                    <div style="font-size:2.4rem; font-weight:800; color:#C6FF3D;">{avg_visibility:.1f}%</div>
                    <div style="color:#A0AEC0; font-size:0.82rem; margin-top:4px;">Visibility Score</div>
                </div>
                <div>
                    <div style="font-size:2.4rem; font-weight:800; color:#C6FF3D;">{avg_sov:.1f}%</div>
                    <div style="color:#A0AEC0; font-size:0.82rem; margin-top:4px;">Share of Voice</div>
                </div>
                <div>
                    <div style="font-size:2.4rem; font-weight:800; color:{sent_color};">{avg_sentiment:+.2f}</div>
                    <div style="color:#A0AEC0; font-size:0.82rem; margin-top:4px;">Sentiment Score</div>
                </div>
            </div>
        </div>

        <!-- 엔진별 분석 -->
        <div class="card">
            <div class="section-title">엔진별 분석</div>
            <table>
                <thead>
                    <tr>
                        <th style="text-align:left;">엔진</th>
                        <th>Visibility</th>
                        <th>SoV</th>
                        <th>Sentiment</th>
                        <th>언급/전체</th>
                    </tr>
                </thead>
                <tbody>
                    {engine_rows if engine_rows else '<tr><td colspan="5" style="text-align:center; padding:20px; color:#718096;">스캔 데이터 없음</td></tr>'}
                </tbody>
            </table>
        </div>

        <!-- 경쟁사 비교 -->
        <div class="card">
            <div class="section-title">경쟁사 비교 (Share of Voice)</div>
            {competitor_bars if competitor_bars else '<p style="color:#718096; text-align:center; padding:20px;">경쟁사 데이터 없음</p>'}
        </div>

        <!-- 프롬프트별 상세 결과 -->
        <div class="card">
            <div class="section-title">프롬프트별 상세 결과</div>
            <div style="overflow-x:auto;">
                <table>
                    <thead>
                        <tr>
                            <th style="text-align:left;">프롬프트</th>
                            <th>엔진</th>
                            <th>브랜드 언급</th>
                            <th>감성</th>
                        </tr>
                    </thead>
                    <tbody>
                        {prompt_rows if prompt_rows else '<tr><td colspan="4" style="text-align:center; padding:20px; color:#718096;">스캔 결과 없음</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- 최적화 추천 -->
        <div class="card">
            <div class="section-title">최적화 추천 (GEO Optimization Guide)</div>
            {recommendation_cards if recommendation_cards else '<p style="color:#718096; text-align:center; padding:20px;">현재 추천 사항 없음 (모든 점수가 양호)</p>'}
        </div>

        <!-- Footer -->
        <div style="text-align:center; padding:40px 0 20px; border-top:1px solid rgba(255,255,255,0.06); margin-top:20px;">
            <p style="color:#718096; font-size:0.8rem;">
                &copy; {datetime.now().year} ㈜ 헤세드코퍼레이션 &middot; MindScope Korea &middot; {report_date}
            </p>
        </div>

    </div>
</body>
</html>"""

        return html

    def _engine_label(self, engine: str) -> str:
        labels = {
            "chatgpt": "ChatGPT",
            "gemini": "Gemini",
            "perplexity": "Perplexity",
            "claude": "Claude",
        }
        return labels.get(engine, engine)

    def _escape(self, text: str) -> str:
        """HTML escape."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
        )

    def _error_html(self, message: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>Error</title></head>
<body style="background:#0B0F19; color:#FF2D5F; font-family:system-ui; display:flex; align-items:center; justify-content:center; height:100vh;">
    <h1>{self._escape(message)}</h1>
</body>
</html>"""
