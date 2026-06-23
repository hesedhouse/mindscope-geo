"""MindScope GEO -- 요금제 정의 및 제한 체크 로직."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

PLANS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "max_engines": 2,
        "max_prompts": 5,
        "max_brands": 1,
        "max_competitors": 2,
        "daily_scan_limit": 1,
        "auto_scan": False,
        "report": False,
        "features": [
            "ChatGPT + Gemini",
            "프롬프트 5개",
            "수동 스캔 일 1회",
        ],
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 290000,
        "max_engines": 4,
        "max_prompts": 30,
        "max_brands": 1,
        "max_competitors": 3,
        "daily_scan_limit": -1,  # unlimited
        "auto_scan": True,
        "report": True,
        "features": [
            "AI 엔진 4개",
            "프롬프트 30개",
            "매일 자동 스캔",
            "HTML 리포트",
            "경쟁사 3개",
        ],
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 790000,
        "max_engines": 5,
        "max_prompts": 100,
        "max_brands": 5,
        "max_competitors": 10,
        "daily_scan_limit": -1,
        "auto_scan": True,
        "report": True,
        "features": [
            "AI 엔진 5개 + AI Overview",
            "프롬프트 100개",
            "매일 자동 스캔",
            "HTML + PDF 리포트",
            "경쟁사 10개",
            "브랜드 5개",
            "월 1회 최적화 컨설팅",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": -1,  # custom pricing
        "max_engines": -1,
        "max_prompts": -1,
        "max_brands": -1,
        "max_competitors": -1,
        "daily_scan_limit": -1,
        "auto_scan": True,
        "report": True,
        "features": [
            "무제한 엔진 / 프롬프트 / 브랜드",
            "전담 매니저",
            "커스텀 리포트",
            "API 접근",
        ],
    },
}


def get_plan(plan_name: str) -> dict:
    """요금제 정보를 반환합니다. 유효하지 않으면 free를 반환."""
    return PLANS.get(plan_name, PLANS["free"])


def check_limit(plan_name: str, resource: str, current_count: int) -> bool:
    """리소스 제한 초과 여부를 확인합니다.

    Returns:
        True: 제한 내 (사용 가능)
        False: 제한 초과 (사용 불가)
    """
    plan = get_plan(plan_name)
    limit_key = f"max_{resource}"

    if limit_key not in plan:
        return True  # 해당 리소스에 대한 제한이 정의되지 않음

    limit = plan[limit_key]
    if limit == -1:  # unlimited
        return True

    return current_count < limit


def check_daily_scan_limit(plan_name: str, today_scan_count: int) -> bool:
    """일일 스캔 제한 확인.

    Returns:
        True: 스캔 가능
        False: 일일 제한 초과
    """
    plan = get_plan(plan_name)
    limit = plan.get("daily_scan_limit", 1)

    if limit == -1:
        return True

    return today_scan_count < limit


def format_price(plan_name: str) -> str:
    """요금제 가격을 포맷팅된 문자열로 반환."""
    plan = get_plan(plan_name)
    price = plan["price_monthly"]

    if price == 0:
        return "무료"
    elif price == -1:
        return "별도 문의"
    else:
        return f"₩{price:,}/월"
