"""Telegram bot for followers (aiogram 3). Settings are changed with tap-buttons, not typed commands."""
import logging
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

from exchange.bingx import BingXClient

from ..models import ApiCredential, Follower
from .reports import build_report

log = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "Welcome! This bot copies the trader's new BingX futures trades onto YOUR BingX account.\n\n"
    "⚠️ Trading is risky and you can lose money. Copied trades will NOT match the trader's fills exactly "
    "(slippage, timing, size). You are solely responsible for your account. Nothing here is financial advice.\n\n"
    "Commands:\n/connect – link your BingX API key\n/settings – sizing and safety limits (tap to change)\n"
    "/status – your setup and latest copies\n/report – your last 7 days\n"
    "/pause  /resume – stop or restart copying\n/disconnect – delete your stored key"
)

KEY_HELP = (
    "Create a BingX API key with ONLY futures trading permission.\n"
    "• Do NOT enable withdrawals.\n"
    f"• Whitelist this IP on the key: {settings.SERVER_IP}\n"
    "• Your account must be in Hedge (two-way) position mode.\n\n"
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


async def _edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified"
    await cb.answer()


@router.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME)


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.")


@router.message(Command("connect"))
async def connect(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return await m.answer("Please message me privately to link your account.")
    await state.set_state(Connect.api_key)
    await m.answer(KEY_HELP)


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
            equity = await client.get_equity()
    except Exception as e:
        log.info("key validation failed for %s: %s", m.from_user.id, type(e).__name__)
        return await m.answer("I couldn't validate that key (wrong key, missing permission, or IP not whitelisted). "
                              "Please check and try /connect again.")
    follower, _ = await Follower.objects.aget_or_create(
        telegram_id=m.from_user.id, defaults={"username": m.from_user.username or ""})
    cred, _ = await ApiCredential.objects.aget_or_create(follower=follower, defaults={"api_key_enc": "", "api_secret_enc": ""})
    cred.set_keys(key, secret)
    cred.verified_at = timezone.now()
    await cred.asave()
    await m.answer(f"✅ Linked. Your futures equity is {equity:.2f} USDT.\n"
                   "Safety limits are ON by default. Review them and your sizing with /settings.")


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
        "", "Latest copies:",
    ]
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


async def run_bot() -> None:
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)