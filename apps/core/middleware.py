class LocalePreferenceMiddleware:
    """
    Reads Accept-Language and X-Currency headers and attaches the resolved
    language/currency to the request for views/serializers to use.
    Full preferred_language/preferred_currency resolution against the
    authenticated user lands in apps.core in Step 3.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.bbms_language = request.headers.get("Accept-Language", "km").split(",")[0][:2]
        request.bbms_currency = request.headers.get("X-Currency", "KHR")
        return self.get_response(request)
