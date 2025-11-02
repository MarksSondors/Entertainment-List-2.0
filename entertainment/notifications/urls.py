from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'subscriptions', views.PushSubscriptionViewSet, basename='push-subscription')
router.register(r'preferences', views.NotificationPreferenceViewSet, basename='notification-preference')

urlpatterns = [
    path('test/', views.send_test_notification, name='test-notification'),
    path('test-page/', views.notification_test_page, name='notification-test-page'),
    path('vapid-public-key/', views.get_vapid_public_key, name='vapid-public-key'),
    path('', include(router.urls)),
]
