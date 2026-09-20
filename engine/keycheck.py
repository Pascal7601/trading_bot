"""Decide whether a follower's API key / account setup is acceptable. Pure Python."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyPermissions:
    can_withdraw: bool | None = None   # None = could not be determined
    can_trade: bool | None = None
    ip_restricted: bool | None = None
    raw: tuple = ()                    # raw exchange payloads (permissions only, never secrets) for debugging


@dataclass(frozen=True)
class KeyVerdict:
    blockers: list[str]        # shown to the user; the key is NOT stored
    warnings: list[str]        # shown to the user; the key is stored
    inconclusive: list[str]    # admin-only: things we could not verify


def evaluate_key(perms: KeyPermissions, hedge_mode: bool | None, strict: bool) -> KeyVerdict:
    blockers: list[str] = []
    warnings: list[str] = []
    inconclusive: list[str] = []

    if perms.can_withdraw is True:
        blockers.append("🚫 This API key can WITHDRAW funds. For your safety I won't store it. Create a NEW key "
                        "with only Futures trading enabled (withdrawals OFF), then send /connect again.")
    elif perms.can_withdraw is None:
        inconclusive.append("could not determine the withdrawal permission")
        if strict:
            blockers.append("I couldn't verify this key's permissions, so I can't accept it yet. Please try again in "
                            "a few minutes. If it keeps failing, contact support.")

    if perms.can_trade is False:
        blockers.append("This API key does not have Futures trading enabled. Enable it on the key (keep withdrawals "
                        "OFF) and send /connect again.")
    elif perms.can_trade is None:
        inconclusive.append("could not determine the futures-trading permission")

    if hedge_mode is False:
        blockers.append("Your futures account is in One-way position mode, but copying needs Hedge (two-way) mode. "
                        "Switch the position mode in your BingX futures settings (you must have no open positions), "
                        "then send /connect again.")
    elif hedge_mode is None:
        inconclusive.append("could not determine the position mode")

    if perms.ip_restricted is False:
        warnings.append("⚠️ This key has no IP whitelist. Adding the server IP to it makes it much safer if the key "
                        "ever leaks.")
    return KeyVerdict(blockers, warnings, inconclusive)