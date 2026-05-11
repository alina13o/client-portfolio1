from django.urls import path

from portfolio import views


urlpatterns = [
    path('', views.home, name='dashboard'),
    path('manager/dashboard/', views.manager_dashboard, name='manager_dashboard'),
    path('owner/dashboard/', views.owner_dashboard, name='owner_dashboard'),
    path('owner/dashboard/manager/<int:manager_id>/', views.owner_manager_detail, name='owner_manager_detail'),
    path('clients/', views.clients, name='clients'),
    path('clients/search/', views.client_search, name='client_search'),
    path('clients/new/', views.client_create, name='client_create'),
    path('clients/<int:client_id>/', views.client_detail, name='client_detail'),
    path('clients/<int:client_id>/edit/', views.client_update, name='client_update'),
    path('clients/<int:client_id>/delete/', views.client_delete, name='client_delete'),
    path('clients/<int:client_id>/orders/new/', views.fact_create, name='fact_create'),
    path('clients/<int:client_id>/orders/<int:fact_id>/edit/', views.fact_update, name='fact_update'),
    path('clients/<int:client_id>/orders/<int:fact_id>/delete/', views.fact_delete, name='fact_delete'),
    path('admin/managers/', views.managers_admin, name='managers_admin'),
    path('admin/managers/<int:manager_id>/edit/', views.manager_update, name='manager_update'),
    path('analytics/', views.analytics, name='analytics'),
    path('upload/', views.upload_excel, name='upload_excel'),
]
