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

# Mock submodules
import types
for mod_name in ["google", "google.genai"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Ensure supabase has create_client
sys.modules["supabase"].create_client = MagicMock()
sys.modules["supabase"].Client = MagicMock()
