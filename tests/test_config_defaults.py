from __future__ import annotations

import importlib
import os


REQUIRED_ENV: dict[str, str] = {
    "XAI_API_KEY": "test-key",
    "TELEGRAM_BOT_TOKEN": "token",
    "WEBHOOK_URL": "https://example.com",
    "WEBHOOK_SECRET": "secret",
}


def _load_config_module() -> object:
    for key, value in REQUIRED_ENV.items():
        os.environ[key] = value
    import config

    return importlib.reload(config)


def test_reasoning_model_default_is_grok_420_beta() -> None:
    config_module = _load_config_module()
    assert (
        config_module.settings.xai_model_reasoning
        == "grok-4.20-0309-reasoning"
    )


def test_fast_model_default_is_grok_420_non_reasoning() -> None:
    config_module = _load_config_module()
    assert (
        config_module.settings.xai_model_fast
        == "grok-4.20-0309-non-reasoning"
    )


def test_model_defaults_can_be_overridden_by_env() -> None:
    os.environ["XAI_MODEL_REASONING"] = "reasoning-override"
    os.environ["XAI_MODEL_FAST"] = "fast-override"
    config_module = _load_config_module()
    assert config_module.settings.xai_model_reasoning == "reasoning-override"
    assert config_module.settings.xai_model_fast == "fast-override"
