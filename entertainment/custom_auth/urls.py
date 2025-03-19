from django.urls import path
from . import views

urlpatterns = [
    # pages
    path('', views.login_page, name='login_page'),
    path('home/', views.home_page, name='home'),

    # requests
    path('login/', views.login_request, name='login_request'),

]