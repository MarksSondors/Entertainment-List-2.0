from rest_framework import serializers
from custom_auth.models import TVShow

class TVShowSerializer(serializers.ModelSerializer):
    class Meta:
        model = TVShow
        fields = '__all__'