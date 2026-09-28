from .models import AuditLog


def log_action(
    *, actor, action: str, target, before: dict | None = None, after: dict | None = None
) -> AuditLog:
    """
    Records one sensitive-action audit entry. `target` is any model instance
    with a `public_id` (every domain model does, via PublicIdModel) — its
    class name and public_id are stored so the log survives even if the
    target row is later deleted.
    """
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target.__class__.__name__,
        target_id=str(target.public_id),
        before=before or {},
        after=after or {},
    )
