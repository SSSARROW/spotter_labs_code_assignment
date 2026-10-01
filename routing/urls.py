from django.urls import path

from routing.views import PlaceSearchView, RoutePlanView

urlpatterns = [
    path("route/", RoutePlanView.as_view(), name="route-plan"),
    path("places/", PlaceSearchView.as_view(), name="place-search"),
]
