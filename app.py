import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import folium
import streamlit as st
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from streamlit_js_eval import streamlit_js_eval

from city_graph import CRIMES_PATH, all_crimes, build_city, geocode, nearest_node
from routing import dijkstra, haversine_m, nearest_safe_table, path_length_m, path_to_safe, safest_within

APP_NAME = "Chega Bem - HeraGPS"
MAX_WALK_M = 3000
WALK_M_PER_MIN = 75
SAO_PAULO_TZ = timezone(timedelta(hours=-3))
PERIOD_LABEL = {"day": "dia", "night": "noite"}
ROUTE = "#24348C"
EMERGENCY = "#C62828"
SAFE_POINT = "#1C7C74"
TEAL = "#0F3D44"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
DEFAULT_CENTER = (-23.5643, -46.6527)
GOOD_ACCURACY_M = 50
GPS_JS = """new Promise(resolve => {
  if (!navigator.geolocation) return resolve({error: 'unsupported'});
  let best = null, id = null;
  const done = () => { navigator.geolocation.clearWatch(id); resolve(best || {error: 'timeout'}); };
  id = navigator.geolocation.watchPosition(p => {
    if (!best || p.coords.accuracy < best.accuracy)
      best = {lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy};
    if (best.accuracy <= 30) done();
  }, e => { if (!best) resolve({error: e.message}); }, {enableHighAccuracy: true, maximumAge: 0, timeout: 15000});
  setTimeout(done, 5000);
})"""

st.set_page_config(page_title=APP_NAME, page_icon=os.path.join(ASSETS, "favicon.png"), layout="wide")
st.logo(os.path.join(ASSETS, "logo_dark.png"), icon_image=os.path.join(ASSETS, "favicon.png"), size="large")
st.html("<style>[data-testid=stElementContainer]:has(iframe[title*=streamlit_js_eval]){display:none}</style>")


@st.cache_resource(show_spinner=False)
def preload_crimes():
    threading.Thread(target=all_crimes, args=(CRIMES_PATH,), daemon=True).start()


@st.cache_resource(show_spinner=False, max_entries=16)
def cached_city(lat1, lon1, lat2, lon2):
    return build_city(lat1, lon1, lat2, lon2)


@st.cache_resource(show_spinner=False, max_entries=16)
def cached_safe_table(city_key, period):
    city = cached_city(*city_key)
    start = time.perf_counter()
    table, rounds = nearest_safe_table(city.graph, list(city.safe_points), risk_weight(period))
    return table, rounds, time.perf_counter() - start


def length_weight(edge):
    return edge.length_m


def risk_weight(period):
    if period == "night":
        return lambda edge: edge.length_m * edge.risk_night
    return lambda edge: edge.length_m * edge.risk_day


def path_edges(graph, path):
    return [(u, next(e for n, e in graph[u] if n == v)) for u, v in zip(path, path[1:])]


def path_points(city, path):
    points = []
    for u, edge in path_edges(city.graph, path):
        geometry = edge.geometry
        if haversine_m(geometry[0], city.nodes[u]) > haversine_m(geometry[-1], city.nodes[u]):
            geometry = geometry[::-1]
        points += geometry
    return points


def exposure(graph, path, weight):
    return sum(weight(e) for _, e in path_edges(graph, path))


def street_name(named, edge):
    if edge.name:
        return edge.name
    lat, lon = edge.geometry[len(edge.geometry) // 2]
    k = math.cos(math.radians(lat)) ** 2
    best = min(named, key=lambda p: (p[0] - lat) ** 2 + k * (p[1] - lon) ** 2)
    return best[2] if haversine_m((lat, lon), best[:2]) <= 25 else "Calçada ou travessia"


def directions(city, path):
    named = [(lat, lon, e.name) for es in city.graph.values() for _, e in es
             if e.name and e.highway != "footway" for lat, lon in e.geometry]
    steps = []
    for _, edge in path_edges(city.graph, path):
        name = street_name(named, edge)
        if steps and (steps[-1][0] == name or edge.length_m < 25):
            steps[-1][1] += edge.length_m
        else:
            steps.append([name, edge.length_m])
    return steps


def attention_points(graph, path, period, top=4):
    crimes = (lambda e: e.crimes_night) if period == "night" else (lambda e: e.crimes_day)
    seen, rows = set(), []
    for _, edge in sorted(path_edges(graph, path), key=lambda ue: -crimes(ue[1])):
        name = edge.name
        if not name or crimes(edge) == 0 or name in seen:
            continue
        seen.add(name)
        count = crimes(edge)
        notes = [f"{count} {'ocorrência registrada' if count == 1 else 'ocorrências registradas'} ({PERIOD_LABEL[period]})"]
        if edge.lit == "no":
            notes.append("sem iluminação")
        if edge.highway in ("path", "steps"):
            notes.append("passagem isolada")
        rows.append(f"**{name}**: " + ", ".join(notes))
        if len(rows) == top:
            break
    return rows


def short_label(display_name):
    return ", ".join(display_name.split(", ")[:3])


def minutes(meters):
    return max(1, round(meters / WALK_M_PER_MIN))


def lookup(text, what):
    try:
        found = geocode(text)
    except Exception:
        st.sidebar.error("Serviço de endereços indisponível agora. Tente novamente em instantes.")
        return None
    if not found:
        st.sidebar.error(f"{what} não encontrado. Tente incluir número, rua ou bairro.")
    return found


def current_period(choice):
    now = "day" if 6 <= datetime.now(SAO_PAULO_TZ).hour < 18 else "night"
    return {"Dia (06h-18h)": "day", "Noite (18h-06h)": "night"}.get(choice, now)


def you_are_here(m, origin):
    folium.CircleMarker(origin, radius=8, color="white", weight=3, fill=True, fill_color=TEAL,
                        fill_opacity=1, tooltip="Você está aqui").add_to(m)


def gps_origin():
    with st.sidebar:
        gps = streamlit_js_eval(js_expressions=GPS_JS, key=f"gps_{st.session_state.setdefault('gps_try', 0)}")
    if st.session_state.get("pin"):
        st.sidebar.success("Usando a posição marcada no mapa.")
        return st.session_state.pin, None, "Posição marcada no mapa"
    if gps is None:
        st.sidebar.info("Obtendo sua localização. Permita o acesso quando o navegador pedir.")
        return None, None, None
    if "error" in gps:
        st.sidebar.error("Não foi possível obter a localização. Permita o acesso ou digite o endereço.")
        return None, None, None
    accuracy = gps["accuracy"]
    if accuracy <= GOOD_ACCURACY_M:
        st.sidebar.success(f"Localização obtida (precisão de ~{accuracy:.0f} m).")
    else:
        st.sidebar.warning(f"Localização imprecisa (~{accuracy:.0f} m). Toque no mapa para marcar onde você está.")
    return (gps["lat"], gps["lon"]), accuracy, "Sua localização atual"


def sidebar():
    st.sidebar.caption("Rotas a pé mais seguras, calculadas com as ocorrências registradas pela SSP-SP.")
    mode = st.sidebar.radio("Origem", ["Minha localização (GPS)", "Digitar endereço"])
    origin = accuracy = origin_label = origin_text = None
    if mode.startswith("Minha"):
        origin, accuracy, origin_label = gps_origin()
        if st.sidebar.button("Atualizar localização", use_container_width=True):
            st.session_state.gps_try += 1
            st.session_state.pop("pin", None)
            st.rerun()
    else:
        origin_text = st.sidebar.text_input("Endereço de origem", placeholder="Ex.: Avenida Paulista, 1106")
    destination_text = st.sidebar.text_input("Destino", placeholder="Ex.: Metrô Consolação")
    period_choice = st.sidebar.selectbox("Horário da caminhada", ["Agora", "Dia (06h-18h)", "Noite (18h-06h)"])
    detour = st.sidebar.slider("Quanto a mais você aceita andar por uma rota mais segura?", 0, 100, 30, 10, format="%d%%")

    if st.sidebar.button("Ver rotas", type="primary", use_container_width=True):
        start_trip(mode, origin, origin_label, origin_text, destination_text, period_choice, detour)
    st.sidebar.divider()
    st.sidebar.caption("Em perigo, ligue 190 (Polícia Militar) ou 180 (Central de Atendimento à Mulher).")
    return mode, origin, accuracy


def start_trip(mode, origin, origin_label, origin_text, destination_text, period_choice, detour):
    if mode == "Digitar endereço":
        if not origin_text:
            st.sidebar.error("Informe o endereço de origem.")
            return
        found = lookup(origin_text, "Endereço de origem")
        if not found:
            return
        origin, origin_label = found[:2], short_label(found[2])
    elif origin is None:
        st.sidebar.error("Localização ainda não obtida. Marque sua posição no mapa ou digite o endereço.")
        return
    if not destination_text:
        st.sidebar.error("Informe o destino.")
        return
    found = lookup(destination_text, "Destino")
    if not found:
        return
    distance = haversine_m(origin, found[:2])
    if distance > MAX_WALK_M:
        st.sidebar.error(f"O destino está a {distance / 1000:.1f} km em linha reta. "
                         f"O {APP_NAME} calcula rotas a pé de até {MAX_WALK_M / 1000:.0f} km.")
        return
    st.session_state.trip = {
        "origin": origin, "origin_label": origin_label,
        "destination": found[:2], "destination_label": short_label(found[2]),
        "period": current_period(period_choice), "detour": detour,
    }
    st.session_state.emergency = False


def welcome(mode, origin, accuracy):
    st.title(APP_NAME)
    st.write("Informe o destino na barra lateral e toque em **Ver rotas**.")
    m = folium.Map(location=origin or DEFAULT_CENTER, zoom_start=17 if origin else 15,
                   tiles="OpenStreetMap", control_scale=True)
    if origin:
        if accuracy:
            folium.Circle(origin, radius=accuracy, color=ROUTE, weight=1, fill=True, fill_opacity=0.1).add_to(m)
        you_are_here(m, origin)
    gps_mode = mode.startswith("Minha")
    if gps_mode:
        st.caption("Se o ponto verde-escuro não estiver onde você está, toque no lugar certo no mapa.")
    clicked = st_folium(m, height=560, use_container_width=True, key="pick",
                        returned_objects=["last_clicked"] if gps_mode else [])
    point = (clicked or {}).get("last_clicked")
    if gps_mode and point:
        pin = (point["lat"], point["lng"])
        if pin != st.session_state.get("pin"):
            st.session_state.pin = pin
            st.rerun()


def route_screen(trip):
    st.title(APP_NAME)
    st.markdown(f"**De:** {trip['origin_label']}  \n**Para:** {trip['destination_label']}")

    origin, destination, period = trip["origin"], trip["destination"], trip["period"]
    city_key = tuple(round(x, 3) for x in (*origin, *destination))
    with st.spinner("Carregando as ruas da região e as ocorrências registradas..."):
        try:
            city = cached_city(*city_key)
        except Exception as error:
            st.error(f"Não foi possível carregar o mapa da região agora ({error}). Tente novamente em instantes.")
            return
    if not city.nodes:
        st.error("Nenhuma rua para pedestres encontrada nessa região.")
        return
    src, dst = nearest_node(city.nodes, *origin), nearest_node(city.nodes, *destination)
    if src == dst:
        st.success("Você já está no destino.")
        return

    by_risk = risk_weight(period)
    timings = {}
    start = time.perf_counter()
    short = dijkstra(city.graph, src, dst, length_weight)
    safe = dijkstra(city.graph, src, dst, by_risk)
    timings["dijkstra"] = time.perf_counter() - start
    if not short:
        st.error("Não existe caminho a pé entre esses pontos.")
        return
    short_len = path_length_m(city.graph, short[1])
    start = time.perf_counter()
    rounded_short = sum(math.ceil(e.length_m / 10) * 10 for _, e in path_edges(city.graph, short[1]))
    budget = max(short_len * (1 + trip["detour"] / 100), rounded_short)
    limited = safest_within(city.graph, city.nodes, src, dst, budget, by_risk)
    timings["memo"] = time.perf_counter() - start
    table, rounds, timings["table"] = cached_safe_table(city_key, period)

    candidates = [
        ("Recomendada", limited[1] if limited else safe[1],
         f"A mais segura entre as rotas com até {trip['detour']}% a mais de caminhada."),
        ("Mais segura", safe[1], "Menor exposição a ocorrências, sem limite de distância."),
        ("Mais curta", short[1], "Menor distância, sem considerar o risco."),
    ]
    routes = {}
    for label, path, note in candidates:
        if all(path != p for p, _ in routes.values()):
            routes[label] = (path, note)

    choice = st.segmented_control("Rota", list(routes), default="Recomendada") or "Recomendada"
    path, note = routes[choice]
    length = path_length_m(city.graph, path)
    risk_change = (exposure(city.graph, path, by_risk) / exposure(city.graph, short[1], by_risk) - 1) * 100

    cols = st.columns(3)
    cols[0].metric("Distância", f"{length:.0f} m")
    cols[1].metric("A pé", f"{minutes(length)} min")
    if path == short[1]:
        cols[2].metric("Risco", "Referência")
    else:
        cols[2].metric("Risco", f"{abs(risk_change):.0f}% menor" if risk_change < 0 else f"{risk_change:.0f}% maior")
    st.caption(f"{note} Horário considerado: {PERIOD_LABEL[period]}.")

    emergency_path = path_to_safe(table, src) if src in table else None
    if st.button("Emergência: ir ao ponto seguro mais próximo", type="primary", use_container_width=True):
        st.session_state.emergency = not st.session_state.get("emergency", False)
    emergency = st.session_state.get("emergency", False)
    if emergency:
        if emergency_path:
            safe_len = path_length_m(city.graph, emergency_path)
            st.error(f"Ponto seguro mais próximo: **{city.safe_points[emergency_path[-1]]}**, "
                     f"a {safe_len:.0f} m ({minutes(safe_len)} min). Em perigo, ligue 190 ou 180.")
        else:
            st.error("Nenhum ponto seguro encontrado nesta região. Ligue 190.")

    show_crimes = st.checkbox("Mostrar ocorrências registradas no mapa",
                              help="Peso por gravidade: furto 1; roubo, ameaça e porte de arma 2; lesão corporal e perseguição 3; sequestro, tentativa de homicídio e crime sexual 4; homicídio, feminicídio e latrocínio 5.")
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    if show_crimes:
        HeatMap([(lat, lon, w) for lat, lon, p, w in city.crimes if p in (period, "unknown")],
                radius=14, blur=18, min_opacity=0.3, max_zoom=15,
                gradient={0.3: "#F2D16B", 0.6: "#E8862A", 0.85: "#C62828", 1: "#7A0E0E"}).add_to(m)
    for other, (other_path, _) in routes.items():
        if other != choice:
            folium.PolyLine(path_points(city, other_path), color="#7A7A7A", weight=4,
                            opacity=0.5, dash_array="6 8", tooltip=other).add_to(m)
    main_points = path_points(city, path)
    folium.PolyLine(main_points, color=ROUTE, weight=7, opacity=0.9, tooltip=choice).add_to(m)
    if emergency and emergency_path:
        folium.PolyLine(path_points(city, emergency_path), color=EMERGENCY, weight=7,
                        tooltip="Ponto seguro mais próximo").add_to(m)
    for node, label in city.safe_points.items():
        folium.CircleMarker(city.nodes[node], radius=6, color="white", weight=2, fill=True,
                            fill_color=SAFE_POINT, fill_opacity=1, tooltip=label).add_to(m)
    you_are_here(m, origin)
    folium.Marker(destination, tooltip="Destino", icon=folium.Icon(color="darkblue", icon="flag")).add_to(m)
    m.fit_bounds([origin, destination, *main_points])
    st_folium(m, height=480, use_container_width=True, returned_objects=[], key="map")
    st.caption("Linha azul: rota escolhida. Tracejado cinza: outras opções. "
               "Pontos verdes: locais seguros (polícia, hospital, farmácia 24h, estação).")

    st.subheader("Passo a passo")
    st.markdown("\n".join(f"{i}. {name}: {meters:.0f} m" for i, (name, meters) in
                          enumerate(directions(city, path), 1)))

    attention = attention_points(city.graph, path, period)
    if attention:
        st.subheader("Pontos de atenção nesta rota")
        st.markdown("\n".join(f"- {row}" for row in attention))

    with st.expander("Como funciona"):
        edges = sum(len(v) for v in city.graph.values()) // 2
        st.markdown(f"""
- **Mapa da região:** {len(city.nodes)} esquinas, {edges} trechos de rua, {len(city.safe_points)} locais seguros e {len(city.crimes)} ocorrências da SSP-SP (2025-2026).
- **Grafos (Dijkstra):** rota mais curta (peso = metros) e mais segura (peso = metros x risco do trecho). {timings['dijkstra'] * 1000:.0f} ms.
- **Memoização:** `melhor(esquina, metros_restantes)` com `lru_cache` encontra a rota recomendada dentro do limite de distância. {timings['memo'] * 1000:.0f} ms. Cache: {limited[2] if limited else '-'}.
- **Tabulação (Bellman-Ford):** `dp[k][v]` = menor risco da esquina v até qualquer local seguro usando até k trechos. {rounds} rodadas, {timings['table'] * 1000:.0f} ms, calculado uma vez para todas as esquinas.
""")


if not os.path.exists(CRIMES_PATH):
    st.error("Arquivo de ocorrências `data/crimes_sp.csv.gz` não encontrado. Rode `python ssp_data.py`.")
else:
    mode, origin, accuracy = sidebar()
    if "trip" in st.session_state:
        route_screen(st.session_state.trip)
    else:
        welcome(mode, origin, accuracy)
    preload_crimes()
