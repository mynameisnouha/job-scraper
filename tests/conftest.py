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
