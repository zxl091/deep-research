"""模型切换回归：一处环境变量覆盖默认模型及全部研究角色。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.llm_config import LLMConfig, reload_config


class ModelConfigTests(unittest.TestCase):
    def test_one_setting_controls_every_role(self):
        for model in ('qwen3.7-flash', 'fixture-next-model'):
            with self.subTest(model=model), patch.dict(os.environ, {'OPENAI_MODEL': model, 'RESEARCH_SEARCH_MODEL': ''}):
                config = reload_config()
                self.assertEqual(config.default_model, model)
                for role in ('architect', 'scout', 'data_analyst', 'wizard', 'critic', 'writer', 'unknown'):
                    self.assertEqual(config.get_agent_config(role).model, model)

    def test_blank_setting_keeps_working_default(self):
        with patch.dict(os.environ, {'OPENAI_MODEL': '  '}):
            self.assertEqual(LLMConfig().default_model, 'qwen3.7-plus')

    def test_existing_provider_endpoint_and_explicit_override(self):
        with patch.dict(os.environ, {'DASHSCOPE_BASE_URL': 'https://provider.example/v1'}, clear=True):
            self.assertEqual(LLMConfig().base_url, 'https://provider.example/v1')
            with patch.dict(os.environ, {'LLM_BASE_URL': 'https://override.example/v1'}):
                self.assertEqual(LLMConfig().base_url, 'https://override.example/v1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
