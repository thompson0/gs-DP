# Chega Bem - HeraGPS: rotas a pé mais seguras para mulheres

Global Solution 2026.2, **CitizenTech**. Disciplina: **Dynamic Programming**.

## Ficha da CitizenTech

| Campo | |
|---|---|
| **Nome** | Chega Bem - HeraGPS |
| **Cidade e bairro** | São Paulo, Bela Vista (entorno da FIAP Paulista). O app funciona em qualquer ponto da capital, a partir do GPS |
| **Cidadão beneficiado** | Mulheres que caminham pela cidade, principalmente à noite. Exemplo: a estudante que sai da faculdade às 22h e vai a pé até o metrô |
| **Problema, em uma frase** | A rota mais curta pode passar por ruas com muitos roubos e pouco movimento, e quem caminha não tem como saber disso antes de sair |
| **Como funciona** | O app pega a localização pelo GPS e recebe o destino. Baixa as ruas reais da região (OpenStreetMap) e cruza com mais de 520 mil ocorrências reais em via pública registradas pela SSP-SP (furtos, roubos, lesões, homicídios e violência contra a mulher), cada uma com peso pela gravidade. Depois mostra no mapa a rota recomendada, a mais segura e a mais curta, com passo a passo e um botão de emergência que leva ao ponto seguro mais próximo |
| **O que cada parte faz** | Dados (`ssp_data.py`), mapa e risco (`city_graph.py`), algoritmos (`routing.py`) e interface (`app.py`). Detalhes na seção [Estrutura](#estrutura) |
| **Link do repositório** | https://github.com/thompson0/gs-DP |
| **Indicador de impacto** | % de redução da exposição ao risco em cada rota, comparada com a mais curta (o app mostra esse número). Exemplo real, à noite, da Av. Paulista 1106 ao Metrô Consolação: **39% menos risco andando 278 m a mais** |
| **ODS conectados** | **5** Igualdade de gênero (meta 5.2: eliminar a violência contra mulheres nos espaços públicos). **11** Cidades sustentáveis (meta 11.7: espaços públicos seguros, em especial para mulheres). **10** Redução das desigualdades |

## Grupo

| RM | Nome |
|---|---|
| 563126 | Gabriel Thompson Freitas |
| 561503 | Enzo Quarelo |
| 563186 | Jose Otavio Kaneto |
| 563245 | Nicolas Baradel |
| 562499 | João Pedro Sassarrão de Carvalho |

## Funcionalidades

1. **Entrada direta.** Ao abrir o site, o app já pede a localização. A barra lateral pergunta o destino.
2. **Origem pelo GPS.** A usuária toca no botão de localização (`streamlit-geolocation`) e permite o acesso. Se a precisão passar de 50 m, o app avisa e ela toca no mapa para marcar onde está. Também é possível digitar o endereço.
3. **Destino por endereço.** O endereço é convertido em coordenada pelo Nominatim (OpenStreetMap).
4. **Três rotas, uma por vez:**
   - **Recomendada:** a mais segura entre as que andam no máximo X% a mais que a mais curta (padrão +30%, ajustável na barra lateral).
   - **Mais segura:** sem limite de distância.
   - **Mais curta.**
   A rota escolhida aparece destacada no mapa, e as outras aparecem tracejadas.
5. **Resumo da rota.** Distância, tempo a pé e percentual de risco em relação à mais curta.
6. **Mapa com nomes de rua** (OpenStreetMap), com os locais seguros marcados e, opcionalmente, um mapa de calor das ocorrências, ponderado pela gravidade de cada crime.
7. **Passo a passo.** Lista as ruas da rota, na ordem, com a distância em cada uma. Calçadas sem nome no mapa recebem o nome da rua mais próxima.
8. **Pontos de atenção.** Mostra as ruas da rota com mais ocorrências no período e as que não têm iluminação.
9. **Botão de emergência.** Mostra a rota até o local seguro mais próximo (delegacia ou base policial, hospital, farmácia 24h ou estação) e os telefones 190 e 180.
10. **Horário.** "Agora" usa o horário de São Paulo. Também dá para escolher dia (06h–18h) ou noite (18h–06h). O risco de cada rua muda com o horário.
11. **"Como funciona".** Mostra o tamanho do grafo, o tempo de cada algoritmo e as estatísticas do cache da memoização.

## Algoritmos

O mapa vira um **grafo**: cada **esquina** é um vértice e cada **trecho de rua** entre duas esquinas é uma aresta. Cada aresta tem dois pesos possíveis:

- **comprimento** (metros), usado na rota mais curta;
- **exposição ao risco** = comprimento × risco do trecho no período, usada nas rotas seguras.

| Técnica | Função (`routing.py`) | O que resolve | Complexidade |
|---|---|---|---|
| **Grafos** | `dijkstra` | Rota mais curta e rota mais segura entre origem e destino | O((V + E) log V) com `heapq` |
| **Memoização** | `safest_within` | Rota mais segura **sem passar de X metros** | O(V × B × grau) estados, em vez de testar todos os caminhos (exponencial) |
| **Tabulação** | `nearest_safe_table` | Ponto seguro mais próximo de **todas** as esquinas de uma vez | O(k × E), com k rodadas |

### Grafos: Dijkstra

Usa uma fila de prioridade (`heapq`) e sempre expande a esquina mais barata ainda não finalizada. Os pesos são positivos (metros e risco ≥ 1), então a primeira vez que o destino sai da fila o custo dele já é o mínimo. O mesmo código calcula as duas rotas. Só muda a função de peso passada como parâmetro.

### Memoização: rota mais segura com limite de distância

O Dijkstra não aceita a restrição "no máximo X metros". O subproblema é:

```
melhor(esquina, metros_restantes) = menor exposição para ir da esquina até o destino
                                    andando no máximo metros_restantes
melhor(destino, r)  = 0
melhor(v, r)        = min( peso(v→u) + melhor(u, r − comprimento(v→u)) )  para cada vizinho u
```

- O mesmo par (esquina, metros restantes) aparece em muitos caminhos diferentes. Por isso `@lru_cache` guarda cada resposta e ela é calculada **uma vez só**.
- As distâncias são contadas em unidades de 10 m, arredondadas para cima. Assim o orçamento sempre diminui, e a recursão termina mesmo com ciclos no grafo.
- **Poda:** se a distância em linha reta até o destino é maior que o que resta, o estado é descartado na hora.
- Na tela, `cache_info()` mostra quantas chamadas foram respondidas pelo cache (hits).

### Tabulação: Bellman-Ford a partir dos pontos seguros

```
dp[0][v] = 0 se v é ponto seguro, ∞ caso contrário
dp[k][v] = min( dp[k−1][v], peso(v→u) + dp[k−1][u] )   para cada vizinho u
```

- `dp[k][v]` é a menor exposição de v até **qualquer** ponto seguro usando no máximo k trechos.
- A tabela é preenchida linha a linha, de k = 0 em diante. Só a linha anterior é guardada (tabela "rolante"), e o cálculo para quando uma linha não muda mais.
- Junto com o custo, cada esquina guarda o **próximo passo** em direção ao ponto seguro. É isso que reconstrói a rota de emergência.
- A tabela é calculada **uma vez** por região e período. Depois, o botão de emergência responde instantaneamente de qualquer esquina.

**Por que não Floyd-Warshall?** Ele calcula todos os pares, mas custa O(V³). Com as 2.279 esquinas da região da Paulista, isso dá ~1,3 × 10¹⁰ operações, dezenas de minutos em Python. O Bellman-Ford com várias origens responde exatamente a pergunta da emergência em O(k × E).

### Eficiência medida (dados reais, Av. Paulista 1106 → Metrô Consolação, noite)

| Etapa | Tempo |
|---|---|
| Grafo da região | 2.279 esquinas, 3.439 trechos, 15 pontos seguros, 11.134 ocorrências |
| Dijkstra (2 rotas) | ~9 ms |
| Memoização (+30%) | ~40 ms. O cache respondeu ~15 mil chamadas |
| Tabulação (todas as esquinas) | ~150 ms, 40 rodadas |
| Download das ruas (OpenStreetMap) | 30–60 s na primeira vez. Depois fica em cache no disco (`cache/`) |

## Como medimos o perigo de um trecho

Para cada trecho de rua e cada período (dia ou noite), em `city_graph.py`:

```
densidade = soma dos pesos das ocorrências do período a até 30 m do trecho / (comprimento / 100 m)
risco     = 1 + 1.0 × log(1 + densidade) + iluminação + via isolada
exposição = comprimento × risco
```

- **Ocorrências com nível de perigo** (`CRIME_WEIGHT` em `city_graph.py`). O mesmo peso vale para o mapa de calor:

  | Peso | Crimes |
  |---|---|
  | 1 | Furto |
  | 2 | Roubo, ameaça (inclui descumprimento de medida protetiva), porte de arma |
  | 3 | Lesão corporal dolosa, perseguição (*stalking*) |
  | 4 | Extorsão mediante sequestro, tentativa de homicídio, crimes contra a dignidade sexual |
  | 5 | Homicídio doloso, feminicídio, latrocínio, lesão corporal seguida de morte |

- Uma ocorrência de horário desconhecido conta nos dois períodos.
- **log(1 + densidade):** a SSP agrupa centenas de BOs em uma mesma coordenada (endereço aproximado). Sem o log, poucos trechos dominariam o risco.
- **Iluminação** (tag `lit` do OpenStreetMap):
  - `lit=no` soma +1.5 à noite e +0.2 de dia;
  - rua sem a informação soma +0.3 só à noite, para preferir ruas confirmadamente iluminadas.
- **Via isolada:** trilhas, escadarias e passagens de pedestres que não são calçada somam +0.5.

Os pesos são constantes no topo de `city_graph.py` e podem ser ajustados. Na disciplina de Data Science, a regressão logística pode calibrá-los.

## Dados

| Dado | Fonte | Uso |
|---|---|---|
| Ocorrências | [SSP-SP, Portal da Transparência](https://www.ssp.sp.gov.br/estatistica/consultas): `SPDadosCriminais` 2025 e 2026 (todos os crimes) e base de **Violência contra a Mulher** | Risco de cada trecho |
| Ruas, iluminação, pontos seguros | [OpenStreetMap](https://www.openstreetmap.org) via Overpass API (licença ODbL) | Grafo |
| Endereços | Nominatim (OpenStreetMap) | Origem digitada e destino |

**Tratamento** (`ssp_data.py`):
- Das 2,9 milhões de linhas, ficam só as ocorrências **na capital, em via pública, com coordenada**, de 2025 em diante, e dos crimes da tabela de pesos. Ficam de fora, por exemplo, acidentes de trânsito, crimes de drogas, furto de veículo e injúria.
- Cada crime de um BO conta uma vez só. A base de violência contra a mulher tem uma linha por pessoa, e um mesmo BO pode estar nas duas bases: as duplicatas são removidas.
- O período sai do campo da SSP. Quando ele está vazio, é calculado pela hora.
- Resultado: **522.757 ocorrências** em `data/crimes_sp.csv.gz` (3 MB, versionado): 308.692 furtos, 152.124 roubos, 31.799 lesões corporais, 14.287 ameaças, 13.459 perseguições, 1.087 portes de arma, 600 tentativas de homicídio, 491 homicídios, 148 crimes sexuais e 70 sequestros. As planilhas originais (~650 MB) não vão para o repositório.

**Nenhum dado pessoal** é usado ou guardado. A localização da usuária fica só na sessão do navegador.

### Limitações

- **Crimes sexuais quase não têm coordenada pública** (estupro: 0%), por privacidade das vítimas. Por isso só 148 entram no mapa.
- Na base de violência contra a mulher, só cerca de 35% das ocorrências têm coordenada. As que acontecem em casa vêm sem endereço, e isso é bom: a rota a pé depende do que acontece na rua.
- Há **subnotificação** e **viés de registro**: lugares movimentados geram mais BOs.
- A iluminação depende do OpenStreetMap. Na região da Paulista, só ~40% das ruas têm essa informação.
- Pontos seguros também vêm do OpenStreetMap. Uma Delegacia da Mulher só aparece se estiver mapeada.

## Como rodar

Requer Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows   (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
streamlit run app.py             # abre em http://localhost:8501
```

- **GPS:** o navegador só libera a localização em `localhost` ou HTTPS. No PC, basta abrir `http://localhost:8501` e permitir o acesso. No celular, é preciso publicar o app com HTTPS (ex.: Streamlit Community Cloud).
- A primeira busca em uma região baixa as ruas do OpenStreetMap e leva de 30 a 60 s. As seguintes usam o cache.
- **Atualizar as ocorrências:** `python ssp_data.py` baixa as planilhas da SSP (~650 MB) e gera `data/crimes_sp.csv.gz` de novo. Leva ~25 min.

## Estrutura

| Arquivo | Parte |
|---|---|
| `app.py` | Interface Streamlit: GPS, mapa (folium/Leaflet), rotas, passo a passo, emergência |
| `routing.py` | **Algoritmos**: Dijkstra, memoização e tabulação. Só biblioteca padrão |
| `city_graph.py` | Ruas do OpenStreetMap → grafo de esquinas, ocorrências → trechos, risco, pontos seguros, endereços |
| `ssp_data.py` | Download e tratamento das planilhas da SSP-SP |
| `data/crimes_sp.csv.gz` | Ocorrências tratadas (`lat,lon,period,crime,year`) |
| `assets/` | Logo (versão escura) e favicon, gerados a partir do logo da equipe |
| `.streamlit/config.toml` | Tema escuro com a paleta da marca e fonte Inter |
