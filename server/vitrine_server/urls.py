from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from guestmedia import docs_views

# One Vitrine-styled sign-in page for /manage/ and /admin/.
admin.site.login_template = "guestmedia/login.html"
admin.site.site_header = "Vitrine administration"
admin.site.site_title = "Vitrine"
admin.site.index_title = "Raw data"
admin.site.site_url = "/manage/"

urlpatterns = [
    path("", RedirectView.as_view(url="/manage/", permanent=False)),
    path("login/", auth_views.LoginView.as_view(template_name="guestmedia/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("docs/", docs_views.page, name="docs-home"),
    path("docs/_media/<path:name>", docs_views.media, name="docs-media"),
    path("docs/<slug:slug>/", docs_views.page, name="docs-page"),
    path("admin/", admin.site.urls),
    path("", include("guestmedia.urls")),
]
