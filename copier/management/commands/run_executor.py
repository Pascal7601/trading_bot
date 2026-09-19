import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.executor import run_executor


class Command(BaseCommand):
    help = "Run the executor process (long-running)."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_executor())
        except KeyboardInterrupt:
            pass