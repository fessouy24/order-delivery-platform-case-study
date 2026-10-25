from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.urls import include, path
from django_otp.admin import OTPAdminSite

from apps.core.views import live, ready
from apps.identity import views as identity

secure_admin = OTPAdminSite(name="secure_admin")
secure_admin.register(get_user_model(), UserAdmin)

urlpatterns = [
    path("api/v1/", include("apps.operations.urls")),
    path("health/live", live),
    path("health/ready", ready),
    path("admin/", secure_admin.urls),
    path("api/v1/auth/csrf/", identity.csrf),
    path("api/v1/auth/login/", identity.sign_in),
    path("api/v1/auth/logout/", identity.sign_out),
    path("api/v1/auth/me/", identity.me),
    path("api/v1/auth/mfa/enroll/", identity.enroll),
    path("api/v1/auth/mfa/verify/", identity.verify),
]
