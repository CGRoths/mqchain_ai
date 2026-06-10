from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DBSession
from app.models.intake import ApprovedAddress, ApprovedAddressEvidence, ApprovedAddressRole, Entity


api_router = APIRouter(prefix="/registry", tags=["registry"])


@api_router.get("/approved-addresses")
def approved_addresses(
    db: DBSession,
    entity_name: str | None = None,
    chain_slug: str | None = None,
    address_class: str | None = None,
    role: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    evidence_count = func.count(ApprovedAddressEvidence.id).label("evidence_count")
    stmt = (
        select(Entity, ApprovedAddress, ApprovedAddressRole, evidence_count)
        .join(ApprovedAddress, ApprovedAddress.entity_id == Entity.id)
        .join(ApprovedAddressRole, ApprovedAddressRole.approved_address_id == ApprovedAddress.id)
        .outerjoin(ApprovedAddressEvidence, ApprovedAddressEvidence.approved_address_id == ApprovedAddress.id)
        .group_by(Entity.id, ApprovedAddress.id, ApprovedAddressRole.id)
        .order_by(Entity.entity_name.asc(), ApprovedAddress.chain_slug.asc(), ApprovedAddress.normalized_address.asc(), ApprovedAddressRole.role.asc())
        .limit(limit)
        .offset(offset)
    )
    if entity_name:
        stmt = stmt.where(Entity.entity_name == entity_name)
    if chain_slug:
        stmt = stmt.where(ApprovedAddress.chain_slug == chain_slug)
    if address_class:
        stmt = stmt.where(ApprovedAddress.address_class == address_class)
    if role:
        stmt = stmt.where(ApprovedAddressRole.role == role)

    rows = []
    for entity, approved, approved_role, count in db.execute(stmt):
        rows.append(
            {
                "entity_name": entity.entity_name,
                "chain_slug": approved.chain_slug,
                "source_network": approved.source_network,
                "address": approved.address,
                "normalized_address": approved.normalized_address,
                "address_class": approved.address_class,
                "role": approved_role.role,
                "source_trust_status": approved.source_trust_status,
                "confidence_score": approved.confidence_score,
                "status": approved.status,
                "evidence_count": int(count or 0),
                "first_approved_at": approved.first_approved_at,
            }
        )
    return rows
