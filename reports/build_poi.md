# Сборка мест: склейка дублей и Wikidata

Склеено групп: **10** (убрано точек: 10). Сопоставлено с Wikidata: **25** (по тегу OSM — 20, по названию — 5).

## Заполненность

| Поле | До (poi_osm.json) | После (poi.json) |
|---|---|---|
| Точек всего | 1852 | 1872 |
| С названием | 1081 | 1101 |
| name_ru (среди мест с названием) | 177 | 207 |
| name_en (среди мест с названием) | 127 | 149 |
| name_kk (среди мест с названием) | 76 | 96 |
| description (среди мест с названием) | 2 | 46 |
| wikidata (среди мест с названием) | 21 | 52 |

## Склеено (10)

Правило: один Q в радиусе 300 м или похожее название в радиусе 50 м при той же категории (кафе / фастфуд / ресторан — одна). Первая точка в строке — основа.

- **Рахмет ТЦ** `osm:way/241090093` shop=mall ← `osm:node/4900149827` Рахмет (shop=mall, 16 м)
- **Mega Silk Way** `osm:way/499328514` shop=mall ← `osm:node/4965336822` Мега Силк Вей (shop=mall, 17 м)
- **Бейбітшілік және келісім сарайы** `osm:way/166197368` amenity=community_centre ← `osm:node/5130027840` Бейбітшілік және келісім сарайы (amenity=community_centre, 2 м)
- **Coffee Like** `osm:node/6494912583` amenity=cafe ← `osm:node/7569174946` Coffee Like (amenity=cafe, 36 м)
- **ШашлычОк** `osm:node/12049030096` amenity=fast_food ← `osm:way/1430321978` Шашлычок (amenity=cafe, 41 м)
- **ШашлычОк** `osm:node/12049030099` amenity=fast_food ← `osm:node/12049030108` ШашлычОк (amenity=fast_food, 22 м)
- **Durum** `osm:node/12896623405` amenity=cafe ← `osm:node/12917084210` Durum (amenity=fast_food, 23 м)
- **Coffee Stories** `osm:node/12973394495` amenity=cafe ← `osm:node/12990081601` Coffee Stories (amenity=cafe, 7 м)
- **Aizere2  Кафе** `osm:way/1499433763` amenity=cafe ← `osm:node/13779471493` Aizere2 (amenity=cafe, 1 м)
- **Ататүрік саябағы** `osm:way/1460702136` leisure=park ← `osm:way/502046662` «Ататүрік» саябағы (leisure=park, 18 м)

## Проверить глазами: похожее название, другая категория, до 50 м (3)

Не склеены автоматически.

- `osm:node/1198117093` Mega (amenity=bar) — `osm:node/1198118007` Mega (amenity=cafe), 14 м
- `osm:node/5586173121` Kinopark (amenity=cinema) — `osm:way/240909210` Сарыарқа (shop=mall), 20 м
- `osm:node/10168959440` Ailand (tourism=aquarium) — `osm:node/10168968309` Ailand (leisure=water_park), 14 м

## Сопоставлено с Wikidata (25)

| Место | id | Q | Подпись Wikidata | Как | Метры | Описание |
|---|---|---|---|---|---|---|
| Жерұйық саябағы | `osm:relation/16002498` | Q120868819 | Парк Жеруйык | имя | 1 | wikipedia |
| Сызықтық саябақ | `osm:way/734514013` | Q120868822 | Линейный парк | имя | 0 | — |
| Сәдуақас қажы Ғылмани мешіті | `osm:way/240376673` | Q25502203 | Садуакас кажы Гылмани (мечеть) | имя | 12 | wikipedia |
| Ғашықтар саябағы | `osm:way/37884583` | Q42422250 | Парк Влюбленных (Астана) | имя | 24 | wikipedia |
| Қазақ елі | `osm:node/2681623412` | Q12572863 | Казак ели (монумент) | имя | 7 | wikipedia |
| Abu Dhabi Plaza | `osm:way/762780007` | Q1073433 | Абу-Даби Плаза | тег | 30 | wikipedia |
| Astana Musical | `osm:way/1460537088` | Q573362 | Центральный концертный зал «Казахстан» | тег | 7 | wikipedia |
| Sydykbek Arena | `osm:way/1495919050` | Q135106379 |  | тег | 24 | — |
| Алау мұзайдыны сарайы | `osm:way/166267459` | Q1072092 | Алау | тег | 14 | wikipedia |
| Астана Арена | `osm:way/1460088073` | Q746559 | Астана Арена | тег | 13 | wikipedia |
| Астана Опера | `osm:way/917478479` | Q21371563 | Астана опера | тег | 16 | wikipedia |
| Атамекен Қазақстан картасы этно-мемориалдық кешені | `osm:way/1460700597` | Q1454511 | Atameken miniature park | тег | 19 | — |
| Барыс Арена | `osm:way/663143306` | Q16632800 | Барыс Арена | тег | 14 | wikipedia |
| Бейбітшілік және келісім сарайы | `osm:way/166197368` | Q2119741 | Дворец мира и согласия | тег | 13 | wikipedia |
| Бейт Рахель Хабад Любавич | `osm:node/4902164954` | Q42416691 | Синагога Бейт Рахель Хабад Любавич | тег | 5 | wikipedia |
| Ботаникалық бақ | `osm:way/1196402246` | Q104866948 | Астанинский ботанический сад | тег | 44 | wikipedia |
| Бәйтерек | `osm:way/230401645` | Q804210 | Байтерек | тег | 9 | wikipedia |
| Жетісу саябағы | `osm:way/69232075` | Q104866925 | Жетысу | тег | 21 | wikipedia |
| Мәңгілік Ел салтанат қақпасы | `osm:way/549307755` | Q42422896 | Триумфальная арка Мангилик Ел | тег | 0 | wikipedia |
| Сарыарқа | `osm:way/166265821` | Q4408858 | Сарыарка (велотрек) | тег | 10 | wikipedia |
| Тәуелсіздік Сарайы | `osm:way/240873345` | Q4155890 | Дворец Независимости (Нур-Султан) | тег | 15 | wikipedia |
| Хан Шатыр | `osm:way/460703779` | Q671484 | Хан Шатыр | тег | 32 | wikipedia |
| Қазақстан Республикасының Ұлттық Мұражайы | `osm:node/4894937300` | Q18405670 | Национальный музей Республики Казахстан | тег | 69 | wikipedia |
| Әбу Насыр әл-Фараби мешіті | `osm:relation/16827715` | Q1813417 | Мечеть Нур Астана | тег | 17 | wikipedia |
| Әзірет Сұлтан мешіті | `osm:way/240860325` | Q4494668 | Хазрет Султан | тег | 47 | wikipedia |

## Добавлено из Wikidata (23)

Объекты, которых нет в выборке OSM: по типу (P31) из белого списка и только со статьёй в Википедии.

- `wikidata:Q413579` Ак орда — tourism=attraction · фото
- `wikidata:Q1186465` Казахстан — tourism=attraction · фото
- `wikidata:Q2986461` Стадион им. Хаджимукана Мунайтпасова — leisure=stadium · фото
- `wikidata:Q4230441` концертный зал "Астана" — amenity=music_venue · фото
- `wikidata:Q25481641` Монумент защитникам Отечества — historic=monument · фото
- `wikidata:Q25495527` Talan Towers — tourism=attraction
- `wikidata:Q28402868` Ramstore Bridge — tourism=attraction · фото
- `wikidata:Q32193241` Майбалык — leisure=nature_reserve · фото
- `wikidata:Q32205515` Большой Талдыколь — leisure=nature_reserve · фото
- `wikidata:Q97163750` Жилой дом доктора Ф. И. Благовещенского — tourism=attraction · фото
- `wikidata:Q97165482` Больница купца Степана Кубрина — tourism=attraction · фото
- `wikidata:Q97165767` Дом купца Василия Кубрина — tourism=attraction · фото
- `wikidata:Q97165888` Здание бывшей мусульманской школы — tourism=attraction · фото
- `wikidata:Q97165900` Здание городской управы — tourism=attraction · фото
- `wikidata:Q97166399` Ограждение Зелёной мечети — tourism=attraction · фото
- `wikidata:Q97167320` Школа Косшыгулова — tourism=attraction · фото
- `wikidata:Q97212648` Дом купца Силина — tourism=attraction · фото
- `wikidata:Q97274797` Дом купца Д. В. Егорова — tourism=attraction · фото
- `wikidata:Q97274875` Здание метеостанции — tourism=attraction · фото
- `wikidata:Q97496724` Здание кинотеатра «Октябрь» — amenity=cinema · фото
- `wikidata:Q104866684` Президентский парк — leisure=park · фото
- `wikidata:Q108533991` Талдыколь — leisure=nature_reserve · фото
- `wikidata:Q109715788` Малый Талдыколь — leisure=nature_reserve · фото

Фото с Commons: у 38 мест; описание из Википедии: у 45.

## Путеводитель Wikivoyage

Описание прикреплено к 20 местам; новых мест из раздела «See»: 7.

- `wikivoyage:Q1417227` KazMunayGas Palace
- `wikivoyage:Q475139` The Triumph of Astana
- `wikivoyage:Q2995409` Қазақстан Республикасы Парламентінің Сенаты
- `wikivoyage:Q1073439` Emerald towers
- `wikivoyage:buildingofministers` Министрліктер үйі
- `wikivoyage:giantlizardssculptures` Giant lizards sculptures
- `wikivoyage:grandmosqueofastana` Grand Mosque of Astana
