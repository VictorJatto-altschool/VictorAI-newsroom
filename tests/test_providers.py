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


def test_anthropic_first_when_key_present(monkeypatch):
    import victor.ai.anthropic_provider as ap

    class FakeClient:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr("anthropic.Anthropic", FakeClient)
    env = Env(anthropic_api_key="sk-ant-x", groq_api_key="q")
    p = pick_provider(env, ["anthropic", "gemini", "compat", "groq", "mock"])
    assert p.name == "anthropic" and p.model == ap.DEFAULT_MODEL == "claude-opus-5-5"
    env2 = Env(anthropic_api_key="sk-ant-x", anthropic_model="claude-sonnet-5-5")
    assert pick_provider(env2, ["anthropic", "mock"]).model == "claude-sonnet-5-5"


def test_order_is_respected():
    env = Env(gemini_api_key="g", groq_api_key="q")
    assert pick_provider(env, ["groq", "gemini", "mock"]).name == "groq"
    assert pick_provider(env, ["gemini", "groq", "mock"]).name == "gemini"
