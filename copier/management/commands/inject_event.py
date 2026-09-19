import uuid
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from copier.models import MasterEvent


class Command(BaseCommand):
    help = "DEBUG ONLY: insert a fake master fill so you can exercise the executor. Requires DRY_RUN=1."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="BTC-USDT")
        parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
        parser.add_argument("--position-side", default="LONG", choices=["LONG", "SHORT"])
        parser.add_argument("--qty", type=Decimal, default=Decimal("0.01"))
        parser.add_argument("--price", type=Decimal, default=Decimal("81200"))
        parser.add_argument("--leverage", type=int, default=10)
        parser.add_argument("--position-after", type=Decimal, default=Decimal("0.01"),
                            help="master's position size after this fill (0 = fully closed)")
        parser.add_argument("--master-equity", type=Decimal, default=Decimal("10000"))

    def handle(self, *args, **o):
        if not settings.DRY_RUN:
            raise CommandError("Refusing: set DRY_RUN=1 first. This command must never trigger real orders.")
        ev = MasterEvent.objects.create(
            order_id=f"fake-{uuid.uuid4().hex[:12]}", symbol=o["symbol"], side=o["side"],
            position_side=o["position_side"], quantity=o["qty"], price=o["price"], leverage=o["leverage"],
            position_qty_after=o["position_after"], master_equity=o["master_equity"], raw={"fake": True})
        self.stdout.write(self.style.SUCCESS(f"created MasterEvent #{ev.id}: {ev}"))