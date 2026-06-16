from .context import reset_current_tenant


class ClearTenantContextMiddleware:
    """Ensures tenant context from one request cannot leak into the next one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        tokens = getattr(request, '_tenant_context_tokens', None)
        if tokens:
            reset_current_tenant(tokens)
        return response
