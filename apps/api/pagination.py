"""
apps/api/pagination.py
Paginación estándar para la API REST.

Todas las respuestas paginadas incluyen:
- count: total de registros
- next: URL de la siguiente página (null si no hay)
- previous: URL de la página anterior (null si no hay)
- results: lista de objetos
"""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    Paginación por defecto para todos los endpoints.
    
    Uso:
        GET /api/v1/maestros/productos/?page=2&page_size=25
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class LargePagination(PageNumberPagination):
    """
    Paginación para sync de datos maestros.
    Permite batches más grandes para reducir roundtrips.
    
    Uso:
        GET /api/v1/maestros/productos/?page_size=500
    """
    page_size = 200
    page_size_query_param = 'page_size'
    max_page_size = 500


class NotificacionesPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
