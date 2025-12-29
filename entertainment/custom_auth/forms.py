from django import forms
from .models import Meme
import re

class MemeForm(forms.ModelForm):
    class Meta:
        model = Meme
        fields = ['tiktok_url', 'caption']
        widgets = {
            'tiktok_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://www.tiktok.com/@user/video/...'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Add a caption (optional)'}),
        }

    def clean_tiktok_url(self):
        url = self.cleaned_data['tiktok_url']
        # Regex to extract video ID
        match = re.search(r'/video/(\d+)', url)
        if not match:
            raise forms.ValidationError("Invalid TikTok URL. Could not extract video ID. Please use the full URL (e.g., https://www.tiktok.com/@user/video/123...)")
        return url

    def save(self, commit=True):
        instance = super().save(commit=False)
        match = re.search(r'/video/(\d+)', instance.tiktok_url)
        if match:
            instance.video_id = match.group(1)
        if commit:
            instance.save()
        return instance
