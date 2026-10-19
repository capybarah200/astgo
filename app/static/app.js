"use strict";

// Данные из OSM и Википедии выводим только через textContent — никакого innerHTML.
// Контекст разговора (state) хранится только здесь, в браузере; сервер его не записывает.
const $ = (sel) => document.querySelector(sel);
const params = new URLSearchParams(location.search);
const DEMO_AT = /^\d{1,2}:\d{2}$/.test(params.get("at") || "") ? params.get("at") : null;
// seed — случайное число этого разговора: одинаковые запросы в разных разговорах дают не одну и ту же двадцатку мест
let state = { seed: 1 + Math.floor(Math.random() * 2147483646) };
let busy = false;

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Ссылки из OSM правит кто угодно: «javascript:…» в href выполнится по клику — пропускаем только http(s)
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
const round3 = (x) => Math.round(x * 1000) / 1000;  // ~100 м: точные координаты не покидают телефон

// ---------- карта ----------
// Тайлы OpenStreetMap без ключей; спокойный серый вид — фильтром CSS (style.css, .leaflet-tile-pane)
const map = L.map("map", { zoomControl: false }).setView([51.128, 71.43], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">участники OpenStreetMap</a>',
}).addTo(map);
const layer = L.layerGroup().addTo(map);
const markers = new Map();
const ACCENT = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#1f5aa6";

function pin(label, cls = "") {
  return L.divIcon({ className: "", html: `<div class="pin ${cls}">${label}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });
}

let drawnKey = null;

function drawMap(res) {
  if (!res.start) return;
  const dest = res.destination ? [res.destination.lat, res.destination.lon] : null;
  const key = `${res.start.lat},${res.start.lon}|${dest}`;
  if (!res.items.length && key === drawnKey) return;  // «спасибо», «не понял»: маршрут остаётся на карте
  drawnKey = key;
  layer.clearLayers();
  markers.clear();
  const start = [res.start.lat, res.start.lon];
  L.marker(start, { icon: pin("", "start"), title: res.start.label, keyboard: false }).addTo(layer);
  const points = [start];
  res.items.forEach((item, i) => {
    const p = [item.lat, item.lon];
    const m = L.marker(p, { icon: pin(String(i + 1)), title: item.name }).addTo(layer);
    m.bindPopup(() => h("strong", {}, item.name));
    markers.set(item.id, m);
    points.push(p);
  });
  if (dest) {
    L.marker(dest, { icon: pin("★", "dest"), title: res.destination.label, keyboard: false })
      .bindPopup(() => h("strong", {}, res.destination.label)).addTo(layer);
  }
  // Маршрут: к цели — до цели, «до вылета» — обратно к старту, иначе — до последнего места.
  // Варианты на выбор («кофе по пути») — не маршрут: их не соединяем, к цели — только прямая от старта
  const isRoute = res.route && res.route.mode === "route" && res.items.length;
  let line = null;
  if (isRoute) line = dest ? [...points, dest] : res.route.return_min ? [...points, start] : points;
  else if (dest) line = [start, dest];
  if (line) {
    // interactive: false — касание линии не считается касанием карты («я здесь»)
    L.polyline(line, { color: ACCENT, weight: 3, dashArray: "2 8", lineCap: "round", opacity: 0.9, interactive: false })
      .addTo(layer);
  }
  const all = dest ? [...points, dest] : points;
  if (all.length > 1) map.fitBounds(L.latLngBounds(all).pad(0.3), { maxZoom: 16 });
  else map.setView(start, 15);
}

// Касание карты = «я здесь», но не двойное касание для зума: ждём, не придёт ли dblclick
let tapTimer = null;
map.on("click", (e) => {
  clearTimeout(tapTimer);
  tapTimer = setTimeout(() => send("", { lat: round3(e.latlng.lat), lon: round3(e.latlng.lng), source: "map" }), 300);
});
map.on("dblclick", () => clearTimeout(tapTimer));

// ---------- разговор ----------
const log = $("#log");

function scrollDown() {
  log.scrollTop = log.scrollHeight;
}

function addUser(text, isPoint = false) {
  log.append(h("div", { class: `msg-user${isPoint ? " point" : ""}` }, text));
  scrollDown();
}

const SOURCE_LABELS = { osm: "OpenStreetMap", wikidata: "Wikidata", wikipedia: "Википедия", commons: "Wikimedia Commons",
  wikivoyage: "путеводитель Wikivoyage" };

// Фото с Commons: у каждого своя лицензия — автор и лицензия под фото обязательны
function photo(item) {
  const img = item.image;
  const src = img && safeUrl(img.thumb);
  if (!src) return null;
  const page = safeUrl(img.page);
  const credit = [img.artist, img.license].filter(Boolean).join(", ") || "автор не указан";
  return h("figure", { class: "photo" },
    h("img", { src, alt: item.name, loading: "lazy", referrerpolicy: "no-referrer" }),
    h("figcaption", {}, `Фото: ${credit} — `,
      page ? h("a", { href: page, target: "_blank", rel: "noopener" }, "Wikimedia Commons") : "Wikimedia Commons"));
}

function place(item, i, isRoute) {
  const meta = [
    h("span", {}, `${item.walk_min} мин пешком${isRoute && i > 0 ? " от предыдущего" : ""}`),
    " · ",
    h("span", {}, `${item.visit_min[0]}–${item.visit_min[1]} мин на месте`),
    " · ",
    h("span", { class: item.open.state === "open" ? "open" : null }, item.open.text),
  ];
  if (item.warning) meta.push(" · ", h("span", { class: "warn" }, item.warning));
  if (item.chain) meta.push(" · сетевое");

  let desc = null;
  if (item.description) {
    const src = { wikipedia: "Википедия", osm: "OpenStreetMap", wikidata: "Wikidata" }[item.description_source] || "данные";
    const url = safeUrl(item.wikipedia);
    desc = h("p", { class: "desc" }, item.description, " — ",
      url ? h("a", { href: url, target: "_blank", rel: "noopener" }, src) : src);
  }
  const osm = item.id.replace(/^osm:/, "");
  const site = safeUrl(item.website);
  const textBy = item.text_source === "gemini" ? "Gemini по этим данным" : "шаблон по этим данным";
  return h("li", { class: "place" },
    h("span", { class: "num", "aria-hidden": "true" }, String(i + 1)),
    h("div", {},
      h("h3", {}, h("button", { type: "button", onclick: () => focusPlace(item) }, item.name),
        h("span", { class: "kind" }, ` · ${item.category_label}`)),
      photo(item),
      h("div", { class: "meta" }, meta),
      h("p", {}, item.why),
      h("p", {}, h("span", { class: "label" }, "Что делать: "), item.todo),
      desc,
      h("div", { class: "links" },
        h("a", { href: `https://www.google.com/maps/dir/?api=1&destination=${item.lat},${item.lon}&travelmode=walking`,
          target: "_blank", rel: "noopener" }, "Маршрут пешком"),
        site ? h("a", { href: site, target: "_blank", rel: "noopener nofollow" }, "Сайт") : null,
        osm.includes("/") ? h("a", { href: `https://www.openstreetmap.org/${osm}`, target: "_blank", rel: "noopener" }, "OSM") : null),
      h("div", { class: "src" },
        `Факты: ${item.sources.map((s) => SOURCE_LABELS[s] || s).join(", ")} · текст: ${textBy}`)));
}

function focusPlace(item) {
  const m = markers.get(item.id);
  if (!m) return;
  map.setView(m.getLatLng(), Math.max(map.getZoom(), 16));
  m.openPopup();
  if (window.matchMedia("(max-width: 899px)").matches) $("#map").scrollIntoView({ behavior: "smooth" });
}

function dataUsed(du, understood) {
  if (!du) return null;
  const texts = { gemini: "Gemini", template: "шаблон", mixed: "Gemini и шаблон" }[du.texts];
  const parser = understood && understood.parser === "gemini" ? "Gemini" : "правила";
  return h("details", { class: "data" }, h("summary", {}, "Какие данные использованы"),
    h("dl", {},
      h("dt", {}, "Точка"), h("dd", {}, du.location),
      du.destination ? [h("dt", {}, "Куда"), h("dd", {}, du.destination)] : null,
      h("dt", {}, "Время"), h("dd", {}, du.time),
      h("dt", {}, "Учли"), h("dd", {}, du.filters.join(", ")),
      understood && understood.parsed.length ? [h("dt", {}, "Поняли"), h("dd", {}, `${understood.parsed.join(", ")} (${parser})`)] : null,
      h("dt", {}, "Текст"), h("dd", {}, `${texts}; факты — только из базы`),
      h("dt", {}, "Источники"), h("dd", {}, du.sources.join(", ")),
      h("dt", {}, "Хранение"), h("dd", {}, du.stored)));
}

// Геолокацию в Google не передаём: без origin он сам возьмёт «моё местоположение» — на телефоне это GPS.
// Точку, отмеченную на карте, передаём (она округлена до ~100 м): иначе на компьютере Google брал место
// по IP и строил маршрут от окраины города
const PERSONAL_STARTS = new Set(["вы здесь (±100 м)"]);

function fullRouteLink(res) {
  if (!res.route || res.route.mode !== "route" || !res.items.length) return null;
  const stops = res.items.map((i) => `${i.lat},${i.lon}`);
  const start = `${res.start.lat},${res.start.lon}`;
  const personal = PERSONAL_STARTS.has(res.start.label);
  let dest;
  if (res.destination) dest = `${res.destination.lat},${res.destination.lon}`;
  else if (res.route.return_min && !personal) dest = start;  // «до вылета» — обратно к старту
  else dest = stops.pop();
  const params = new URLSearchParams({ api: "1", destination: dest, travelmode: "walking" });
  if (!personal) params.set("origin", start);
  if (stops.length) params.set("waypoints", stops.join("|"));
  return h("p", { class: "full-route" },
    h("a", { href: `https://www.google.com/maps/dir/?${params}`, target: "_blank", rel: "noopener" },
      "Весь маршрут в Google Maps"));
}

function suggestions(res) {
  if (!res.items.length && !res.route) return null;
  const s = res.state || {};
  const options = [["Ещё варианты", "ещё варианты"], ["Ближе", "ближе"]];
  if (res.route && res.route.mode === "route" && res.items.length) options.unshift(["Дальше", "дальше"]);
  options.push(s.with_kids ? ["Начать заново", "заново"] : ["А с ребёнком?", "с ребёнком"]);
  return h("div", { class: "suggest" }, options.map(([label, text]) =>
    h("button", { type: "button", onclick: () => send(text) }, label)));
}

function renderBot(res) {
  const isRoute = res.route && res.route.mode === "route";
  return h("div", { class: "msg-bot" },
    h("p", { class: "reply" }, res.reply),
    res.items.length ? h("ol", { class: "places" }, res.items.map((item, i) => place(item, i, isRoute))) : null,
    fullRouteLink(res),
    res.notes.length ? h("ul", { class: "notes" }, res.notes.map((n) => h("li", {}, n))) : null,
    dataUsed(res.data_used, res.understood),
    suggestions(res));
}

let queued = null;

async function send(text, point = null) {
  text = (text || "").trim();
  if (!text && !point) return;
  if (busy) {
    queued = { text, point };  // не теряем: выполним сразу после текущего ответа (последнее действие)
    return;
  }
  busy = true;
  $("#welcome")?.remove();
  log.querySelectorAll(".suggest").forEach((el) => el.remove());  // подсказки — только у последнего ответа
  if (text) addUser(text);
  else addUser(point.source === "geo" ? "Я здесь" : "Я здесь — отметил на карте", true);
  const pending = h("p", { class: "pending" }, "Подбираю…");
  log.append(pending);
  scrollDown();
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, state, at: DEMO_AT, ...(point || {}) }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(typeof data.detail === "string" ? data.detail : "не получилось, попробуйте ещё раз");
    state = data.state;
    const bot = renderBot(data);
    pending.replaceWith(bot);
    drawMap(data);
    log.scrollTop = bot.offsetTop - 8;  // начало ответа, а не его хвост
  } catch (err) {
    pending.replaceWith(h("p", { class: "error", role: "alert" }, `Не получилось: ${err.message}`));
    scrollDown();
  } finally {
    busy = false;
    if (queued) {
      const next = queued;
      queued = null;
      send(next.text, next.point);
    }
  }
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#input");
  send(input.value);
  input.value = "";
});
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation) {
    log.append(h("p", { class: "error" }, "Браузер не даёт геолокацию — напишите, где вы, или нажмите на карту."));
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => send("", { lat: round3(pos.coords.latitude), lon: round3(pos.coords.longitude), source: "geo" }),
    () => log.append(h("p", { class: "error" }, "Геолокация недоступна (нужен https или разрешение). Напишите, где вы, или нажмите на карту.")),
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
});

if (DEMO_AT) {
  const note = $("#demo-time");
  note.textContent = `считаем на ${DEMO_AT}`;
  note.hidden = false;
}
fetch("/api/status").then((r) => r.json()).then((s) => {
  $("#llm-status").textContent = s.llm
    ? `Сейчас ответы пишет Gemini (${s.model}; кончилась квота у одной модели — отвечает следующая). Не пишите в запросе личные данные: он уходит в Gemini.`
    : "Сейчас ключа Gemini нет: запрос разбирают правила, ответ собирается по шаблону из тех же фактов.";
}).catch(() => {});
