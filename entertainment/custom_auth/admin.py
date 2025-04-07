from django.contrib import admin

# Register your models here.
from .models import CustomUser, Genre, Keyword, Country, Watchlist, Review

admin.site.register(CustomUser)
admin.site.register(Genre)
admin.site.register(Keyword)
admin.site.register(Country)
admin.site.register(Watchlist)
admin.site.register(Review)
