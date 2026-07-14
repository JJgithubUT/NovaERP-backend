from django.urls import path

from core.views import LoginView, MeView

urlpatterns = [
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/core/me/', MeView.as_view(), name='me'),
]
