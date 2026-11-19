from django.urls import path

from . import agent_api, guest_views, manage_views, tus

urlpatterns = [
    # Guest link
    path("g/<str:token>/", guest_views.page, name="guest-page"),
    path("g/<str:token>/api/state", guest_views.api_state, name="guest-state"),
    path("g/<str:token>/api/set/add", guest_views.api_set_add, name="guest-set-add"),
    path("g/<str:token>/api/set/remove", guest_views.api_set_remove, name="guest-set-remove"),
    path("g/<str:token>/api/set/order", guest_views.api_set_order, name="guest-set-order"),
    path("g/<str:token>/api/markers/add", guest_views.api_marker_add, name="guest-marker-add"),
    path("g/<str:token>/api/markers/delete", guest_views.api_marker_delete, name="guest-marker-delete"),
    path("g/<str:token>/api/private", guest_views.api_private, name="guest-private"),
    path("g/<str:token>/thumb/<int:asset_id>", guest_views.thumb, name="guest-thumb"),
    path("g/<str:token>/file/<int:asset_id>", guest_views.media_file, name="guest-file"),
    path("g/<str:token>/tus/", tus.collection, name="tus-collection"),
    path("g/<str:token>/tus/<uuid:upload_id>", tus.resource, name="tus-resource"),
    # Agent API
    path("api/agent/manifest", agent_api.manifest, name="agent-manifest"),
    path("api/agent/heartbeat", agent_api.heartbeat, name="agent-heartbeat"),
    path("api/agent/pairing", agent_api.pairing, name="agent-pairing"),
    path("api/agent/markers", agent_api.markers, name="agent-markers"),
    path("api/agent/files/<int:asset_id>/<str:variant>", agent_api.file, name="agent-file"),
    # Staff backend
    path("manage/", manage_views.dashboard, name="manage-dashboard"),
    path("manage/service/<int:pk>/", manage_views.service_detail, name="manage-service"),
    path("manage/library/", manage_views.library, name="manage-library"),
    path("manage/asset/<int:pk>/", manage_views.asset_detail, name="manage-asset"),
    path("manage/asset/<int:pk>/approve", manage_views.approve, name="manage-approve"),
    path("manage/asset/<int:pk>/<str:variant>", manage_views.asset_file, name="manage-asset-file"),
    path("manage/agents/", manage_views.agents, name="manage-agents"),
    path("manage/qr.svg", manage_views.qr_svg, name="manage-qr"),
]
