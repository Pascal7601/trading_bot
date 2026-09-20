from django.contrib import admin

from .models import ApiCredential, CopyOrder, Follower, GuardEvent, Heartbeat, MasterEvent, SystemState, TermsAcceptance


@admin.register(SystemState)
class SystemStateAdmin(admin.ModelAdmin):
    list_display = ("__str__", "copying_enabled")
    list_editable = ("copying_enabled",)


@admin.register(Follower)
class FollowerAdmin(admin.ModelAdmin):
    list_display = ("__str__", "telegram_id", "is_active", "is_banned", "sizing_mode", "sizing_value",
                    "max_slippage_pct", "max_daily_loss_pct", "stop_loss_roi_pct", "created_at")
    list_filter = ("is_active", "is_banned", "sizing_mode")
    search_fields = ("username", "telegram_id")


@admin.register(ApiCredential)
class ApiCredentialAdmin(admin.ModelAdmin):
    # Never show ciphertext or keys in the admin.
    fields = ("follower", "verified_at", "created_at", "updated_at")
    readonly_fields = fields
    list_display = ("follower", "verified_at", "updated_at")

    def has_add_permission(self, request):
        return False


@admin.register(MasterEvent)
class MasterEventAdmin(admin.ModelAdmin):
    list_display = ("id", "symbol", "side", "position_side", "quantity", "price", "status", "created_at")
    list_filter = ("status", "symbol")


@admin.register(CopyOrder)
class CopyOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "master_event", "follower", "status", "quantity", "updated_at")
    list_filter = ("status",)
    search_fields = ("follower__username", "client_order_id", "detail")


@admin.register(GuardEvent)
class GuardEventAdmin(admin.ModelAdmin):
    list_display = ("id", "follower", "kind", "detail", "created_at")
    list_filter = ("kind",)




@admin.register(Heartbeat)
class HeartbeatAdmin(admin.ModelAdmin):
    list_display = ("name", "beat_at", "detail")


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("follower", "version", "accepted_at")
    list_filter = ("version",)