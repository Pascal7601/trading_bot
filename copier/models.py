from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from engine.types import RiskLimits, SizingMode

from . import crypto

_cipher = None


def cipher():
    global _cipher
    if _cipher is None:
        _cipher = crypto.build_cipher(settings.ENCRYPTION_KEYS)
    return _cipher


def _symbols(csv: str) -> frozenset[str]:
    return frozenset(s.strip().upper() for s in csv.split(",") if s.strip())


class SystemState(models.Model):
    """Singleton (pk=1). `copying_enabled=False` is the global kill switch (toggle in the admin)."""
    copying_enabled = models.BooleanField(default=True)

    @classmethod
    async def aenabled(cls) -> bool:
        state, _ = await cls.objects.aget_or_create(pk=1)
        return state.copying_enabled

    def __str__(self):
        return f"Copying {'ENABLED' if self.copying_enabled else 'PAUSED'}"


class Follower(models.Model):
    class Sizing(models.TextChoices):
        PROPORTIONAL = SizingMode.PROPORTIONAL.value, "Proportional to equity (x scale)"
        FIXED_NOTIONAL = SizingMode.FIXED_NOTIONAL.value, "Fixed USDT per trade"
        MULTIPLIER = SizingMode.MULTIPLIER.value, "Multiple of master quantity"

    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True, help_text="Follower-controlled pause.")
    is_banned = models.BooleanField(default=False, help_text="Admin-controlled block.")
    sizing_mode = models.CharField(max_length=20, choices=Sizing.choices, default=Sizing.PROPORTIONAL)
    sizing_value = models.DecimalField(max_digits=28, decimal_places=10, default=1)
    max_notional_per_trade = models.DecimalField(max_digits=28, decimal_places=10, null=True, blank=True)
    max_leverage = models.PositiveSmallIntegerField(null=True, blank=True)
    allowed_symbols = models.TextField(blank=True, help_text="Comma-separated, e.g. BTC-USDT,ETH-USDT. Empty = all.")
    blocked_symbols = models.TextField(blank=True)
    # Protection settings (NULL = off). Safe defaults so a new follower is protected from day one.
    max_slippage_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=Decimal("1.00"))
    max_exposure_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=Decimal("80"))
    max_daily_loss_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=Decimal("10"))
    stop_loss_roi_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    flatten_on_max_loss = models.BooleanField(default=False, help_text="Also close all positions when the daily loss limit trips.")
    guard_baseline_at = models.DateTimeField(null=True, blank=True, help_text="Drawdown is measured from this moment or 24h ago, whichever is later (reset on /resume).")
    created_at = models.DateTimeField(auto_now_add=True)

    def risk_limits(self) -> RiskLimits:
        return RiskLimits(
            max_notional_per_trade=self.max_notional_per_trade,
            max_leverage=self.max_leverage,
            allowed_symbols=_symbols(self.allowed_symbols),
            blocked_symbols=_symbols(self.blocked_symbols),
            max_slippage_pct=self.max_slippage_pct,
            max_exposure_pct=self.max_exposure_pct,
            max_daily_loss_pct=self.max_daily_loss_pct,
            stop_loss_roi_pct=self.stop_loss_roi_pct,
            flatten_on_max_loss=self.flatten_on_max_loss,
        )

    def __str__(self):
        return f"@{self.username or self.telegram_id}"


class ApiCredential(models.Model):
    follower = models.OneToOneField(Follower, on_delete=models.CASCADE, related_name="credential")
    api_key_enc = models.TextField()
    api_secret_enc = models.TextField()
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_keys(self, api_key: str, api_secret: str) -> None:
        self.api_key_enc = crypto.encrypt(cipher(), api_key)
        self.api_secret_enc = crypto.encrypt(cipher(), api_secret)

    def get_keys(self) -> tuple[str, str]:
        return crypto.decrypt(cipher(), self.api_key_enc), crypto.decrypt(cipher(), self.api_secret_enc)

    def __str__(self):
        return f"credential for {self.follower}"


class MasterEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        DONE = "done"
        SKIPPED = "skipped"

    order_id = models.CharField(max_length=64, unique=True)  # master's exchange order id (dedupe key)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4)
    position_side = models.CharField(max_length=5)
    quantity = models.DecimalField(max_digits=28, decimal_places=10)
    price = models.DecimalField(max_digits=28, decimal_places=10)
    leverage = models.PositiveSmallIntegerField(null=True, blank=True)
    position_qty_after = models.DecimalField(max_digits=28, decimal_places=10)
    master_equity = models.DecimalField(max_digits=28, decimal_places=10)
    raw = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    # Filled only on the fill that FULLY closes a position (used for the PnL card):
    result_entry_price = models.DecimalField(max_digits=28, decimal_places=10, null=True, blank=True)
    result_exit_price = models.DecimalField(max_digits=28, decimal_places=10, null=True, blank=True)
    result_leverage = models.PositiveSmallIntegerField(null=True, blank=True)
    result_qty = models.DecimalField(max_digits=28, decimal_places=10, null=True, blank=True)
    card_status = models.CharField(max_length=10, default="none", db_index=True)  # none|pending|offered|posting|posted|skipped
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.symbol} {self.side}/{self.position_side} {self.quantity}"


class CopyOrder(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        FILLED = "filled"      # order accepted by the exchange (market orders fill immediately)
        SKIPPED = "skipped"    # deliberately not copied (risk rule, minimum size, ...)
        FAILED = "failed"      # exchange rejected it
        UNKNOWN = "unknown"    # timeout/crash mid-flight: order MAY exist -> needs reconciliation

    master_event = models.ForeignKey(MasterEvent, on_delete=models.CASCADE, related_name="copies")
    follower = models.ForeignKey(Follower, on_delete=models.CASCADE, related_name="copies")
    client_order_id = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    quantity = models.DecimalField(max_digits=28, decimal_places=10, null=True, blank=True)
    exchange_order_id = models.CharField(max_length=64, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["master_event", "follower"], name="one_copy_per_follower_event")]


class EquitySnapshot(models.Model):
    """Periodic follower equity, used for the rolling 24h drawdown and weekly reports."""
    follower = models.ForeignKey(Follower, on_delete=models.CASCADE, related_name="snapshots")
    equity = models.DecimalField(max_digits=28, decimal_places=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["follower", "created_at"])]


class GuardEvent(models.Model):
    """Audit trail of every automatic protective action."""
    follower = models.ForeignKey(Follower, on_delete=models.CASCADE, related_name="guard_events")
    kind = models.CharField(max_length=32)   # max_daily_loss | stop_loss
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)




class Heartbeat(models.Model):
    """Each long-running process updates its row every few seconds; the monitor alerts when one goes quiet."""
    name = models.CharField(max_length=32, unique=True)
    beat_at = models.DateTimeField()
    detail = models.CharField(max_length=200, blank=True)


class TermsAcceptance(models.Model):
    """Append-only record of who accepted which version of the risk disclosure, and when."""
    follower = models.ForeignKey(Follower, on_delete=models.CASCADE, related_name="terms_acceptances")
    version = models.CharField(max_length=32)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["follower", "version"], name="one_acceptance_per_version")]