"""Explorer page view — the JS-driven Windows-Explorer shell."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def explorer_page(request):
    return render(request, "explorer/index.html", {})
