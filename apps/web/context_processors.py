from apps.web.session import get_display_currency


def display_currency(request):
    return {"display_currency": get_display_currency(request)}
