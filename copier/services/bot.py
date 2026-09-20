"""Telegram bot for followers (aiogram 3). Settings are changed with tap-buttons, not typed commands."""
import asyncio
import json
import logging
from datetime import timedelta
from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from django.conf import settings
from django.utils import timezone

from engine.keycheck import evaluate_key
from exchange.bingx import BingXClient

from ..models import ApiCredential, CopyOrder, Follower, MasterEvent, TermsAcceptance
from .alerts import alert_admin
from .community import card_loop, post_to_community
from .heartbeat import beat
from .reports import build_report

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")                # privacy: every personal command works in DMs only
router.callback_query.filter(F.message.chat.type == "private")
group_router = Router()                                        # what the bot does when spoken to in a group

WELCOME = (
    "Welcome! This bot copies the trader's new BingX futures trades onto YOUR BingX account.\n\n"
    "⚠️ Trading is risky and you can lose money. Copied trades will NOT match the trader's fills exactly "
    "(slippage, timing, size). You are solely responsible for your account. Nothing here is financial advice.\n\n"
    "Commands:\n/connect – link your BingX API key (you'll read and accept the terms first)\n"
    "/settings – sizing and safety limits (tap to change)\n"
    "/status – your setup and latest copies\n/report – your last 7 days\n"
    "/pause  /resume – stop or restart copying\n/terms – risk disclosure and terms\n"
    "/disconnect – delete your stored key"
)

TERMS_TEXT = (
    "📜 Risk disclosure and terms. Please read.\n\n"
    "• Trading leveraged crypto futures is very risky. You can lose some or ALL of your money, and fast markets "
    "or high leverage can make losses larger and quicker than you expect.\n"
    "• This service copies the trader's trades automatically. Your fills WILL differ from his (timing, price, size). "
    "Trades can be delayed, skipped or fail, and the service itself can go down.\n"
    "• Past results of the trader or of this service do not predict future results. Nothing here is investment, "
    "legal or tax advice.\n"
    "• You stay in full control of your BingX account and funds. The bot has no withdrawal access and you must "
    "never enable withdrawals on the API key. You are responsible for keeping your key and account secure.\n"
    "• You choose your own size and safety limits (/settings). You can pause or disconnect at any time; open "
    "positions are NOT closed automatically when you do.\n"
    "• The service is provided as is, without guarantees. Use only money you can afford to lose, and only where "
    "this is legal for you."
)

KEY_HELP = (
    "Create a BingX API key with ONLY futures trading permission.\n"
    "• Do NOT enable withdrawals (I will refuse keys that allow them).\n"
    f"• Whitelist this IP on the key: {settings.SERVER_IP}\n"
    "• Your futures account must be in Hedge (two-way) position mode.\n\n"
    "Now send your API KEY. I will delete your message right after reading it. (/cancel to stop)"
)

MODES = {"proportional": "Proportional", "fixed_notional": "Fixed USDT", "multiplier": "Multiplier"}
DEFAULT_SCALE = {"proportional": "1", "fixed_notional": "50", "multiplier": "0.5"}
SCALE_CHOICES = {
    "proportional": ["0.25", "0.5", "1", "1.5", "2"],
    "multiplier": ["0.1", "0.25", "0.5", "1", "2"],
    "fixed_notional": ["25", "50", "100", "250", "500"],
}
CHOICES = {  # key -> menu label, explanation, Follower field, options, unit
    "slip": ("Max slippage", "Skip a trade if the price already moved this much against the trader's entry.",
             "max_slippage_pct", ["0.3", "0.5", "1", "2", "3"], "%"),
    "loss": ("Daily loss limit", "Pause copying if your equity falls this much below its 24h peak.",
             "max_daily_loss_pct", ["3", "5", "10", "15"], "%"),
    "sl": ("Stop-loss", "Close a position on your account when its ROI reaches this loss, even if the trader stays in.",
           "stop_loss_roi_pct", ["10", "20", "30", "50"], "%"),
    "exp": ("Max margin in use", "Skip new trades that would lock more than this share of your equity as margin.",
            "max_exposure_pct", ["25", "50", "80", "100"], "%"),
    "lev": ("Leverage cap", "Never use more than this leverage, even if the trader does.",
            "max_leverage", ["3", "5", "10", "20"], "x"),
    "trade": ("Max size per trade", "Cap the position value of each copied trade.",
              "max_notional_per_trade", ["50", "100", "250", "500"], " USDT"),
}


class Connect(StatesGroup):
    api_key = State()
    api_secret = State()


def _num(v) -> str:
    return format(Decimal(str(v)).normalize(), "f")


def _show(v, unit: str = "") -> str:
    return "off" if v is None else f"{_num(v)}{unit}"


def terms_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ I understand and accept", callback_data="terms:yes")
    kb.button(text="❌ No thanks", callback_data="terms:no")
    kb.adjust(1)
    return kb.as_markup()


def main_menu(f: Follower) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Sizing: {MODES[f.sizing_mode]} × {_num(f.sizing_value)}", callback_data="s:sizing")
    for key, (label, _why, attr, _opts, unit) in CHOICES.items():
        kb.button(text=f"{label}: {_show(getattr(f, attr), unit)}", callback_data=f"s:{key}")
    kb.button(text=f"If daily limit hits: {'pause + close all' if f.flatten_on_max_loss else 'pause only'}",
              callback_data="t:flat")
    kb.button(text="⏸ Pause copying" if f.is_active else "▶️ Resume copying", callback_data="t:active")
    kb.adjust(1)
    state = "ON" if f.is_active and not f.is_banned else "OFF"
    return f"⚙️ Settings  (copying is {state})\nTap a line to change it.", kb.as_markup()


def sizing_menu(f: Follower) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    for mode, name in MODES.items():
        kb.button(text=("✅ " if f.sizing_mode == mode else "") + name, callback_data=f"c:mode:{mode}")
    for v in SCALE_CHOICES[f.sizing_mode]:
        kb.button(text=("✅ " if Decimal(v) == f.sizing_value else "") + v, callback_data=f"c:scale:{v}")
    kb.button(text="⬅️ Back", callback_data="s:back")
    kb.adjust(3, 5, 1)
    text = ("Sizing\n• Proportional: same share of YOUR equity as the trader (× the scale below)\n"
            "• Fixed USDT: the same USDT amount on every trade\n• Multiplier: the trader's size × the scale\n\n"
            f"Now: {MODES[f.sizing_mode]} × {_num(f.sizing_value)}")
    return text, kb.as_markup()


def choice_menu(key: str, f: Follower) -> tuple[str, InlineKeyboardMarkup]:
    label, why, attr, options, unit = CHOICES[key]
    kb = InlineKeyboardBuilder()
    for v in options:
        kb.button(text=f"{v}{unit}", callback_data=f"c:{key}:{v}")
    kb.button(text="Off", callback_data=f"c:{key}:off")
    kb.button(text="⬅️ Back", callback_data="s:back")
    kb.adjust(*([3] * -(-(len(options) + 1) // 3)), 1)  # rows of 3 (options + Off), then Back alone
    return f"{label}\n{why}\n\nNow: {_show(getattr(f, attr), unit)}", kb.as_markup()


async def _follower(m: Message) -> Follower | None:
    f = await Follower.objects.filter(telegram_id=m.from_user.id).afirst()
    if f is None:
        await m.answer("You haven't linked an account yet. Use /connect.")
    return f


async def _accepted(telegram_id: int) -> bool:
    return await TermsAcceptance.objects.filter(
        follower__telegram_id=telegram_id, version=settings.TERMS_VERSION).aexists()


async def _edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified"
    await cb.answer()


async def _start_key_flow(target: Message, state: FSMContext) -> None:
    await state.set_state(Connect.api_key)
    await target.answer(KEY_HELP)


@router.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME)


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.")


@router.message(Command("terms"))
async def terms(m: Message):
    if await _accepted(m.from_user.id):
        await m.answer("✅ You have accepted the current version.\n\n" + TERMS_TEXT)
    else:
        await m.answer(TERMS_TEXT, reply_markup=terms_keyboard())


@router.callback_query(F.data == "terms:yes")
async def terms_yes(cb: CallbackQuery, state: FSMContext):
    if cb.message.chat.type != "private":
        return await cb.answer("Please do this in a private chat with me.", show_alert=True)
    follower, _ = await Follower.objects.aget_or_create(
        telegram_id=cb.from_user.id, defaults={"username": cb.from_user.username or ""})
    await TermsAcceptance.objects.aget_or_create(follower=follower, version=settings.TERMS_VERSION)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await cb.answer("Accepted")
    if await ApiCredential.objects.filter(follower=follower).aexists():
        await cb.message.answer("✅ Thanks. You're up to date.")
    else:
        await _start_key_flow(cb.message, state)


@router.callback_query(F.data == "terms:no")
async def terms_no(cb: CallbackQuery):
    await _edit(cb, "No problem. Nothing was saved. Send /connect whenever you'd like to continue.", None)


@router.message(Command("connect"))
async def connect(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return await m.answer("Please message me privately to link your account.")
    if not await _accepted(m.from_user.id):
        return await m.answer(TERMS_TEXT, reply_markup=terms_keyboard())
    await _start_key_flow(m, state)


@router.message(Connect.api_key)
async def got_key(m: Message, state: FSMContext):
    await state.update_data(api_key=(m.text or "").strip())
    await m.delete()
    await state.set_state(Connect.api_secret)
    await m.answer("Got it (message deleted). Now send your API SECRET.")


@router.message(Connect.api_secret)
async def got_secret(m: Message, state: FSMContext):
    secret = (m.text or "").strip()
    data = await state.get_data()
    await state.clear()
    await m.delete()
    key = data.get("api_key", "")
    try:
        async with BingXClient(key, secret, base_url=settings.BINGX_BASE_URL) as client:
            equity = await client.get_equity()  # fails for a wrong key, a missing permission or an unlisted IP
            perms = await client.get_key_permissions()
            hedge = await client.get_position_mode()
    except Exception as e:
        log.info("key validation failed for %s: %s", m.from_user.id, type(e).__name__)
        return await m.answer("I couldn't validate that key (wrong key, missing permission, or IP not whitelisted). "
                              "Please check and try /connect again.")

    verdict = evaluate_key(perms, hedge, settings.STRICT_KEY_CHECK)
    if verdict.inconclusive:  # admin-only: tells you the parser needs adjusting (raw has permissions, no secrets)
        await alert_admin(f"Key check for user {m.from_user.id} was inconclusive ({'; '.join(verdict.inconclusive)}). "
                          f"Raw: {json.dumps(perms.raw, default=str)[:800]}", key=f"keycheck:{m.from_user.id}", icon="⚠️")
    if verdict.blockers:  # nothing has been stored
        return await m.answer("\n\n".join(verdict.blockers))

    follower, _ = await Follower.objects.aget_or_create(
        telegram_id=m.from_user.id, defaults={"username": m.from_user.username or ""})
    cred, _ = await ApiCredential.objects.aget_or_create(follower=follower, defaults={"api_key_enc": "", "api_secret_enc": ""})
    cred.set_keys(key, secret)
    cred.verified_at = timezone.now()
    await cred.asave()
    await m.answer("\n\n".join([
        f"✅ Linked. Your futures equity is {equity:.2f} USDT.\n"
        "Safety limits are ON by default. Review them and your sizing with /settings.",
        *verdict.warnings,
    ]))


@router.message(Command("settings"))
async def settings_cmd(m: Message):
    if f := await _follower(m):
        text, markup = main_menu(f)
        await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("s:"))
async def open_menu(cb: CallbackQuery):
    f = await Follower.objects.filter(telegram_id=cb.from_user.id).afirst()
    if f is None:
        return await cb.answer("Use /connect first.", show_alert=True)
    key = cb.data[2:]
    if key == "back":
        text, markup = main_menu(f)
    elif key == "sizing":
        text, markup = sizing_menu(f)
    elif key in CHOICES:
        text, markup = choice_menu(key, f)
    else:
        return await cb.answer()
    await _edit(cb, text, markup)


@router.callback_query(F.data.startswith("c:"))
async def choose(cb: CallbackQuery):
    f = await Follower.objects.filter(telegram_id=cb.from_user.id).afirst()
    if f is None:
        return await cb.answer("Use /connect first.", show_alert=True)
    _, key, value = cb.data.split(":", 2)
    if key == "mode" and value in MODES:
        f.sizing_mode, f.sizing_value = value, Decimal(DEFAULT_SCALE[value])
        fields = ["sizing_mode", "sizing_value"]
    elif key == "scale" and value in SCALE_CHOICES[f.sizing_mode]:
        f.sizing_value, fields = Decimal(value), ["sizing_value"]
    elif key in CHOICES and (value == "off" or value in CHOICES[key][3]):
        attr = CHOICES[key][2]
        setattr(f, attr, None if value == "off" else (int(value) if attr == "max_leverage" else Decimal(value)))
        fields = [attr]
    else:
        return await cb.answer()
    await f.asave(update_fields=fields)
    text, markup = sizing_menu(f) if key in ("mode", "scale") else choice_menu(key, f)
    await _edit(cb, text, markup)


@router.callback_query(F.data.startswith("t:"))
async def toggle(cb: CallbackQuery):
    f = await Follower.objects.filter(telegram_id=cb.from_user.id).afirst()
    if f is None:
        return await cb.answer("Use /connect first.", show_alert=True)
    if cb.data == "t:flat":
        f.flatten_on_max_loss = not f.flatten_on_max_loss
        fields = ["flatten_on_max_loss"]
    else:
        f.is_active = not f.is_active
        fields = ["is_active"]
        if f.is_active:  # resuming: old drawdown must not instantly re-trigger the limit
            f.guard_baseline_at = timezone.now()
            fields.append("guard_baseline_at")
    await f.asave(update_fields=fields)
    text, markup = main_menu(f)
    await _edit(cb, text, markup)


@router.message(Command("status"))
async def status(m: Message):
    if not (f := await _follower(m)):
        return
    lines = [
        f"Copying: {'ON' if f.is_active and not f.is_banned else 'OFF'}" + (" (blocked by admin)" if f.is_banned else ""),
        f"Sizing: {MODES[f.sizing_mode]} × {_num(f.sizing_value)}",
        f"Limits: slippage {_show(f.max_slippage_pct, '%')} · daily loss {_show(f.max_daily_loss_pct, '%')} · "
        f"stop-loss {_show(f.stop_loss_roi_pct, '%')} · margin {_show(f.max_exposure_pct, '%')}",
    ]
    if not await _accepted(m.from_user.id):
        lines.append("⚠️ You haven't accepted the current terms, so copying is OFF. Send /terms to review and accept.")
    lines += ["", "Latest copies:"]
    async for c in f.copies.select_related("master_event").order_by("-id")[:5]:
        e = c.master_event
        lines.append(f"• {e.symbol} {e.position_side} {e.side} → {c.status} {c.detail[:60]}")
    await m.answer("\n".join(lines))


@router.message(Command("report"))
async def report(m: Message):
    if not (f := await _follower(m)):
        return
    await m.answer("Building your last-7-days summary…")
    try:
        await m.answer(await build_report(f, 7))
    except Exception:
        log.exception("report failed for %s", m.from_user.id)
        await m.answer("I couldn't build the report right now. Please try again later.")


@router.message(Command("pause"))
async def pause(m: Message):
    if f := await _follower(m):
        f.is_active = False
        await f.asave(update_fields=["is_active"])
        await m.answer("⏸️ Paused. Note: your OPEN positions stay open and are not managed while paused "
                       "(your stop-loss, if set, still protects them).")


@router.message(Command("resume"))
async def resume(m: Message):
    if f := await _follower(m):
        f.is_active, f.guard_baseline_at = True, timezone.now()
        await f.asave(update_fields=["is_active", "guard_baseline_at"])
        await m.answer("▶️ Resumed. Only NEW trades from now on will be copied.")


@router.message(Command("disconnect"))
async def disconnect(m: Message):
    if f := await _follower(m):
        await ApiCredential.objects.filter(follower=f).adelete()
        f.is_active = False
        await f.asave(update_fields=["is_active"])
        await m.answer("Your stored key was deleted and copying stopped. Also delete the API key in your BingX "
                       "account settings to be safe. Open positions are NOT closed.")


def _is_master(user_id: int) -> bool:
    return str(user_id) == str(settings.MASTER_TELEGRAM_ID)


def _is_operator(user_id: int) -> bool:
    return _is_master(user_id) or str(user_id) == str(settings.ADMIN_CHAT_ID)


@router.message(Command("myid"))
async def my_id(m: Message):
    await m.answer(f"Your Telegram id is {m.from_user.id}")


@router.message(Command("stats"))
async def stats(m: Message):
    """Totals only: never who is connected, never any follower's details."""
    if not _is_operator(m.from_user.id):
        return
    linked = Follower.objects.filter(credential__isnull=False, is_banned=False)
    since = timezone.now() - timedelta(hours=24)
    today = CopyOrder.objects.filter(created_at__gte=since)
    await m.answer(
        "📈 Community bot\n"
        f"Linked followers: {await linked.acount()}\n"
        f"Copying now: {await linked.filter(is_active=True, terms_acceptances__version=settings.TERMS_VERSION).acount()}\n"
        f"Last 24h copies: {await today.filter(status=CopyOrder.Status.FILLED).acount()} done · "
        f"{await today.filter(status=CopyOrder.Status.SKIPPED).acount()} skipped · "
        f"{await today.filter(status__in=[CopyOrder.Status.FAILED, CopyOrder.Status.UNKNOWN]).acount()} failed")


@router.callback_query(F.data.startswith("card:"))
async def card_action(cb: CallbackQuery, bot: Bot):
    if not _is_master(cb.from_user.id):
        return await cb.answer("Only the trader can do this.", show_alert=True)
    _, action, raw_id = cb.data.split(":")
    # Claim the card atomically so a double tap can't post it twice.
    claimed = await MasterEvent.objects.filter(pk=int(raw_id), card_status="offered").aupdate(card_status="posting")
    if not claimed:
        return await cb.answer("Already handled.", show_alert=True)
    event = await MasterEvent.objects.aget(pk=int(raw_id))
    if action == "post":
        try:
            await post_to_community(bot, event)
            note = "✅ Posted to the community."
        except Exception as exc:
            event.card_status = "skipped"
            await event.asave(update_fields=["card_status"])
            await alert_admin(f"Couldn't post the card to the community: {exc}. Is the bot an admin of the community "
                              "chat, and is COMMUNITY_CHAT_ID right?", key=f"post:{event.id}")
            note = "❌ Couldn't post it. The admin has been told."
    else:
        event.card_status = "skipped"
        await event.asave(update_fields=["card_status"])
        note = "🚫 Skipped."
    try:
        await cb.message.edit_caption(caption=note, reply_markup=None)
    except TelegramBadRequest:
        pass
    await cb.answer()


@group_router.message(Command("start", "connect", "settings", "status", "report", "terms", "pause", "resume",
                              "disconnect", "stats", "myid"), F.chat.type.in_({"group", "supergroup"}))
async def in_group(m: Message, bot: Bot):
    """Never show personal data in a group: point people to a private chat instead."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔒 Open private chat", url=f"https://t.me/{(await bot.get_me()).username}?start=community")
    await m.reply("For your privacy I only work in a private chat.", reply_markup=kb.as_markup())


@group_router.message(Command("chatid"), F.chat.type.in_({"group", "supergroup", "channel"}))
async def chat_id(m: Message):
    """Setup helper: lets the operator read this chat's id for COMMUNITY_CHAT_ID."""
    if m.from_user and _is_operator(m.from_user.id):
        await m.reply(f"This chat's id is {m.chat.id}")

async def _heartbeat_loop() -> None:
    while True:
        await beat("bot", "polling")
        await asyncio.sleep(15)

async def run_bot() -> None:
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(group_router)
    dp.include_router(router)
    tasks = [asyncio.create_task(_heartbeat_loop()), asyncio.create_task(card_loop(bot))]
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()