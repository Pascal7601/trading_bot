import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.listener import run_listener


class Command(BaseCommand):
    help = "Run the listener process (long-running)."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_listener())
        except KeyboardInterrupt:
            pass