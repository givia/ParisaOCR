"""The page labeller's OpenRouter path (parisaocr.ebook.gemini.OpenRouter): requests translated from generateContent's
shape, replies translated back, failures inside HTTP 200 replies retried, keys and folder names. No network: urlopen
is a stand-in."""
import io
import json
import urllib.error

import pytest
from PIL import Image

from parisaocr.ebook import gemini

MODEL = "openrouter:google/gemma-4-31b-it"


@pytest.fixture(autouse=True)
def no_model_list(monkeypatch):
    """OpenRouter's model list is not fetched in tests (a test that needs entries sets its own)."""
    monkeypatch.setattr(gemini, "openrouter_models", lambda: {})
    monkeypatch.delenv("PARISAOCR_OPENROUTER_EXTRA", raising=False)


def _walk(schema):
    yield schema
    for sub in schema.get("properties", {}).values():
        yield from _walk(sub)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_json_schema():
    js = gemini.json_schema(gemini.PAGE_SCHEMA)
    nodes = list(_walk(js))
    assert all(n["type"] == n["type"].lower() for n in nodes)
    assert not any("propertyOrdering" in n for n in nodes)
    assert js["properties"]["lines"]["items"]["properties"]["r"]["enum"] == gemini.ROLES
    assert js["required"] == ["page", "lines"]


class FakeOpen:
    """urlopen stand-in: replies in turn (a dict is a 200 JSON body, an int an HTTP error); keeps the requests."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            raise urllib.error.HTTPError(req.full_url, reply, "error", {}, io.BytesIO(b'{"error": {"message": "no credits"}}'))
        return io.BytesIO(json.dumps(reply).encode())


def _reply(text, cost=0.0003, finish="stop"):
    return {"model": "google/gemma-4-31b-it", "provider": "SomeProvider",
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 2100, "completion_tokens": 400, "cost": cost}}


def test_openrouter_request_and_reply(monkeypatch):
    answer = {"page": {"type": "text", "pn": "5"}, "lines": [{"i": 0, "r": "heading", "l": 1}, {"i": 1, "r": "body"}]}
    fake = FakeOpen([_reply(json.dumps(answer) + "\n```")])
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    monkeypatch.delenv("PARISAOCR_OPENROUTER_PROVIDER", raising=False)
    client = gemini.client_for(MODEL, "sk-test")
    contents = [{"role": "user", "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": "QUJD"}}, {"text": "the prompt"}]},
                {"role": "model", "parts": [{"text": "{}"}]}, {"role": "user", "parts": [{"text": "again"}]}]
    resp = client.generate(contents, gemini.PAGE_SCHEMA)
    req = fake.requests[0]
    body = json.loads(req.data)
    assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer sk-test"
    assert body["model"] == "google/gemma-4-31b-it" and body["temperature"] == 0
    assert body["messages"][0]["content"] == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                                              {"type": "text", "text": "the prompt"}]
    assert body["messages"][1] == {"role": "assistant", "content": "{}"} and body["messages"][2]["role"] == "user"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == gemini.json_schema(gemini.PAGE_SCHEMA)
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == gemini.OPENROUTER_PROVIDER and body["provider"]["data_collection"] == "deny"
    text, finish, err = gemini.answer_of(resp)
    assert err is None and finish == "STOP"
    out, problems = gemini.validate(text, 2)
    assert not problems and out["lines"][0] == {"i": 0, "r": "heading", "l": 1}
    assert gemini.usage_of(resp) == {"input": 2100, "output": 400, "cost": 0.0003}
    assert resp["provider"] == "SomeProvider"


def test_openrouter_provider_override(monkeypatch):
    """An override narrows the routing; the privacy and schema policy always holds, and weakening it stops the run."""
    ask = [{"role": "user", "parts": [{"text": "t"}]}]
    monkeypatch.setenv("PARISAOCR_OPENROUTER_PROVIDER", '{"order": ["X"], "allow_fallbacks": false}')
    monkeypatch.setenv("PARISAOCR_OPENROUTER_EXTRA", '{"provider": {"quantizations": ["bf16"]}}')
    body = gemini.OpenRouter(MODEL, "k").body(ask, gemini.META_SCHEMA)
    assert body["provider"] == {"order": ["X"], "allow_fallbacks": False, "quantizations": ["bf16"], **gemini.OPENROUTER_PROVIDER}
    for env, value in (("PARISAOCR_OPENROUTER_PROVIDER", '{"zdr": false}'),
                       ("PARISAOCR_OPENROUTER_EXTRA", '{"provider": {"data_collection": "allow"}}')):
        monkeypatch.setenv(env, value)
        with pytest.raises(SystemExit, match="cannot change"):
            gemini.OpenRouter(MODEL, "k")
        monkeypatch.delenv(env)
    monkeypatch.setenv("PARISAOCR_OPENROUTER_PROVIDER", "{order: X}")
    with pytest.raises(SystemExit, match="not valid JSON"):
        gemini.OpenRouter(MODEL, "k")


def test_reasoning_off_where_optional(monkeypatch):
    """Pages are labelled without reasoning: a model that reasons by default is told not to, unless it must."""
    models = {"qwen/qwen3.8-27b": {"reasoning": {"mandatory": False, "default_enabled": True}},
              "qwen/qwen3-vl-30b-a3b-thinking": {"reasoning": {"mandatory": True}},
              "qwen/qwen3-vl-235b-a22b-instruct": {"reasoning": {}}}
    monkeypatch.setattr(gemini, "openrouter_models", lambda: models)
    ask = [{"role": "user", "parts": [{"text": "t"}]}]
    assert gemini.OpenRouter("openrouter:qwen/qwen3.8-27b", "k").body(ask, gemini.META_SCHEMA)["reasoning"] == {"enabled": False}
    assert "reasoning" not in gemini.OpenRouter("openrouter:qwen/qwen3-vl-30b-a3b-thinking", "k").body(ask, gemini.META_SCHEMA)
    assert "reasoning" not in gemini.OpenRouter("openrouter:qwen/qwen3-vl-235b-a22b-instruct", "k").body(ask, gemini.META_SCHEMA)
    monkeypatch.setenv("PARISAOCR_OPENROUTER_EXTRA", '{"reasoning": {"effort": "low"}, "seed": 1}')
    body = gemini.OpenRouter("openrouter:qwen/qwen3.8-27b", "k").body(ask, gemini.META_SCHEMA)
    assert body["reasoning"] == {"effort": "low"} and body["seed"] == 1


def test_openrouter_failures(monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    # a provider failing inside an HTTP 200 reply, then a reply cut by an error finish: both asked again
    fake = FakeOpen([{"error": {"code": 502, "message": "provider down"}}, _reply("{}", finish="error"), _reply('{"lines": []}')])
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    resp = gemini.OpenRouter(MODEL, "k").generate([{"role": "user", "parts": [{"text": "t"}]}], gemini.PAGE_SCHEMA)
    assert len(fake.requests) == 3 and gemini.answer_of(resp)[0] == '{"lines": []}'
    # out of credits: no retry, an HTTP 4xx error (label_book stops asking on it)
    monkeypatch.setattr(gemini.urllib.request, "urlopen", FakeOpen([402]))
    with pytest.raises(gemini.ApiError, match=r"^HTTP 402: .*no credits"):
        gemini.OpenRouter(MODEL, "k").generate([{"role": "user", "parts": [{"text": "t"}]}], gemini.PAGE_SCHEMA)
    # a length cut is MAX_TOKENS, which validate() then judges, as with Gemini
    assert gemini.OpenRouter(MODEL, "k").as_gemini(_reply('{"li', finish="length"))["candidates"][0]["finishReason"] == "MAX_TOKENS"


def test_names_keys_and_models(monkeypatch, tmp_path):
    assert gemini.labels_dirname("gemini-3.8-flash") == "gemini-gemini-3.8-flash"  # unchanged: old work folders still found
    assert gemini.labels_dirname(MODEL) == "gemini-openrouter-google-gemma-4-31b-it"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="openrouter/api_key"):
        gemini.load_key(MODEL)
    (tmp_path / ".config" / "openrouter").mkdir(parents=True)
    (tmp_path / ".config" / "openrouter" / "api_key").write_text("sk-file\n")
    assert gemini.load_key(MODEL) == "sk-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert gemini.load_key(MODEL) == "sk-env"
    models = {"google/gemma-4-31b-it": {"architecture": {"input_modalities": ["text", "image"]}, "pricing": {"prompt": "0.0000001", "completion": "0.0000003"}},
              "some/text-only": {"architecture": {"input_modalities": ["text"]}}}
    monkeypatch.setattr(gemini, "openrouter_models", lambda: models)
    assert gemini.resolve_model(MODEL, "k") == MODEL
    with pytest.raises(SystemExit, match="does not read images"):
        gemini.resolve_model("openrouter:some/text-only", "k")
    with pytest.raises(SystemExit, match="does not offer"):
        gemini.resolve_model("openrouter:no/such-model", "k")
    assert gemini.price_of(MODEL) == pytest.approx((0.1, 0.3))
    assert gemini.cost({"input": 1_000_000, "output": 0}, MODEL) == pytest.approx(0.1)
    assert gemini.cost({"input": 5, "output": 5, "cost": 0.25}, MODEL) == 0.25  # what OpenRouter charged wins


def test_label_book_through_openrouter(monkeypatch, tmp_path):
    """A whole (one-page) book labelled through OpenRouter: the page, the book meta and the outline are asked, and the
    cost OpenRouter reports is summed."""
    ocr = tmp_path / "ocr"
    (ocr / "pages").mkdir(parents=True)
    (ocr / "jsonl").mkdir()
    Image.new("L", (600, 800), 255).save(ocr / "pages" / "p-001.png")
    rows = [{"text": "فصل یکم", "bbox": [200, 100, 400, 140], "conf": 95, "words": [], "column": 0},
            {"text": "متن کتاب در این صفحه", "bbox": [100, 200, 500, 230], "conf": 95, "words": [], "column": 0}]
    (ocr / "jsonl" / "p-001.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    asked = []

    def generate(self, contents, schema):
        asked.append(schema)
        if schema is gemini.PAGE_SCHEMA:
            a = {"page": {"type": "opening", "pn": ""}, "lines": [{"i": 0, "r": "heading", "l": 1}, {"i": 1, "r": "body", "p": True}]}
        elif schema is gemini.META_SCHEMA:
            a = {k: [] if k in gemini.NAMES else "none" for k in gemini.META_FIELDS}
        else:
            a = {"headings": [{"id": 0, "level": 1, "kind": "chapter", "title": "فصل یکم"}]}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(a, ensure_ascii=False)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100, "cost": 0.001}}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(gemini, "openrouter_models", lambda: {})
    monkeypatch.setattr(gemini.OpenRouter, "generate", generate)
    labels = tmp_path / gemini.labels_dirname(MODEL)
    summary = gemini.label_book(ocr, labels, model=MODEL, jobs=1)
    assert asked == [gemini.PAGE_SCHEMA, gemini.META_SCHEMA, gemini.OUTLINE_SCHEMA]
    page = json.loads((labels / "p-001.json").read_text(encoding="utf-8"))
    assert page["valid"] and page["model"] == MODEL and page["usage"] == {"input": 1000, "output": 100, "cost": 0.001}
    assert (labels / "book_outline.json").exists() and (labels / "book_meta.json").exists()
    assert summary.startswith(f"{MODEL}, 1 of 1 pages labelled, $0.00 this run")


def test_openrouter_page_errors_and_empty_replies(monkeypatch):
    """A cold start (no content, no finish reason) is asked again; a refusal of this page's content is a block, which
    label_book records instead of stopping the book."""
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    empty = _reply("", finish=None)
    fake = FakeOpen([empty, _reply('{"lines": []}')])
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    ask = [{"role": "user", "parts": [{"text": "t"}]}]
    assert gemini.answer_of(gemini.OpenRouter(MODEL, "k").generate(ask, gemini.PAGE_SCHEMA))[0] == '{"lines": []}'
    assert len(fake.requests) == 2

    def refused(req, timeout=None):
        body = b'{"error": {"code": 403, "message": "flagged", "metadata": {"error_type": "content_policy_violation"}}}'
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(body))
    monkeypatch.setattr(gemini.urllib.request, "urlopen", refused)
    with pytest.raises(gemini.ApiError, match=r"^blocked: content_policy_violation"):
        gemini.OpenRouter(MODEL, "k").generate(ask, gemini.PAGE_SCHEMA)
    reply = {"choices": [{"message": {"content": [{"type": "text", "text": '{"a": '}, {"type": "text", "text": "1}"}]},
                          "finish_reason": "stop"}]}
    assert gemini.answer_of(gemini.OpenRouter(MODEL, "k").as_gemini(reply))[0] == '{"a": 1}'  # content as parts


def test_checks_for_providers_that_bend_the_schema(monkeypatch, tmp_path):
    """What Gemini's schema enforcement guarantees is checked, so an off-schema answer is asked again, not stored."""
    good = {"page": {"type": "text", "pn": "5"}, "lines": [{"i": 0, "r": "heading", "l": 2}]}
    assert gemini.validate(json.dumps(good), 1)[1] == []
    for bad, why in (({"lines": good["lines"]}, 'no "page"'), (dict(good, page={"type": "chapter", "pn": ""}), "unknown page type"),
                     (dict(good, lines=[{"i": 0, "r": "heading", "l": 9}]), "heading level 9"), (dict(good, toc="none"), '"toc" is not a list')):
        assert any(why in p for p in gemini.validate(json.dumps(bad), 1)[1]), why

    class Client:
        model = MODEL

        def __init__(self, answer):
            self.answer = answer

        def generate(self, contents, schema):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.answer)}]}, "finishReason": "STOP"}]}
    assert gemini._ask(Client({"headings": [{"level": 1}]}), [{"text": "q"}], gemini.OUTLINE_SCHEMA)[2] == "not the expected JSON"
    assert gemini._ask(Client(["x"]), [{"text": "q"}], gemini.META_SCHEMA)[2] == "not the expected JSON"
    assert gemini._ask(Client({"headings": [{"id": 0, "level": 1}]}), [{"text": "q"}], gemini.OUTLINE_SCHEMA)[2] is None
    # a key file with more than the key is refused before anything is sent (it would end up in errors and labels)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".config" / "openrouter").mkdir(parents=True)
    (tmp_path / ".config" / "openrouter" / "api_key").write_text("sk-or-v1-abc\n# my key\n")
    with pytest.raises(SystemExit, match="the key alone"):
        gemini.load_key(MODEL)
    # a routing variant is its model
    monkeypatch.setattr(gemini, "openrouter_models", lambda: {"google/gemma-4-31b-it": {"architecture": {"input_modalities": ["image"]},
                                                                                       "pricing": {"prompt": "1e-7", "completion": "3e-7"}}})
    assert gemini.resolve_model(MODEL + ":floor", "k") == MODEL + ":floor"
    assert gemini.price_of(MODEL + ":floor") == pytest.approx((0.1, 0.3))


def test_markers_placed_after_the_labellers_words():
    """With the word each marker follows (targeted labels), the marker goes right after it: in place of digits the
    OCR glued or spaced there, split from a next word glued to it; a copy the OCR put elsewhere on the line goes."""
    from parisaocr.ebook.labels import _place_after_words
    from parisaocr.ebook.markers import MARK
    cases = [
        ("او این را در نامه‌ای کوتاه بیان کرد.»۱۰۸شاید بعدها", [108], ["کرد"], "او این را در نامه‌ای کوتاه بیان کرد.»۱۰۸ شاید بعدها"),
        ("این کتاب در آن سال‌ها نایاب به‌شمار می‌آمد. ۱۷", [107], ["می‌آمد"], "این کتاب در آن سال‌ها نایاب به‌شمار می‌آمد.۱۰۷"),
        ("این موضوع بررسی شده.۳۳ و سپس", [32], ["شده"], "این موضوع بررسی شده.۳۲ و سپس"),
        ("الف۵ ب ج", [5], ["ج"], "الف۵ ب ج"),  # the OCR read the marker glued to its word: kept (more precise)
        ("الف ب ج۴", [5], ["ج"], "الف ب ج۵"),  # misread there: replaced by the labeller's number
        ("نام مِهرداد و سیامَک آمده" + MARK, [1, 2], ["مهرداد", "سیامک"], "نام مِهرداد۱ و سیامَک۲ آمده" + MARK),  # a marker the labeller missed stays
        ("قرارداد" + MARK + " تا شرکت(XYZ)" + MARK + " که", [2], ["قرارداد"], "قرارداد۲ تا شرکت(XYZ)" + MARK + " که"),
        ("نظریه۲M بود", [2], ["نظریه"], "نظریه۲ M بود"),
        ("در نامه‌ای کوتاه بیان کرد.»۱۰۸شاید بعدها", [108], ["کرد.”"], "در نامه‌ای کوتاه بیان کرد.»۱۰۸ شاید بعدها"),  # punctuation in the answer
    ]
    assert _place_after_words("سفر با هواپیمای ۴ 76-11", [4], ["۴"]) is None  # the marker itself as "the word"
    for text, want, words, expected in cases:
        assert _place_after_words(text, want, words) == expected, text
    assert _place_after_words("متن بی‌ربط", [3], ["کلمه"]) is None  # the word is not on the line: old behaviour


def test_client_robustness(monkeypatch, tmp_path):
    """A reply cut short or not JSON is asked again, not a crash; off-type fields are not stored; a key saved with a
    BOM is read; the EXTRA variable cannot replace the request's core fields."""
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    good = {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}
    fake = FakeOpen([["not", "an", "object"], good])
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    assert gemini.Gemini("gemini-3.8-flash", "k").generate([{"role": "user", "parts": [{"text": "t"}]}], gemini.META_SCHEMA) == good
    answer = {"page": {"type": "text", "pn": "1"}, "lines": [{"i": 0, "r": "body", "m": [0]}],
              "toc": [{"t": "x", "pg": 12, "l": 1}], "missing": [{"r": "heading", "t": "y", "before": [3]}]}
    out, problems = gemini.validate(json.dumps(answer), 1)
    assert not problems and "m" not in out["lines"][0] and out["toc"] == [] and out["missing"] == []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".config" / "openrouter").mkdir(parents=True)
    (tmp_path / ".config" / "openrouter" / "api_key").write_bytes("\ufeffsk-or-v1-abc\n".encode("utf-8"))
    assert gemini.load_key(MODEL) == "sk-or-v1-abc"
    monkeypatch.setenv("PARISAOCR_OPENROUTER_EXTRA", '{"model": "other/model"}')
    with pytest.raises(SystemExit, match="cannot set model"):
        gemini.OpenRouter(MODEL, "k")


def test_llm_options(capsys):
    """--llm [MODEL], --llm-estimate, --llm-jobs, and the --gemini* names they replace; a book after --llm is refused."""
    from parisaocr.cli import llm_options, parser

    def opts(*extra):
        return llm_options(parser().parse_args(["epub", "book.pdf", "--out", "out", *extra]))

    def picked(o):
        return o.gemini, o.gemini_model, o.gemini_estimate, o.gemini_jobs

    assert picked(opts()) == (False, "gemini-3.8-flash", False, 8)
    assert picked(opts("--llm")) == (True, "gemini-3.8-flash", False, 8)
    assert picked(opts("--llm", "--cpu")) == (True, "gemini-3.8-flash", False, 8)
    assert picked(opts("--llm", MODEL, "--llm-jobs", "4")) == (True, MODEL, False, 4)
    assert picked(opts(f"--llm={MODEL}", "--llm-estimate")) == (True, MODEL, True, 8)
    assert picked(opts("--llm-estimate")) == (False, "gemini-3.8-flash", True, 8)
    assert picked(opts("--gemini", "--gemini-model", MODEL, "--gemini-jobs", "2")) == (True, MODEL, False, 2)
    assert picked(opts("--gemini-model", MODEL)) == (False, MODEL, False, 8)  # a model alone sends nothing
    assert picked(opts("--gemini-estimate")) == (False, "gemini-3.8-flash", True, 8)
    with pytest.raises(SystemExit):
        parser().parse_args(["epub", "--out", "out", "--llm", "book.pdf"])
    assert "'book.pdf' is not a model" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        parser().parse_args(["epub", "-h"])
    shown = capsys.readouterr().out
    assert "--llm-estimate" in shown and "--gemini" not in shown
