import pytest

from app.query import clean, expand_aliases, find_landmark, ordinal_indexes, parse_minutes, parse_rules


@pytest.mark.parametrize("text, minutes", [
    ("хочу прогуляться 30–60 минут", 60),
    ("что-то на 1–2 часа", 120),
    ("у меня 2 часа", 120),
    ("полтора часа свободно", 90),
    ("есть полчаса", 30),
    ("последние пару часов", 120),
    ("погулять на час", 60),
    ("минут 45 есть, 45 мин", 45),
    ("есть минут 40, хочется красивого", 40),  # число после единицы
    ("свободно часа 2", 120),
    ("часть 2 маршрута", None),
    ("встреча в 10 часов", None),  # время суток, а не длительность
    ("просто погулять", None),
])
def test_parse_minutes(text, minutes):
    assert parse_minutes(text) == minutes


# Примерные сценарии из задания
def test_task_scenario_mega_walk():
    p = clean(parse_rules("Я сейчас возле Mega Silk Way, хочу прогуляться 30–60 минут — что посоветуете?"))
    assert p["landmark"] == "mega" and p["time_budget_min"] == 60 and not p["wants"]


def test_task_scenario_coffee_socket():
    p = clean(parse_rules("Хочу тихое место с кофе и розеткой недалеко от меня."))
    assert {"coffee", "quiet", "socket"} <= set(p["wants"])


def test_task_scenario_kids_without_false_sport():
    p = clean(parse_rules("Еду с ребёнком — где рядом есть детские площадки или музей с интерактивами?"))
    assert p["with_kids"] is True
    assert p["interests"] == ["Культура"]  # «интерактивами» — не спорт


def test_task_scenario_flight():
    p = clean(parse_rules("Я — турист, у меня 2 часа перед вылетом: что можно посмотреть поблизости?"))
    assert p["before_flight"] is True and p["time_budget_min"] == 120 and p["landmark"] is None


def test_task_scenario_night_without_false_sport():
    p = clean(parse_rules("Ищу необычную ночную активность (последние пару часов)."))
    assert set(p["wants"]) == {"night", "unusual"} and p["interests"] == [] and p["time_budget_min"] == 120


def test_task_example_expo():
    p = clean(parse_rules("Я у здания Экспо 2017, хочу что-то необычное на 1–2 часа, пешком."))
    assert p["landmark"] == "expo" and p["time_budget_min"] == 120 and p["wants"] == ["unusual"]


@pytest.mark.parametrize("text, landmark", [
    ("хочу дойти до Байтерека и выпить кофе", None),        # цель пути, а не где я
    ("как пройти к Хан Шатыру", None),
    ("я у Экспо, хочу дойти до Байтерека", "expo"),
    ("сижу в Меге, есть час", "mega"),
    ("Астана — мегаполис, что посмотреть?", None),
    ("я на вокзале Астана-1, есть час", "old_station"),     # старый вокзал — не Нурлы Жол
    ("я на старом вокзале, хочу пройти всю Астану", "old_station"),
    ("я на вокзале, есть час", "station"),
    ("стою у здания Экспо", "expo"),
])
def test_landmark_is_where_user_is(text, landmark):
    assert parse_rules(text)["landmark"] == landmark


def test_evening_walk_in_park_is_not_nightlife():
    p = clean(parse_rules("вечером погулять в парке"))
    assert "night" not in p["wants"] and p["interests"] == ["Парки"]


def test_clean_rejects_garbage_from_llm():
    p = clean({"landmark": "кремль", "time_budget_min": 5, "with_kids": "yes", "before_flight": 1,
               "interests": ["Парки", "Казино"], "wants": ["coffee", "drugs"], "at": "25:00", "place": 42,
               "more": "да", "closer": 1, "destination": ["Байтерек"]})
    assert p == {"landmark": None, "place": None, "destination": None, "time_budget_min": None, "at": None,
                 "with_kids": None,
                 "before_flight": None, "interests": ["Парки"], "wants": ["coffee"],
                 "more": False, "closer": False, "next": False, "reset": False, "thanks": False, "one": False}


@pytest.mark.parametrize("text, at", [
    ("погулять в 23:00", "23:00"),
    ("что открыто в 9 вечера", "21:00"),
    ("в 10 утра", "10:00"),
    ("в 12 ночи", "00:00"),
    ("на 2 часа", None),
])
def test_parse_at(text, at):
    assert parse_rules(text)["at"] == at


@pytest.mark.parametrize("text, key", [
    ("ещё варианты", "more"), ("покажи другие", "more"), ("а поближе?", "closer"),
    ("давай заново", "reset"), ("спасибо!", "thanks"),
])
def test_dialog_follow_ups(text, key):
    assert parse_rules(text)[key] is True


def test_still_have_an_hour_is_time_not_more():
    p = parse_rules("у меня есть ещё час")
    assert p["more"] is False and p["time_budget_min"] == 60
    assert parse_rules("ещё")["more"] is True


def test_hour_and_a_half_any_word_order():
    assert parse_minutes("хочу погулять часа полтора") == 90


def test_clean_rejects_bool_as_minutes():
    assert clean({"time_budget_min": True})["time_budget_min"] is None


@pytest.mark.parametrize("text, start, dest", [
    ("доведи до Байтерека по красивым местам", None, "baiterek"),
    ("я у Экспо, хочу дойти до Хан Шатыра", "expo", "khan_shatyr"),
    ("я у Байтерека", "baiterek", None),
    ("иду мимо Байтерека", None, None),   # по пути — не старт и не цель
    ("с Экспо до Хан Шатыра через Байтерек", "expo", "khan_shatyr"),
])
def test_start_and_destination_are_not_confused(text, start, dest):
    p = parse_rules(text)
    assert (p["landmark"], p["destination"]) == (start, dest)


def test_beautiful_places_are_sights_and_parks():
    assert set(parse_rules("по красивым местам")["interests"]) == {"Парки", "Достопримечательности"}


@pytest.mark.parametrize("text, tour", [
    ("хочу пройти всю Астану пешком", True),
    ("покажи все достопримечательности", True),
    ("обзорная экскурсия по городу", True),
    ("главные места города за 3 часа", True),
    ("погулять в парке час", False),
])
def test_tour_request(text, tour):
    assert ("tour" in parse_rules(text)["wants"]) == tour


@pytest.mark.parametrize("text, nxt", [("дальше", True), ("а потом куда?", True), ("продолжим", True),
                                       ("ещё варианты", False), ("хочу подальше от шума", False)])
def test_continue_request(text, nxt):
    assert parse_rules(text)["next"] == nxt


# ---------- прогон 40 типичных фраз: что понималось неправильно ----------

@pytest.mark.parametrize("text, group", [
    ("хочу посмотреть на город сверху", "Достопримечательности"),
    ("холодно, хочу куда-то в помещение", "ТРЦ и магазины"),
    ("погулять с собакой", "Парки"),
])
def test_everyday_wording_maps_to_groups(text, group):
    assert group in parse_rules(text)["interests"]


def test_all_the_main_things_is_a_tour():
    assert "tour" in parse_rules("есть 4 часа, хочу посмотреть всё главное")["wants"]


@pytest.mark.parametrize("text, dest", [
    ("я на старом вокзале, хочу в центр погулять", "baiterek"),   # центр города
    ("я в торговом центре", None),                              # не центр города
    ("хочу в центр развлечений", None),
])
def test_center_of_the_city_is_a_destination(text, dest):
    assert parse_rules(text)["destination"] == dest


def test_via_landmark_is_neither_start_nor_destination():
    assert find_landmark("с Экспо до Хан Шатыра через Байтерек", via=True) == "baiterek"
    assert find_landmark("я у Байтерека", via=True) is None


def test_short_names_are_expanded():
    # вживую: «хочу дойти с адп до ханша через байтерек»
    assert expand_aliases("с адп до ханша") == "с Абу-Даби Плаза до Хан Шатыр"
    assert expand_aliases("ханшайым, адпх") == "ханшайым, адпх"  # только целые слова


@pytest.mark.parametrize("text, indexes", [("второе", [1]), ("первого и третьего", [0, 2]), ("2", [1]), ("№ 3", [2]),
                                           ("2 часа", []), ("первоклассного", [])])
def test_ordinal_indexes(text, indexes):
    assert ordinal_indexes(text) == indexes


def test_not_wanted_place_is_not_a_destination():
    assert find_landmark("не хочу в Хан Шатыр", destination=True) is None
    assert find_landmark("хочу в Хан Шатыр", destination=True) == "khan_shatyr"


@pytest.mark.parametrize("text, expanded", [
    ("через арку", "через Триумфальная арка"),            # вживую: «через арку» молча пропускалось
    ("через Триумфальную арку", "через Триумфальная арка"),
    ("я в Сарыарке у Аркадия", "я в Сарыарке у Аркадия"),  # не арка
])
def test_arch_is_the_triumphal_arch(text, expanded):
    assert expand_aliases(text) == expanded


@pytest.mark.parametrize("text, one", [
    ("укажи одно заведение", True), ("выбери одно", True), ("какое из них лучше?", True), ("только одно", True),
    ("я одна, хочу погулять", False), ("есть один час", False),
])
def test_one_place_request(text, one):
    assert parse_rules(text)["one"] == one
