import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.monitor import run_monitor


class Command(BaseCommand):
    help = "Run the monitor process (long-running): alerts the admin when something breaks."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_monitor())
        except KeyboardInterrupt:
            pass