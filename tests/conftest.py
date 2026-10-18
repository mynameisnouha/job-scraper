import os
import sys
from unittest.mock import MagicMock

# Set required env vars for config/supabase_utils
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
os.environ.setdefault("LLM_API_KEY", "test-api-key")

# Mock heavy external modules before any test imports
_MOCK_MODULES = [
    "litellm",
    "supabase",
    "pdfplumber",
    "reportlab",
    "playwright",
    "google.genai",
]

for mod_name in _MOCK_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock submodules. Only stub 'google' if the real namespace package isn't installed —
# stubbing it unconditionally shadows google.protobuf, which streamlit needs.
import types
try:
    import google  # noqa: F401
except ImportError:
    sys.modules["google"] = types.ModuleType("google")
if "google.genai" not in sys.modules:
    sys.modules["google.genai"] = types.ModuleType("google.genai")

# Ensure supabase has create_client
sys.modules["supabase"].create_client = MagicMock()
sys.modules["supabase"].Client = MagicMock()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_message_batches(monkeypatch):
    """Batching off unless a test turns it on: a batch polls for minutes, and the
    per-job path is the one most tests patch."""
    import config
    monkeypatch.setattr(config, "LLM_BATCH_ENABLED", False)


@pytest.fixture(autouse=True)
def _isolated_apply_documents(tmp_path, monkeypatch):
    """Keep the owner's real uploaded CV (apply_documents.json) out of every test."""
    from apply import documents
    monkeypatch.setattr(documents, "SETTINGS_PATH", str(tmp_path / "apply_documents.json"))
    monkeypatch.setattr(documents, "DOCUMENTS_DIR", str(tmp_path / "cv"))
