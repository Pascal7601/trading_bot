import asyncio
import getpass
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from engine.keycheck import evaluate_key
from exchange.bingx import BingXClient


class Command(BaseCommand):
    help = ("Debug helper: prints what BingX says about an API key (permissions, position mode) and the verdict "
            "the bot would reach. Use it once on a real key to confirm exchange/parsing.py matches BingX. "
            "The secret is typed at a hidden prompt and never stored.")

    def handle(self, *args, **options):
        key = input("API key: ").strip()
        secret = getpass.getpass("API secret (hidden): ").strip()
        asyncio.run(self.run(key, secret))

    async def run(self, key: str, secret: str) -> None:
        async with BingXClient(key, secret, base_url=settings.BINGX_BASE_URL) as client:
            perms = await client.get_key_permissions()
            hedge = await client.get_position_mode()
        self.stdout.write("RAW permission payloads:\n" + json.dumps(perms.raw, indent=2, default=str))
        self.stdout.write(f"\nParsed: can_withdraw={perms.can_withdraw} can_trade={perms.can_trade} "
                          f"ip_restricted={perms.ip_restricted} hedge_mode={hedge}")
        verdict = evaluate_key(perms, hedge, settings.STRICT_KEY_CHECK)
        self.stdout.write(f"Blockers: {verdict.blockers}\nWarnings: {verdict.warnings}\n"
                          f"Inconclusive: {verdict.inconclusive}")