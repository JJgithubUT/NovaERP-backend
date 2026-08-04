class ParametroInvalido(Exception):
    """Parametro de consulta mal formado -> HTTP 400. Se distingue de
    BusinessRuleError (422) en que aqui la peticion ni siquiera llega a tener
    sentido: un enum fuera del catalogo, una fecha que no es fecha, un limit no
    numerico. El 422 es para una peticion bien formada que rompe una regla."""

    def __init__(self, detail, campo=None):
        self.detail = detail
        self.campo = campo
        super().__init__(detail)

    def to_dict(self):
        payload = {"detail": self.detail}
        if self.campo:
            payload["campo"] = self.campo
        return payload


class BusinessRuleError(Exception):
    """Violacion de una regla de negocio -> HTTP 422. `campo` senala el
    campo del formulario en conflicto (opcional); `extra` agrega datos
    adicionales al payload (ej. saldo_pendiente)."""

    def __init__(self, detail, campo=None, extra=None):
        self.detail = detail
        self.campo = campo
        self.extra = extra or {}
        super().__init__(detail)

    def to_dict(self):
        payload = {"detail": self.detail}
        if self.campo:
            payload["campo"] = self.campo
        payload.update(self.extra)
        return payload
