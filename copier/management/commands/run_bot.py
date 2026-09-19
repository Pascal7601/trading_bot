import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.bot import run_bot


class Command(BaseCommand):
    help = "Run the bot process (long-running)."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            pass