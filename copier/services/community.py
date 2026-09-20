"""Closed-trade PnL cards for the community chat.

CARD_MODE=approve: the card is first sent to the master privately with Post / Skip buttons.
CARD_MODE=auto:    the card is posted straight away.  CARD_MODE=off: nothing is posted.
Only the bot posts to the community, and only about the MASTER's trades: nothing about any follower.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from django.conf import settings

from engine.pnl import roi_pct

from ..models import MasterEvent
from .pnl_card import generate_pnl_card
from .alerts import alert_admin

log = logging.getLogger(__name__)
_username: list[str] = []


async def _join_button(bot: Bot) -> InlineKeyboardMarkup:
    if not _username:
        _username.append((await bot.get_me()).username)
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 Copy these trades automatically", url=f"https://t.me/{_username[0]}?start=community")
    return kb.as_markup()


async def _render(event: MasterEvent) -> tuple[bytes, str]:
    if not event.result_leverage:  # without the leverage the ROI would be wrong: never guess
        raise ValueError("leverage unknown, so no card can be made for this trade")
    entry, exit_ = event.result_entry_price, event.result_exit_price
    png = await asyncio.to_thread(generate_pnl_card, event.symbol, event.position_side, event.result_leverage,
                                  entry, exit_, brand=settings.COMMUNITY_BRAND)
    roi = roi_pct(event.position_side, event.result_leverage, entry, exit_)
    caption = (f"{'🟢' if roi >= 0 else '🔴'} {event.symbol} {event.position_side} closed: {roi:+.1f}% ROI "
               "(estimate, before fees).\nTrading is risky. This is not financial advice.")
    return png, caption


async def post_to_community(bot: Bot, event: MasterEvent) -> None:
    png, caption = await _render(event)
    await bot.send_photo(int(settings.COMMUNITY_CHAT_ID), BufferedInputFile(png, "trade.jpg"),
                         caption=caption, reply_markup=await _join_button(bot))
    event.card_status = "posted"
    await event.asave(update_fields=["card_status"])


async def offer_to_master(bot: Bot, event: MasterEvent) -> None:
    png, caption = await _render(event)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Post to community", callback_data=f"card:post:{event.id}")
    kb.button(text="🚫 Skip", callback_data=f"card:skip:{event.id}")
    kb.adjust(2)
    await bot.send_photo(int(settings.MASTER_TELEGRAM_ID), BufferedInputFile(png, "trade.jpg"),
                         caption="Preview of the card for the community:\n\n" + caption, reply_markup=kb.as_markup())
    event.card_status = "offered"
    await event.asave(update_fields=["card_status"])


async def card_loop(bot: Bot) -> None:
    while True:
        try:
            events = [e async for e in MasterEvent.objects.filter(card_status="pending").order_by("id")[:5]]
            for event in events:
                try:
                    if settings.CARD_MODE == "auto":
                        await post_to_community(bot, event)
                    elif settings.CARD_MODE == "approve":
                        await offer_to_master(bot, event)
                    else:
                        raise RuntimeError("cards are switched off")
                except Exception as exc:  # never retry forever: mark it and tell the admin
                    event.card_status = "skipped"
                    await event.asave(update_fields=["card_status"])
                    await alert_admin(f"Couldn't make/send the PnL card for {event.symbol} {event.position_side} "
                                      f"(master trade #{event.id}): {exc}. Check CARD_MODE, MASTER_TELEGRAM_ID (the "
                                      "master must have pressed /start in the bot) and COMMUNITY_CHAT_ID.",
                                      key=f"card:{event.id}")
        except Exception:
            log.exception("card loop failed")
        await asyncio.sleep(3)