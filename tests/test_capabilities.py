"""llmcapa 能力封装单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestGetSupportedModalities:
    """get_supported_modalities 测试"""

    def test_known_model_deepseek(self):
        """deepseek-v4-flash 只支持 text"""
        from aion.llm.capabilities import get_supported_modalities

        modalities = get_supported_modalities("deepseek-v4-flash")
        assert modalities == {"text"}

    def test_known_model_gpt4o(self):
        """gpt-4o 支持 text 和 image"""
        from aion.llm.capabilities import get_supported_modalities

        modalities = get_supported_modalities("gpt-4o")
        assert "text" in modalities
        assert "image" in modalities

    def test_unknown_model_conservative(self):
        """未知模型保守假设只支持 text"""
        from aion.llm.capabilities import get_supported_modalities

        modalities = get_supported_modalities("completely-unknown-model-v99")
        assert modalities == {"text"}


class TestCheckModalitySupport:
    """check_modality_support 测试"""

    def test_supported_modality(self):
        """deepseek 支持 text，检查 text 应返回 (True, set())"""
        from aion.llm.capabilities import check_modality_support

        ok, unsupported = check_modality_support("deepseek-v4-flash", {"text"})
        assert ok
        assert unsupported == set()

    def test_unsupported_modality(self):
        """deepseek 不支持 image，检查 image 应返回 (False, {"image"})"""
        from aion.llm.capabilities import check_modality_support

        ok, unsupported = check_modality_support("deepseek-v4-flash", {"image"})
        assert not ok
        assert unsupported == {"image"}

    def test_partial_support(self):
        """gpt-4o 支持 text/image 但不支持 audio"""
        from aion.llm.capabilities import check_modality_support

        ok, unsupported = check_modality_support("gpt-4o", {"text", "image", "audio"})
        assert not ok
        assert unsupported == {"audio"}

    def test_all_supported(self):
        """gpt-4o 支持 text/image，全检查通过"""
        from aion.llm.capabilities import check_modality_support

        ok, unsupported = check_modality_support("gpt-4o", {"text", "image"})
        assert ok
        assert unsupported == set()
