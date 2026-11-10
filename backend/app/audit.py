from sqlalchemy.orm import Session

from .models import AuditLog
from .services.common import Actor


def record(db: Session, actor: Actor, action: str, entity_type: str, entity_id: str,
           summary: str = "", space_id: str | None = None, details: dict | None = None) -> None:
    db.add(AuditLog(actor_type=actor.type, actor_id=actor.id, actor_name=actor.name, action=action,
                    entity_type=entity_type, entity_id=str(entity_id), space_id=space_id,
                    summary=summary, details=details or {}, request_id=actor.request_id, ip=actor.ip))
