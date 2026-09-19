import asyncio
import logging

from django.core.management.base import BaseCommand

from copier.services.reports import send_weekly_reports


class Command(BaseCommand):
    help = "Send every linked follower their last-7-days summary. Run weekly from cron."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO)
        asyncio.run(send_weekly_reports(options["days"]))