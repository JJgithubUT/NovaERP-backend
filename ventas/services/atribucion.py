"""Atribucion comercial: que vendedor queda asociado a cada documento.

Hasta RF-44 el unico responsable comercial era oportunidad.responsable_id. Los
reportes de ventas (RV-01..06) necesitan atribuir tambien cotizaciones, pedidos
y facturas, asi que la columna vendedor_id existe en las tres tablas desde
sql/2026-08-03_rv01_06_reportes_ventas.sql y este modulo centraliza como se
rellena y quien puede alterarla.
"""

from django.core.exceptions import ValidationError

from core.models import Usuario
from core.utils.errors import BusinessRuleError
from core.utils.permissions import PermissionDeniedError, tiene_permiso

# RF-31: sin este permiso un vendedor solo ve y opera lo suyo. Vive aqui porque
# ya no es solo del pipeline: gobierna tambien el alcance de los reportes
# (RN-04) y quien puede atribuir un documento a otra persona (RN-06).
PERMISO_VER_TODO = "ventas:pipeline:ver_todo"


def ve_todo(request):
    """El bypass de TENANT_ADMIN ya satisface este permiso."""
    return tiene_permiso(request, PERMISO_VER_TODO)


def resolver_vendedor(data, request, tenant, heredado=None):
    """RN-06: el vendedor de un documento es, por defecto, el actor.

    Se acepta un `vendedor_id` distinto en el payload solo si el actor tiene
    ventas:pipeline:ver_todo (un gerente registrando en nombre de su equipo);
    si no lo tiene es 403, no un silencioso "se ignora el campo".

    `heredado` es la atribucion del documento de origen -- la cotizacion de un
    pedido, el pedido de una factura -- y manda sobre el actor: la venta es de
    quien la trabajo, no de quien apreto el boton de facturar.
    """
    explicito = (data or {}).get("vendedor_id")
    if explicito:
        if str(explicito) != str(request.usuario_id) and not ve_todo(request):
            raise PermissionDeniedError(
                PERMISO_VER_TODO, "Solo puede atribuir documentos a si mismo."
            )
        # La FK apunta a core.usuario, que es global: sin este filtro por tenant
        # un admin podria atribuir la venta a un usuario de OTRA organizacion, y
        # los reportes de vendedores (RV-06) lo mostrarian al unir con usuario.
        # Un id inexistente o malformado tambien muere aqui, como 422 y no como
        # un IntegrityError 500 de la FK.
        try:
            existe = Usuario.objects.filter(tenant=tenant, id=explicito).exists()
        except (ValueError, ValidationError):
            existe = False
        if not existe:
            raise BusinessRuleError(
                "Vendedor no encontrado en la organizacion.", campo="vendedor_id"
            )
        return explicito

    return heredado or request.usuario_id
