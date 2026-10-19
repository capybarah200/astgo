import pytest
import requests

from app import llm
from app.llm import Gemini, LLMError, apply_explanations


def item(id_, walk=7, visit=(30, 60), name="Парк"):
    return {"id": id_, "name": name, "category_label": "парк", "walk_min": walk, "visit_min": list(visit),
            "open": {"state": "outdoor", "text": "открытая территория"}, "reasons": [], "description": None,
            "why": "шаблон", "todo": "шаблон", "text_source": "template"}


def test_valid_text_applied():
    items = [item("a")]
    n = apply_explanations(items, {"items": [{"id": "a", "why": "Тихий парк в 7 мин.", "todo": "Погуляйте 30 мин."}]})
    assert n == 1 and items[0]["text_source"] == "gemini" and items[0]["why"] == "Тихий парк в 7 мин."


@pytest.mark.parametrize("entry", [
    {"id": "zzz", "why": "Чужое место", "todo": "—"},                    # нет в выдаче
    {"id": "a", "why": "Парк в 12 мин пешком.", "todo": "Погуляйте."},  # 12 — нет в фактах
    {"id": "a", "why": "Билет 2000 тенге.", "todo": "Купите билет."},    # цена придумана
    {"id": "a", "why": "", "todo": "Погуляйте."},
    {"id": "a", "why": "х" * 500, "todo": "Погуляйте."},
    "не объект",
])
def test_bad_text_rejected_template_kept(entry):
    items = [item("a")]
    assert apply_explanations(items, {"items": [entry]}) == 0
    assert items[0]["text_source"] == "template" and items[0]["why"] == "шаблон"


def test_numbers_of_next_place_allowed_only_in_route():
    items = [item("a"), item("b", walk=9, name="Музей")]
    answer = {"items": [{"id": "a", "why": "Парк.", "todo": "Потом музей в 9 мин."}]}
    assert apply_explanations([dict(i) for i in items], answer, is_route=True) == 1
    assert apply_explanations([dict(i) for i in items], answer, is_route=False) == 0


def test_garbage_answer_shape():
    assert apply_explanations([item("a")], ["не словарь"]) == 0
    assert apply_explanations([item("a")], {"items": None}) == 0


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self):
        return self._data


def test_gemini_key_in_header_not_url(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(url=url, headers=headers, body=json)
        return _Resp({"candidates": [{"content": {"parts": [{"text": '{"ok": '}, {"text": "true}"}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    out = Gemini("SECRET", "gemini-test").generate_json("sys", "prompt", {"type": "OBJECT"})
    assert out == {"ok": True}
    assert "SECRET" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "SECRET"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_gemini_thinking_can_be_omitted(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm.requests, "post", lambda url, json, **k: seen.update(body=json) or _Resp(
        {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}))
    Gemini("k", "m", thinking="").generate_json("s", "p", {})
    assert "thinkingConfig" not in seen["body"]["generationConfig"]


def test_gemini_error_names_the_quota(monkeypatch):
    message = ("You exceeded your current quota, please check your plan.\n* Quota exceeded for metric: "
               "generate_content_free_tier_requests, limit: 20, model: m")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp({"error": {"message": message}}, 429))
    with pytest.raises(LLMError, match=r"429 Client Error — Quota exceeded for metric: .*limit: 20"):
        Gemini("k", "m").generate_json("s", "p", {})


def _quota():
    return _Resp({"error": {"message": "Quota exceeded for metric: x, limit: 20. Please retry in 10h48m37.3s."}}, 429)


def _ok():
    return _Resp({"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})


def _models_answering(monkeypatch, responses):
    """responses: модель → функция, возвращающая ответ. → список моделей, к которым обращались."""
    asked = []

    def fake_post(url, json, timeout, headers):
        model = url.split("/models/")[1].split(":")[0]
        asked.append(model)
        return responses[model]()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return asked


def test_next_model_when_quota_exceeded_and_first_paused(monkeypatch):
    asked = _models_answering(monkeypatch, {"a": _quota, "b": _ok})
    g = Gemini("k", "a, b")
    assert g.generate_json("s", "p", {}) == {"ok": True}
    assert g.generate_json("s", "p", {}) == {"ok": True}
    assert asked == ["a", "b", "b"]  # «a» ждёт срок, названный Google, и не дёргается каждый запрос


def test_all_models_out_of_quota(monkeypatch):
    asked = _models_answering(monkeypatch, {"a": _quota})
    g = Gemini("k", "a")
    for _ in range(2):
        with pytest.raises(LLMError):
            g.generate_json("s", "p", {})
    assert asked == ["a"]


@pytest.mark.parametrize("status", [500, 400])
def test_other_errors_do_not_try_next_model(monkeypatch, status):
    # таймаут или 500 — человек и так ждал; вторую модель не спрашиваем
    asked = _models_answering(monkeypatch, {"a": lambda: _Resp({}, status), "b": _ok})
    with pytest.raises(LLMError):
        Gemini("k", "a,b").generate_json("s", "p", {})
    assert asked == ["a"]


@pytest.mark.parametrize("text, seconds", [
    ("Please retry in 10h48m37.5s.", 38917.5),
    ("Please retry in 41.2s.", 41.2),
    ("Please retry in 500ms.", llm.QUOTA_PAUSE_S),  # меньше секунды — не разбираем, берём минуту
    ("Quota exceeded", llm.QUOTA_PAUSE_S),
])
def test_retry_after(text, seconds):
    assert llm.retry_after(text) == pytest.approx(seconds)


@pytest.mark.parametrize("resp", [_Resp({}, 500), _Resp({"candidates": []}), _Resp({"candidates": [
    {"content": {"parts": [{"text": "не json"}]}}]})])
def test_gemini_errors_become_llm_error(monkeypatch, resp):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: resp)
    with pytest.raises(LLMError):
        Gemini("k", "m").generate_json("s", "p", {})


def test_destination_minutes_allowed_after_last_place():
    final = {"name": "Байтерек", "walk_min": 13}
    answer = {"items": [{"id": "a", "why": "Парк по пути.", "todo": "Погуляйте; до Байтерека 13 мин."}]}
    assert llm.facts_for_llm([item("a")], True, final)[0]["next"] == {**final, "is_destination": True}
    assert apply_explanations([item("a")], answer, True, final) == 1
    assert apply_explanations([item("a")], answer, True) == 0  # 13 без цели — выдумка


def test_guide_text_reaches_model_and_its_numbers_are_allowed():
    it = {**item("a"), "guide": {"text": "Pyramid 62 m high, by Norman Foster.", "url": "u"}}
    assert "62 m" in llm.facts_for_llm([it])[0]["guide"]
    answer = {"items": [{"id": "a", "why": "Пирамида высотой 62 м по проекту Нормана Фостера.", "todo": "Осмотрите."}]}
    assert apply_explanations([it], answer) == 1


def _hang():
    raise requests.Timeout("read timeout")


@pytest.mark.parametrize("failing", [lambda: _Resp({"error": {"message": "high demand"}}, 503), _hang])
def test_busy_or_hanging_model_hands_over_to_the_next(monkeypatch, failing):
    # вживую 3.8-flash зависала по 12 с, 3.6-flash отвечала 503 «высокая нагрузка» — и ответ шёл шаблоном
    asked = _models_answering(monkeypatch, {"a": failing, "b": _ok})
    g = Gemini("k", "a,b")
    assert g.generate_json("s", "p", {}) == {"ok": True} and g.generate_json("s", "p", {}) == {"ok": True}
    assert asked == ["a", "b", "b"]  # «a» на паузе, второй запрос сразу к «b»


def test_invented_sockets_are_rejected():
    answer = {"intro": "Кафе с розетками рядом.",
              "items": [{"id": "a", "why": "Уютное кафе с доступными розетками.", "todo": "Поработайте."}]}
    assert apply_explanations([item("a")], answer) == 0
    assert llm.checked_intro(answer, set()) is None


@pytest.mark.parametrize("budget, timeouts", [(5, [5]), (10, [8, 2])])
def test_silent_models_do_not_exceed_the_time_budget(monkeypatch, budget, timeouts):
    # вечером 19.10 Google был перегружен: модели молчали по 8 с каждая, и первый ответ шёл 10–25 с
    now, asked = [0.0], []
    monkeypatch.setattr(llm, "monotonic", lambda: now[0])

    def silent(url, json, timeout, headers):
        asked.append(timeout)
        now[0] += timeout  # модель молчит всё отведённое ей время
        raise requests.Timeout("read timeout")

    monkeypatch.setattr(llm.requests, "post", silent)
    with pytest.raises(LLMError, match=f"за {budget} с не ответила ни одна модель"):
        Gemini("k", "a,b,c", timeout=8).generate_json("s", "p", {}, budget_s=budget)
    assert asked == timeouts  # третью модель уже не ждём
