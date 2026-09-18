import os

import pytest

from backend.agents.llm_config import get_llm
from backend.core.config import get_settings


@pytest.mark.live
def test_real_provider_configured():
    if not os.getenv("RUN_LIVE_LLM_TESTS"):
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 and a real provider key")
    settings = get_settings()
    settings.app_env = "development"
    settings.require_llm_credentials()
    llm = get_llm()
    assert llm is not None
