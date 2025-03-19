from django.urls import path
from . import views

urlpatterns = [
    # login page
    path('', views.login_page, name='login_page'),
    path('login/', views.login_request, name='login_request'),

    # home page
    path('home/', views.home_page, name='home_page'),
    path('logout/', views.logout_request, name='logout_request'),

]