from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compact_label import RoleDict, RoleProposal


ROLE_ALIASES = {
    "cex_por_wallet": "cex_por_cold_wallet",
    "reserve_wallet": "cex_por_cold_wallet",
    "cold_wallet": "cex_cold_wallet",
    "hot_wallet": "cex_hot_wallet",
    "factory_contract": "protocol_factory",
    "registry_contract": "protocol_registry",
    "router_contract": "protocol_router",
    "lending_pool": "protocol_pool",
    "lending_market": "protocol_pool",
    "vault": "protocol_vault",
    "treasury": "protocol_treasury",
    "oracle": "protocol_oracle",
    "address_provider": "aave_pool_addresses_provider",
    "pool_addresses_provider": "aave_pool_addresses_provider",
    "pool": "aave_pool",
    "dex_factory": "dex_factory",
    "dex_router": "dex_router",
    "bridge_vault": "bridge_vault",
}


@dataclass(frozen=True)
class RoleMappingResult:
    role_code: str
    role_id: int | None
    proposal_id: int | None
    status: str

    @property
    def can_commit(self) -> bool:
        return self.role_id is not None and self.status == "mapped"


def normalize_role_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        return None
    return ROLE_ALIASES.get(normalized, normalized)


def map_role_or_propose(
    db: Session,
    role_text: str | None,
    *,
    source_job_id: int | None = None,
    example_addresses: list[str] | None = None,
    category_code: str | None = None,
    role_group: str | None = None,
    reason: str = "unknown_role_requires_dictionary_review",
) -> RoleMappingResult:
    role_code = normalize_role_code(role_text)
    if role_code is None:
        proposal = _get_or_create_proposal(
            db,
            "unknown",
            source_job_id=source_job_id,
            example_addresses=example_addresses or [],
            category_code=category_code,
            role_group=role_group,
            reason=reason,
        )
        return RoleMappingResult(role_code="unknown", role_id=None, proposal_id=proposal.id, status="proposal_created")

    role = db.scalar(select(RoleDict).where(RoleDict.role_code == role_code, RoleDict.is_active.is_(True)))
    if role is not None:
        return RoleMappingResult(role_code=role.role_code, role_id=role.role_id, proposal_id=None, status="mapped")

    proposal = _get_or_create_proposal(
        db,
        role_code,
        source_job_id=source_job_id,
        example_addresses=example_addresses or [],
        category_code=category_code,
        role_group=role_group,
        reason=reason,
    )
    return RoleMappingResult(role_code=role_code, role_id=None, proposal_id=proposal.id, status="proposal_created")


def _get_or_create_proposal(
    db: Session,
    role_code: str,
    *,
    source_job_id: int | None,
    example_addresses: list[str],
    category_code: str | None,
    role_group: str | None,
    reason: str,
) -> RoleProposal:
    proposal = db.scalar(
        select(RoleProposal).where(
            RoleProposal.proposed_role_code == role_code,
            RoleProposal.source_job_id == source_job_id,
            RoleProposal.status == "pending",
        )
    )
    if proposal is None:
        proposal = RoleProposal(
            proposed_role_code=role_code,
            category_code=category_code,
            role_group=role_group,
            source_job_id=source_job_id,
            candidate_count=0,
            example_addresses_json=[],
            reason=reason,
            status="pending",
        )
        db.add(proposal)
        db.flush()
    proposal.candidate_count += 1
    existing = list(proposal.example_addresses_json or [])
    for address in example_addresses:
        if address not in existing:
            existing.append(address)
    proposal.example_addresses_json = existing[:20]
    db.flush()
    return proposal
