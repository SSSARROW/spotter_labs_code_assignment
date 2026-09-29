from django.contrib import admin

from routing.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "price_per_gallon", "latitude", "longitude")
    list_filter = ("state",)
    search_fields = ("name", "city", "address")
