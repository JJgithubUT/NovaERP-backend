from django.core.paginator import Paginator

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def paginate(queryset, request, serialize):
    """Pagina un queryset ya ordenado/filtrado segun ?page=&page_size= y lo
    serializa con `serialize(obj) -> dict`. Devuelve el sobre estandar que
    consume el frontend: {count, page, page_size, num_pages, results}."""
    try:
        page_number = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page_number = 1

    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    paginator = Paginator(queryset, page_size)
    page_number = max(1, min(page_number, paginator.num_pages or 1))
    page = paginator.page(page_number)

    return {
        "count": paginator.count,
        "page": page_number,
        "page_size": page_size,
        "num_pages": paginator.num_pages,
        "results": [serialize(obj) for obj in page.object_list],
    }
