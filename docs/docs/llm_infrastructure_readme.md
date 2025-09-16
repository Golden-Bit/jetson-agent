# Documentazione LLM — Architettura, Modelli, Agenti e Tool

Questa guida descrive l’infrastruttura LLM del progetto (serving, modelli, agenti e strumenti)

---

## 1) Panoramica

* **Obiettivo**: generare report ESG per un impianto tessile (lino) e un’analisi DSS/AHP combinando KPI ambientali e sociali.
* **Tre agenti specializzati** (selezionabili in UI/SDK):

  * **ENV-REPORT** → Report Ambientale
  * **SOC-REPORT** → Report Sociale
  * **DSS-ANALYST** → Analisi multi-criterio (AHP) a partire dai KPI ENV+SOC
* **LLM Serving**: **Ollama** esposto con API **OpenAI-compatible**.
* **Modelli**: famiglia **Qwen3** in quantizzazione **4-bit**:

  * *qwen3:8b (4-bit)* — leggero, più rapido
  * *qwen3:30b (4-bit)* — più capace, default per gli agenti production-like
* **Framework agentico**: **LangChain** (`langchain_openai.ChatOpenAI` + `create_tool_calling_agent` + `AgentExecutor`).
* **Streaming** end-to-end: token del modello e tracciamento degli strumenti in tempo reale (eventi UI).

---

## 2) Stack di Serving

* **Runtime**: Ollama, con endpoint OpenAI standard:

  * `OPENAI_BASE_URL` (es. `http://localhost:11434/v1`)
  * `OPENAI_API_KEY` (placeholder: `"ollama"`)
* **Selezione modello** via ENV:

  * `OLLAMA_MODEL` (default: `qwen3:30b`)
* **Parametri LLM principali**:

  * `temperature` (default: `0.2`) → risposte più stabili
  * `streaming: true` → token-by-token
* **Compatibilità client**:

  * usiamo `langchain_openai.ChatOpenAI`, quindi qualsiasi client OpenAI-like funziona (cURL/SDK).

---

## 3) Eventi di Streaming (contratto UI)

Il core emette un flusso di eventi strutturati (usati dalla UI Streamlit):

* `{"type":"token","text":"…"}` — chunk di testo (con eventuali `<think>…</think>`)
* `{"type":"tool_start","name":"…","inputs":{…},"run_id":"…"}` — avvio tool
* `{"type":"tool_end","name":"…","inputs":{…},"output":{…},"run_id":"…"}` — fine tool
* `{"type":"done"}` — conversazione/turn completato
* `{"type":"error","message":"…"}` — errore gestito

La UI mappa `run_id` → expander “🔧 Eseguendo strumento: …” e mostra input/output. Un expander “🧠 Thinking” visualizza i blocchi `<think>`.

---

## 4) Variabili d’Ambiente (quick reference)

| Variabile           | Default/Note                                             |
| ------------------- | -------------------------------------------------------- |
| `OPENAI_BASE_URL`   | `http://localhost:11434/v1` (Ollama OpenAI-compat)       |
| `OPENAI_API_KEY`    | `ollama` (placeholder)                                   |
| `OLLAMA_MODEL`      | `qwen3:30b` (si può impostare `qwen3:8b`)                |
| `AGENT_TEMPERATURE` | `0.2`                                                    |
| `HIDE_THINK`        | `true` / `false` (filtra blocchi `<think>` lato core)    |
| `SENSOR_DATA_PATH`  | Percorso JSON misure ambientali                          |
| `SOCIAL_DATA_PATH`  | Percorso JSON KPI sociali                                |
| `KPI_TARGETS_PATH`  | Percorso JSON soglie/target (auto-bootstrap con default) |

---

## 5) Agenti, System Message e Regole operative

Ogni modalità (ENV/SOC/DSS) istanzia un **AgentExecutor** con:

* **System message dedicato** (istruzioni di ruolo, formato tabellare, sequenze tool).
* **Set minimo di tool** abilitati per quella modalità.
* **Stesse regole di condotta**: niente dati inventati; una sola riga di contesto quando si usa un tool; interrompere e spiegare in caso di errore; usare “INDEFINITO” se mancano valori.

### 5.1 ENV-REPORT (Report Ambientale)

**System message — principi chiave**

* Attivo **solo** su richieste tipo “Report Ambientale” / “KPI ambientali”.
* **Sequenza**: 1) chiama `env_kpi_snapshot(window_n?, co2_field?)`; 2) genera report tabellare + sintesi.
* **Mostra** una riga di contesto: *(Leggo i KPI ambientali con `env_kpi_snapshot`)*.
* **Non inventare**: se un valore o target manca → **INDEFINITO**.
* **Trend e status**: usa quelli già calcolati dal tool (emoji 🟢/🟡/🔴, frecce ↗/→/↘).
* **Target**: prendi i target dalla chiave `targets_used` restituita dallo snapshot (non serve altra lettura).
* **Unità/arrotondamenti**: mostra unità quando disponibili (°C, %, lux, mm, g, ppm).

**Layout del report (estratto)**

* **Periodo**: `[window.from] – [window.to]` • **Stabilimento**: INDEFINITO
* **Tabella** con metriche:

  * Temperatura, Umidità, Luce, Distanza (mm), Vibrazioni (g), CO₂ (ppm o idx), Energia specifica, Acqua specifica, CO₂eq.ris./CO₂eq.tot.
* **Sintesi**:

  * Punteggio complessivo: `score.value (0–100)` + `score.rating` (emoji).
  * 3 raccomandazioni (immediata/breve/medio termine), coerenti coi KPI.
  * Nota sul dataset/finestra usata.

**Tool abilitati in modalità ENV**

* `env_kpi_snapshot` (principale: KPI + trend + `targets_used` + `window` + `score`)
* *helper di sola lettura opzionali, esposti in modalità ENV dove previsti dalla UI*:

  * `read_last_n` (ultimi N record sensori)
  * `read_data_by_time` (finestra temporale)

### 5.2 SOC-REPORT (Report Sociale)

**System message — principi chiave**

* Attivo **solo** su richieste tipo “Report Sociale” / “KPI sociali”.
* **Sequenza**: 1) `social_kpi_snapshot(facility?, window_n?)`; 2) tabella + sintesi.
* **Contesto**: *(Leggo i KPI sociali con `social_kpi_snapshot`)*.
* **Errore dati**: se lo snapshot ritorna errore (es. “Nessun dato sociale disponibile”), **interrompi** e indica di aggiornare il JSON al *percorso* indicato da `source`.
* **Target e status/trend**: come ENV, usa `targets_used`, `status`, `trend` dallo snapshot.
* **Unità**: % / h anno / h mese / conteggi, ecc.

**Layout del report (estratto)**

* **Periodo**: `[period.start] – [period.end]` • **Stabilimento**: `[facility]`
* **Tabella** con metriche\*\*: turnover, ore formazione annue/dipendente, indice di soddisfazione (scala), assenteismo, % donne, infortuni/1000h, salario vs benchmark, % fornitori etici, ore straordinario/mese, progetti comunità.
* **Sintesi**:

  * Punteggio complessivo: `score.value (0–100)` + `score.rating`.
  * 3 raccomandazioni, aree di eccellenza/miglioramento (in base a 🟢/🔴 e trend).

**Tool abilitati in modalità SOCIAL**

* `social_kpi_snapshot` (principale: KPI + trend + `targets_used` + `period` + `score`)
* (solo lettura opzionale usata dalla UI editor/gestione dati): `read_social_kpis`, `upsert_social_kpis`

### 5.3 DSS-ANALYST (Analisi AHP)

**System message — principi chiave**

* Attivo **solo** su richieste di **DSS/AHP**.
* **Prerequisito**: Riceve in input **i due blocchi KPI** (i dizionari `current` prodotti da ENV e SOC).
  Se mancano → spiega che servono i report e **non** chiamare il tool.
* **Sequenza**: 1) valida presenza `env_kpis` & `social_kpis`; 2) chiama `dss_compute(...)`; 3) formatta risultati (pesi, CR, ranking, score, tabella *final\_items*).
* **Normalizzazione** predefinita: 🟢=1.0, 🟡=0.6, 🔴=0.2 (INDEFINITO escluso).
* **Economico**: placeholder neutro `economic_value=0.5` finché non ci sono KPI economici reali.
* **Consistenza**: se **CR ≥ 0.1**, mostra **avviso** (“rivedere i confronti”) ma **riporta comunque** i risultati.

**Layout del report (estratto)**

* **Pesi categoria** (environment/social/economic) + **CR** matrice A.
* **Pesi interni** (environment/social) + **CR** (se date le matrici interne).
* **Overall score** (`overall_score_pct` %) e **priority ranking** (gap decrescente).
* **Tabella `final_items`**: nome indicatore, categoria, `final_weight`, `norm_value`, `contribution`, `gap`.

**Tool abilitato in modalità DSS**

* `dss_compute` (unico: combina ENV+SOC con AHP; accetta matrici opzionali e mapping stato→\[0,1]).

---

## 6) Strumenti (tool) — cosa fanno e cosa restituiscono

### 6.1 Ambientale

* **`env_kpi_snapshot(window_n=None, co2_field=None)`**

  * Usa i target da `kpi_targets.json` (auto-bootstrap).
  * Restituisce: `current` (metriche con `value`, `unit`, `status`, `trend`), `targets_used`, `window`, `score`.
  * `co2_field`: se presente, usa ppm reali; altrimenti usa `air_quality_raw` e imposta CO₂ a **INDEFINITO** per la valutazione contro target ppm.
* **`read_last_n(n=5, fields=None)`**: ultime N misurazioni sensori (per debug/contesto).
* **`read_data_by_time(start_ts,end_ts,fields)`**: storico per intervallo.

### 6.2 Sociale

* **`social_kpi_snapshot(facility=None, window_n=None)`**

  * Aggrega gli ultimi record sociali e confronta con target (con direzione: higher/lower/center).
  * Restituisce: `current` (status/trend per ogni KPI), `targets_used`, `period`, `facility`, `score`, eventuali `missing_fields`.
* **`read_social_kpis(...)` / `upsert_social_kpis(...)`**

  * Lettura/aggiornamento del datastore JSON sociale (usati dall’editor; non necessari per il report se i dati ci sono).
* **`read_kpi_targets(section=None, metrics=None)`**

  * Consultazione diretta dei target (usare **solo** se l’utente chiede di “vedere/validare i target”).

### 6.3 DSS

* **`dss_compute(env_kpis, social_kpis, category_matrix?, env_matrix?, social_matrix?, status_mapping?, economic_value=0.5)`**

  * Calcola pesi, **CR**, punteggi normalizzati, **overall score** e **priority ranking**; produce la tabella `final_items`.
