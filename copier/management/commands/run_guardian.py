import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.guardian import run_guardian


class Command(BaseCommand):
    help = "Run the guardian process (long-running)."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_guardian())
        except KeyboardInterrupt:
            pass