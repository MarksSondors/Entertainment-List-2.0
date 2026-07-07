from rest_framework import serializers

from explorer.utils import build_thumbnail

from .models import Game


class GameListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for explorer list cards."""

    media_type = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    genre_names = serializers.SerializerMethodField()
    platform_names = serializers.SerializerMethodField()
    user_rating = serializers.FloatField(read_only=True, allow_null=True)
    user_rating_count = serializers.IntegerField(read_only=True, allow_null=True)
    on_my_watchlist = serializers.BooleanField(read_only=True, default=False)
    reviewed_by_me = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = Game
        fields = [
            "id",
            "media_type",
            "title",
            "original_title",
            "thumbnail",
            "year",
            "release_date",
            "rating",
            "user_rating",
            "user_rating_count",
            "on_my_watchlist",
            "reviewed_by_me",
            "metacritic",
            "genre_names",
            "platform_names",
            "date_added",
        ]

    def get_media_type(self, obj):
        return "games"

    def get_thumbnail(self, obj):
        return build_thumbnail(obj.poster)

    def get_year(self, obj):
        return obj.release_date.year if obj.release_date else None

    def get_genre_names(self, obj):
        return [g.name for g in obj.genres.all()]

    def get_platform_names(self, obj):
        return [p.name for p in obj.platforms.all()]
