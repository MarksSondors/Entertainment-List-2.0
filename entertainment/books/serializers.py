from rest_framework import serializers

from explorer.utils import build_thumbnail

from .models import Book


class BookListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for explorer list cards."""

    media_type = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    author_names = serializers.SerializerMethodField()
    genre_names = serializers.SerializerMethodField()
    user_rating = serializers.FloatField(read_only=True, allow_null=True)
    user_rating_count = serializers.IntegerField(read_only=True, allow_null=True)
    on_my_watchlist = serializers.BooleanField(read_only=True, default=False)
    reviewed_by_me = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = Book
        fields = [
            "id",
            "media_type",
            "title",
            "original_title",
            "subtitle",
            "thumbnail",
            "year",
            "published_date",
            "rating",
            "user_rating",
            "user_rating_count",
            "on_my_watchlist",
            "reviewed_by_me",
            "pages",
            "language",
            "author_names",
            "genre_names",
            "date_added",
        ]

    def get_media_type(self, obj):
        return "books"

    def get_thumbnail(self, obj):
        return build_thumbnail(obj.image_url)

    def get_year(self, obj):
        return obj.published_date.year if obj.published_date else None

    def get_author_names(self, obj):
        return [a.name for a in obj.authors.all()]

    def get_genre_names(self, obj):
        return [g.name for g in obj.genres.all()]
