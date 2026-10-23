# -*- coding: utf-8 -*-
"""
PushPlus (pushplus.plus) notification service.

Delivers the Epic claim summary to WeChat via PushPlus's cloud API
(``POST https://www.pushplus.plus/send``). Requires only a user token
(``PUSHPLUS_TOKEN``), which the operator obtains after scanning a QR code on
https://www.pushplus.plus.

The title carries a dense one-line result summary (e.g.
``Epic 周免领取:新领2/共7款``); the content carries the full game list as plain
text (``template=txt`` so newlines are preserved without any markdown/HTML
interpretation of game titles).

Import boundary: this module must NOT import ``services.telegram_notification_service``
or ``services.epic_games_service`` — the test harness stubs dependencies per
module, and cross-importing another service would silently break that stubbing.
"""

import os

import httpx
from loguru import logger

from services.epic_collection_summary_service import CollectionSummary

PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def pushplus_notifications_enabled() -> bool:
    return bool(_env("PUSHPLUS_TOKEN"))


def _claim_status(summary: CollectionSummary) -> str:
    if summary.error_message and summary.newly_claimed_promotions:
        return "部分成功"
    if summary.error_message:
        return "失败"
    if summary.unconfirmed_promotions:
        return "需确认"
    return "成功"


def _format_games(games) -> str:
    if not games:
        return "无"
    return "\n".join(f"- {game.title or game.url or 'Unknown'}" for game in games)


def _first_error_line(error: str) -> str:
    for line in error.splitlines():
        line = line.strip()
        if line:
            return line
    return error.strip()


def build_pushplus_title(summary: CollectionSummary) -> str:
    """Dense single-line summary title for the push notification."""
    status = _claim_status(summary)
    prefix = "Epic 周免领取"
    n_all = len(summary.all_promotions)
    n_new = len(summary.newly_claimed_promotions)
    n_failed = len(summary.failed_promotions)
    n_unconfirmed = len(summary.unconfirmed_promotions)

    if status == "失败":
        reason = _first_error_line(summary.error_message) or "未知原因"
        return f"{prefix}失败:{reason}"

    segments = []
    if n_new:
        segments.append(f"新领{n_new}")
    elif n_all and status == "成功":
        segments.append("均已有")
    if n_failed:
        segments.append(f"失败{n_failed}")
    if n_unconfirmed:
        segments.append(f"待确认{n_unconfirmed}")

    tail = ""
    if not segments:
        segments.append(f"共{n_all}款")
    elif not n_failed and not n_unconfirmed and status != "部分成功":
        tail = f"/共{n_all}款"

    return f"{prefix}:{' '.join(segments)}{tail}"


def build_pushplus_content(summary: CollectionSummary, *, account_label: str | None = None) -> str:
    """Full plain-text run report."""
    lines = []
    if account_label:
        lines.append(f"账号：{account_label}")
    lines.append(f"运行状态：{_claim_status(summary)}")

    sections = (
        ("本周游戏", summary.all_promotions),
        ("本次新领取", summary.newly_claimed_promotions),
        ("之前已领取", summary.previously_claimed_promotions),
    )
    for heading, games in sections:
        lines.append(f"{heading}：")
        lines.append(_format_games(games))
    if summary.unconfirmed_promotions:
        lines.append("未确认成功：")
        lines.append(_format_games(summary.unconfirmed_promotions))
    if summary.failed_promotions:
        lines.append("领取失败：")
        lines.append(_format_games(summary.failed_promotions))
    if summary.error_message:
        lines.append(f"失败原因：{_first_error_line(summary.error_message)}")

    return "\n".join(lines)


async def send_collection_summary_to_pushplus(
    summary: CollectionSummary, *, account_label: str | None = None
) -> None:
    if not pushplus_notifications_enabled():
        logger.debug("PushPlus notification is not configured; skipping delivery")
        return

    # A config or transport problem must never surface as a claim-task failure.
    try:
        payload = {
            "token": _env("PUSHPLUS_TOKEN"),
            "title": build_pushplus_title(summary),
            "content": build_pushplus_content(summary, account_label=account_label),
            "template": "txt",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(PUSHPLUS_SEND_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 200:
                logger.warning(
                    "PushPlus notification failed | code={} msg={}",
                    data.get("code"),
                    data.get("msg"),
                )
    except httpx.HTTPStatusError as err:
        logger.warning("PushPlus notification failed | status={}", err.response.status_code)
    except httpx.HTTPError as err:
        logger.warning("PushPlus notification failed | error_type={}", type(err).__name__)
    except Exception as err:
        logger.warning(
            "PushPlus notification failed before delivery | error_type={}", type(err).__name__
        )
