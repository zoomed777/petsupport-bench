import pytest
from pathlib import Path
import tempfile


@pytest.fixture
def prompts_dir():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "react.txt").write_text("prompt content", encoding="utf-8")
        yield base


class TestPromptRegistry:
    def test_load_prompt_returns_content(self, prompts_dir):
        from app.services.prompt_registry import PromptRegistry
        reg = PromptRegistry(prompts_dir)
        assert reg.load_prompt() == "prompt content"

    def test_load_prompt_missing_file_uses_default(self, prompts_dir):
        from app.services.prompt_registry import PromptRegistry
        empty_dir = Path(tempfile.mkdtemp())
        reg = PromptRegistry(empty_dir)
        text = reg.load_prompt()
        assert len(text) > 0
        assert "智能客服" in text
