from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.network_normalizer import NetworkNormalizer
from app.review.candidate_audit import (
    classify_approval_readiness,
    classify_candidate_address_class,
    classify_source_trust_status,
)
from app.models.intake import (
    AddressCandidate,
    ApprovalEvent,
    ApprovedAddress