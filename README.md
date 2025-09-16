# Guida AI/UX dell’applicazione

Questa guida presenta l’esperienza d’uso e il comportamento “intelligente” dell’app per la generazione di report ESG di un impianto tessile (lino). È pensata per chi utilizza o dimostra il sistema e desidera capire **cosa fa** e **come interagirci**, senza dettagli di implementazione.

---

## 1) Panoramica

* Applicazione chat-based con **agenti specializzati** per i tre ambiti ESG.
* L’utente dialoga in linguaggio naturale; l’agente richiama **tool strutturati** e produce **report tabellari** con sintesi.
* Output in **streaming** (token-by-token) con visibilità opzionale dei passaggi interni.
* La **cronologia** della chat è **persistente**: non si perde cambiando agente.

### Agenti disponibili

* 🌿 **ENV** — Report Ambientale
* 👥 **SOCIAL** — Report Sociale
* ⚖️ **DSS** — Analisi Decisionale (AHP) che combina indicatori ENV+SOC

---

## 2) Esperienza d’uso (UX)

### 2.1 Selettore agente (sidebar)

* Presente un **select box** per scegliere la modalità: ENV / SOCIAL / DSS.
* Il **cambio agente non azzera** la conversazione: la history rimane visibile per continuità e confronto.

### 2.2 Chat in streaming

* Le risposte arrivano **progressivamente** per massimizzare la reattività.
* Opzione **🧠 Thinking**: mostra il contenuto tra `<think>...</think>` (utile per trasparenza/debug).
* Ogni invocazione di strumento è tracciata in un expander **🔧 Eseguendo strumento** con **input** e **output** (auditabilità).

### 2.3 Tracciamento “nascosto” per il modello

* Ogni messaggio può includere una **traccia strumenti** in un **commento HTML** non visibile in UI ma accessibile al modello.
* Migliora l’auto-contestualizzazione senza “rumore” per l’utente.

### 2.4 Editor integrati dei dati

* **Editor Dati Ambientali**: modifica del JSON delle misure sensori.
* **Editor Social**: inserimento/gestione KPI sociali per stabilimento e periodo.
* **Editor Target KPI**: gestione soglie/parametri di valutazione (bootstrap automatico se il file non esiste).
* Tutti gli editor eseguono **validazione JSON** prima del salvataggio.

### 2.5 Gestione degli errori

In presenza di dataset mancanti, JSON non valido o assenza di dati:

* L’agente **interrompe** l’elaborazione,
* Spiega **cosa manca**, **dove intervenire** (percorso file) e **cosa succede** dopo la correzione.

---

## 3) Interazione consigliata (prompt)

* **ENV**: “Genera report ambientale”, “KPI ambientali”, “Usa ultimi 5 campioni”, “CO₂ in ppm dal campo `co2_ppm`”.
* **SOCIAL**: “Genera report sociale”, “KPI sociali per Stabilimento\_Lino\_A (Q1 2025)”.
* **DSS**: “Calcola DSS con i KPI correnti”, “Usa questa `category_matrix`…”.

**Suggerimenti operativi**

* Specificare parametri quando disponibili (es. `window_n` per ENV; `facility`/periodo per SOCIAL).
* Per DSS, assicurarsi che i report ENV e SOCIAL siano stati **eseguiti** (oppure passare direttamente `env_kpis.current` e `social_kpis.current`).

---

## 4) Cosa produce l’app (per agente)

### 4.1 Report Ambientale (ENV)

* **Tabella KPI** con:
  **Valore Attuale** · **Target (da JSON)** · **Status** (🟢/🟡/🔴/INDEFINITO) · **Trend** (↗/→/↘)
* **Punteggio complessivo** (0–100) con **rating** a emoji.
* **3 raccomandazioni** (immediata / breve / medio termine), coerenti con gli scostamenti osservati.
* **Note** su finestra temporale e origine dei dati.

### 4.2 Report Sociale (SOCIAL)

* **Tabella KPI** analoga (HR, sicurezza, etica, engagement).
* **Score sociale** con **rating**.
* **3 raccomandazioni** mirate.
* **Note** su stabilimento e periodo utilizzati.

### 4.3 Analisi DSS (AHP)

* **Pesi di categoria** (environment / social / economic\*) e **CR** (Consistency Ratio).
* **Pesi interni** per indicatore (se fornite le relative matrici).
* **Overall score** in percentuale e **priority ranking** (dal più urgente).
* Tabella **final\_items** con **peso finale**, **valore normalizzato**, **contributo** e **gap**.

> \* La componente **Economico** è un **placeholder neutro = 0.5** sino alla disponibilità di KPI economici reali.
