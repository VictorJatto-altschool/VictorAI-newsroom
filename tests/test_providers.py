from victor.ai.draft import pick_provider
from victor.config import Env


def test_mock_when_nothing_configured():
    assert pick_provider(Env(), ["gemini", "compat", "groq", "mock"]).name == "mock"


def test_compat_preset_detected_from_base_url():
    env = Env(ai_base_url="https://models.github.ai/inference", ai_api_key="ghp_x")
    p = pick_provider(env, ["gemini", "compat", "groq", "mock"])
    assert p.name == "github" and p.model == "openai/gpt-4o-mini"


def test_explicit_model_wins_and_unknown_host_is_generic():
    env = Env(ai_base_url="https://llm.example.org/v1", ai_api_key="k", ai_model="my-model")
    p = pick_provider(env, ["compat", "mock"])
    assert p.name == "compat" and p.model == "my-model"


def test_unknown_host_without_model_falls_through_to_mock():
    env = Env(ai_base_url="https://llm.example.org/v1", ai_api_key="k")
    assert pick_provider(env, ["compat", "mock"]).name == "mock"


def test_order_is_respected():
    env = Env(gemini_api_key="g", groq_api_key="q")
    assert pick_provider(env, ["groq", "gemini", "mock"]).name == "groq"
    assert pick_provider(env, ["gemini", "groq", "mock"]).name == "gemini"
