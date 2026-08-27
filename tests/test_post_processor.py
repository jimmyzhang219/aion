"""PostProcessor / strip_dsml / strip_cot_tags 单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aion.agent.post_processor import strip_cot_tags, strip_dsml


class TestStripFunctions:
    def test_strip_cot_tags_removes_final_tags(self):
        assert strip_cot_tags("Hello <final>world</final>") == "Hello world"
        assert strip_cot_tags("<final>Done</final>") == "Done"

    def test_strip_cot_tags_html_entities(self):
        assert strip_cot_tags("Hello &lt;final&gt;world&lt;/final&gt;") == "Hello world"

    def test_strip_cot_tags_no_tags(self):
        assert strip_cot_tags("Hello world") == "Hello world"

    def test_strip_cot_tags_empty(self):
        assert strip_cot_tags("") == ""

    def test_strip_dsml_removes_tool_calls_block(self):
        text = "思考过程<||DSML||tool_calls>some call</||DSML||tool_calls>回复"
        result = strip_dsml(text)
        assert "DSML" not in result
        assert "思考过程" in result
        assert "回复" in result

    def test_strip_dsml_pure_dsml_returns_empty(self):
        text = "<||DSML||tool_calls><||DSML||invoke>...</||DSML||invoke></||DSML||tool_calls>"
        assert strip_dsml(text) == ""

    def test_strip_dsml_no_tags(self):
        assert strip_dsml("你好世界") == "你好世界"

    def test_strip_dsml_empty(self):
        assert strip_dsml("") == ""
