from victor.ai.base import ProviderError
from victor.ai.compat import OpenAICompatProvider


class Fake(OpenAICompatProvider):
    def __init__(self):
        super().__init__("groq", "https://example/v1", "k", "llama-3.3-70b-versatile")
        self.calls = []

    def list_models(self):
        return ["whisper-large-v3", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]

    def _complete(self, system, user, max_tokens=600):
        self.calls.append(self.model)
        if self.model == "llama-3.3-70b-versatile":
            raise ProviderError('groq http 404: {"error":{"message":"The model `llama-3.3-70b-versatile` does not exist"}}')
        return '{"post": "ok", "why_it_matters": "x", "reason": "y"}'


def test_vanished_model_falls_back_to_best_listed():
    p = Fake()
    assert "ok" in p.complete("s", "u")
    assert p.calls == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]
    assert p.model == "openai/gpt-oss-120b"


def test_other_errors_are_not_retried():
    class Broken(Fake):
        def _complete(self, system, user, max_tokens=600):
            raise ProviderError("groq http 429: rate limited")

    try:
        Broken().complete("s", "u")
    except ProviderError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("should have raised")
