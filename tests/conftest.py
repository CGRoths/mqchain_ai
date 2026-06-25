from __future__ import annotations

import os


os.environ.setdefault("MQCHAIN_AI_DATABASE_URL", "sqlite:///./data/test_mqchain_ai.db")
os.environ.setdefault("MQCHAIN_AI_STAGED_ARTIFACT_DIR", "./data/test_staged_artifacts")
