class DomainError(Exception):
    code = "domain_error"
    default_message = "The operation could not be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        super().__init__(self.message)


class EntityNotFound(DomainError):
    code = "entity_not_found"
    default_message = "The requested resource was not found."


class AccessDenied(DomainError):
    code = "access_denied"
    default_message = "You do not have permission to perform this operation."


class BusinessRuleViolation(DomainError):
    code = "business_rule_violation"
    default_message = "The operation violates a business rule."


class Conflict(DomainError):
    code = "conflict"
    default_message = "The operation conflicts with the current resource state."


class AuthenticationRequired(DomainError):
    code = "authentication_required"
    default_message = "Valid authentication is required."
