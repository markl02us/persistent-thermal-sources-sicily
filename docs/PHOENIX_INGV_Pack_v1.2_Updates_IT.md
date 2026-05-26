# Pacchetto PHOENIX–INGV v1.2 — Modifiche rispetto a v1.1

**Data preparazione:** 26 maggio 2026
**Sostituisce:** v1.1 del 25 maggio 2026
**Punto di contatto:** Gaetano Zambito — folderdj@gmail.com — +39 366 545 0598
**Sistema live:** https://adr-wildfire.com/
**Codice open-source:** https://github.com/markl02us/persistent-thermal-sources-sicily

---

## In sintesi

Tra v1.1 (sera del 25 maggio) e v1.2 (26 maggio), sono state rilasciate sette categorie di lavoro che cambiano materialmente quanto il pack afferma. Cambiamenti a maggior impatto:

1. **Due bug precedentemente dichiarati sono ora RISOLTI in produzione**
   (verifier burn-scar Sentinel-2; overflow FRP subpixel_v1_alpha).
2. **Il grader è ora v2.1** con distinzione race-strict / race-marginal / first-vs-VVF,
   soglie dNBR biome-aware, riconciliazione multi-stadio (T+72h → T+14g → T+45g),
   JSON comparator-panel, flag below-floor, e 14 nuove colonne `event_grades`.
3. **Stack di riproducibilità rilasciato end-to-end**: snapshot pubblici giornalieri,
   reproducer standalone (`scripts/regrade.py`), bootstrap di distribuzione nulla
   per permutazione (`/api/null_bootstrap`), intervallo di confidenza Wilson al 95% ovunque.
4. **Rebuild della pagina pubblica**: striscia profilo-sistema a 7 celle, chip di precisione
   per ogni sub-detector PHOENIX, eventi refuted aperti di default, toggle bilingue EN/IT,
   etichette ARIA, palette daltonica Wong-2011.
5. **Il titolo "3 race-valid wins" è onestamente RITIRATO**: sotto la soglia stretta abbiamo
   0 vittorie in 30 giorni (bootstrap p = 1,00 vs caso); sotto la soglia più permissiva,
   2 eventi PHOENIX-first negli ultimi 7 giorni, entrambi mostrati con asterischi metodologici espliciti.

L'INGV può fare audit end-to-end di qualunque numero su `/wins.html` dai pull grezzi FIRMS / EUMETSAT / VVF senza contattarci. Non era vero il 25 maggio; è vero il 26 maggio.

---

## Modifiche dettagliate

### Bug risolti

| Item | Stato v1.1 | Stato v1.2 |
|---|---|---|
| Verifier dNBR burn-scar Sentinel-2 | HTTP 400 ad ogni chiamata (82 null) | **Risolto end-to-end.** Tre bug sovrapposti: formato datetime STAC, firma SAS MPC, mismatch shape B8/B12. Commit GitHub `eadb2ed`. Smoke test sulla detection ADR del 26 aprile: pre_NBR=0,3072, post_NBR=0,3423, dNBR=−0,0351, verified_burn=False (corretto). Primo burn confermato (via SAR fallback): det_id 16802. |
| Overflow FRP subpixel_v1_alpha | Outlier fino a 3,9 PW (fisicamente impossibile) | **Risolto.** Max FRP ora 9,09 MW, media 2,73 MW, n=5.524, zero outlier sopra 10 GW. |

### Grader v2.1 — nuovo schema e metodologia

- **`race_strict`**: anticipo PHX > 0 AND lead < 50% della rivisita del comparator AND comparator_class='satellite_sensor' AND ≥ 1 comparator capace AND non sotto soglia.
- **`race_valid` (permissivo)**: anticipo PHX entro 100% della rivisita. Quando 50% < rapporto ≤ 100%, mostrato su `/wins.html` come "race-marginal*" con footnote.
- **`comparator_class`** ∈ {`satellite_sensor`, `human_dispatch`, `social`}. VVF e news sono `human_dispatch` — corroborano verità (T2) ma non competono con satelliti. PHOENIX-first vs VVF mostrato come "First vs VVF*" (visualizzazione differente + footnote).
- **`comparator_panel`** (JSON): lista per evento di ogni comparator capace con lead, rivisita, flag below_floor.
- **`worst_capable_lead_min`**: race_strict usa il lead PEGGIORE tra i comparator capaci, non il migliore. Chiude l'attacco "comparator-of-convenience".
- **`below_comparator_floor`**: 1 se la FRP dell'evento è sotto la soglia fisica di ogni comparator capace. Tali eventi sono esclusi dal denominatore FP — un comparator letteralmente non avrebbe potuto vederli.
- **`biome_class` + `dnbr_threshold_biome`**: derivati da ESA WorldCover. Foresta = 0,27 (Key & Benson 2006), shrubland/macchia = 0,18 (Fernández-Manso 2016, De Santis & Chuvieco 2009, Mallinis 2018), grassland/coltivato = 0,12. Sostituisce il valore universale 0,27 della v1 che è calibrato per foresta ed errato per la macchia mediterranea.
- **`wui_built_pct` + `wui_class`** ∈ {U Urbano, I Interfaccia WUI, W Selvatico, A Altro}: contesto operativo per dispatcher.
- **`phoenix_had_coverage`**: per eventi external-led (PHOENIX mancato), 1 se PHOENIX aveva un detector attivo durante l'acquisizione del comparator. Distingue mancata-algoritmica da mancata-feed.
- **`refute_strength`** ∈ {strong, weak, unverifiable}: strong = dNBR cloud-free < soglia biome; weak = dNBR ambiguo; unverifiable = nessuna scena S-2 disponibile. Solo `strong` conta contro la precisione.
- **Riconciliazione multi-stadio**: `t72h_outcome` (iniziale), `t14d_outcome` (ricerca estesa), `t45d_outcome` (disposizione finale della cicatrice). Ogni stadio può promuovere o retrocedere gli esiti precedenti. Eventi cloud-occluded marcati `no_signal_unverifiable`, NON refuted.

### Stack di riproducibilità

| Componente | URL | Funzione |
|---|---|---|
| Snapshot pubblici giornalieri | `https://adr-wildfire.com/data/snapshots/AAAA-MM-GG/` | `internal_fires.csv` grezzo + `external_fires.csv` + `corroboration_signals.csv` + `event_grades.csv` + `SHA256SUMS` + `README.md` |
| Reproducer standalone | `scripts/regrade.py` (su GitHub) | Prende gli input CSV e rigenera `event_grades.csv` usando lo stesso percorso codice della produzione. Verificato zero-mismatch su 2.172 eventi. |
| Bootstrap distribuzione nulla | `https://adr-wildfire.com/api/null_bootstrap` | Test permutazione 200 repliche sul conteggio race_strict. Risultato attuale: osservato=0, media nulla=12,7, p-value=1,00. **Pubblichiamo la nostra stessa falsificazione.** |
| Intervallo Wilson 95% | Mostrato su `/wins.html` precisione + chip per sub-detector | n < 30 → solo intervalli, no stime puntuali |
| Provenienza per riga | Link 🛰️ FIRMS + 🌍 Copernicus Browser | Click-through-alla-fonte su ogni riga evento |

### Riformulazione onesta dei win-counts

- **Vittorie race-strict (30 giorni):** 0. p-value bootstrap 1,00 vs null. PHOENIX attualmente NON è statisticamente distinguibile dal caso alla soglia stretta.
- **Eventi PHOENIX-first (7 giorni, race-valid permissivo):** 2.
  - 25-05-2026 `wind_diff` +9,4 min vs Vigili del Fuoco a (36,99°N, 14,37°E). T2 ("First vs VVF*").
  - 24-05-2026 `wind_diff` +9,1 min vs EUMETSAT MTG-AF-L2. T1 ("Race-marginal*", lead = 91% della rivisita).
- **Co-detected (≥ T1, PHOENIX co-rilevato con comparator):** 16.
- **Catturati da altri, mancati da PHOENIX:** 195.
- **Sole-reporter, in attesa di riconciliazione T+72h:** ~1.096.
- **Refuted a T+72h:** 623.
- **Precisione del resolved-set:** 1,74% (Wilson 95% IC: 0,97%–3,08%, n=634).

Tutti questi sono numeri onesti al 26-05-2026. La v1.1 del pack riportava "3 race-valid wins" — quella cifra usava una definizione che da allora è stata ritirata in favore della soglia più dura sopra.

### Numero di daemon

La v1.1 diceva "21 daemon in esecuzione". La v1.2 esegue:

- 21 daemon di polling (FIRMS, EUMETSAT, Sentinel-1, verifier Sentinel-2, SAR change, NISAR, TROPOMI, OroraTech, ANSA, news italiane, Reddit, Mastodon, joint Dozier, Hawkes, ecc.)
- 3 daemon di riproducibilità (grader eventi ogni 5 min, snapshot giornaliero, bootstrap notturno)
- + riconciliatore multi-stadio ogni 6 ore

Totale daemon di background attivi: 25+.

### Nuovi endpoint API

- `/api/event_grades?days=N[&tier=Tx][&led=phoenix|external]` — lista completa eventi valutati
- `/api/event_grades.csv` — stesso come CSV
- `/api/null_bootstrap` — distribuzione nulla per permutazione
- `/data/snapshots/` — indice degli snapshot giornalieri disponibili
- `/data/snapshots/<data>/` — elenco per una data
- `/data/snapshots/<data>/<file>` — CSV grezzo / README / SHA256SUMS

### Schema `event_grades` (v2.1 — DDL completo)

Le colonne aggiunte in v2.1 rispetto allo schema v1 stampato in Annex D del pack v1.1:

`comparator_class TEXT, comparator_panel TEXT (JSON), capable_comparator_count INTEGER,
worst_capable_lead_min REAL, race_strict INTEGER, below_comparator_floor INTEGER,
biome_class TEXT, dnbr_threshold_biome REAL, phoenix_had_coverage INTEGER,
refute_strength TEXT, t14d_outcome TEXT, t14d_outcome_evidence TEXT,
t14d_reconciled_at TEXT, t45d_outcome TEXT, t45d_outcome_evidence TEXT,
t45d_reconciled_at TEXT, wui_built_pct REAL, wui_class TEXT`

Il lead mediano sicily_full negativo (−98,9 min) **è ora calcolato solo sulle celle dove
`phoenix_had_coverage = 1`** — cioè dove PHOENIX aveva almeno un detector capace con
un'acquisizione valida nella finestra di confronto. Le celle dove PHOENIX non avrebbe
potuto vedere l'incendio sono escluse dalla mediana delle perdite.

### Rebuild della pagina pubblica (`/wins.html`)

- **Striscia profilo-sistema** in cima che mostra tutte e sette le categorie di outcome
  fianco a fianco a peso visivo uguale.
- **Banda di precisione** con intervallo Wilson 95% visualizzato: "Precisione resolved-set: 1,74% [0,97%–3,08%, n=634]"
  e la linea bootstrap distribuzione nulla appesa.
- **Chip per ogni sub-detector PHOENIX** con conteggi confirmed / refuted / pending / unverifiable / below-floor e Wilson 95% per detector. Stesso scoreboard che applichiamo ai comparator applicato a noi stessi.
- **Sezione "fonti autoritative" in cima**: unione di ogni incendio segnalato da VVF / FIRMS / EUMETSAT / SLSTR, con il contributo PHOENIX per riga (co-detected / missed-gap-algoritmico / missed-no-coverage).
- **Sezione Refuted aperta di default**, non più nascosta dietro un `<details>`.
- **Provenienza per riga**: link mappa 🛰️ FIRMS e 🌍 Copernicus Browser su ogni evento.
- **Toggle bilingue EN/IT** (in alto a destra) con dizionario i18n che copre nav, intro, legenda tier, intestazioni di sezione.
- **Etichette ARIA** sui badge tier, race-badge, ruoli di sezione.
- **Palette daltonica-sicura Wong-2011**: T0 grigio, T1 blu, T2 teal, T3 vermiglio (era T3 giallo, troppo vicino a T1 blu per deuteranopia).

### Cosa rimane uguale / ancora onesto

- Team volontario di due persone, nessun intento commerciale, nessuna richiesta di fondi. Invariato.
- Motivazione personale: incendio fatale residenziale 2025 ad Alessandria della Rocca. Invariato.
- Quattro scambi di dati richiesti all'INGV: catalogo termico Etna (priorità 1), meteo co-localizzato con stazioni sismiche, previsioni pennacchio di cenere, atlante storico interazione incendi–vulcano. Invariato.
- Dati CC-BY 4.0 + codice MIT; nessuna esclusività; attribuzione INGV su ogni allerta e pubblicazione che usa dati INGV. Invariato.
- La maschera di esclusione Etna di 15 km è ancora attiva; è il limite non-sbloccato-da-dati-INGV che speriamo questa collaborazione possa sollevare.

### Cosa la v1.2 ancora NON afferma

- Skill race-strict sopra la casualità. (Bootstrap p = 1,00; lo pubblichiamo.)
- Un tasso di FP verificati misurato sotto il 5%. (Soglia di gating dichiarata per i broadcast agli agricoltori; non ancora raggiunta.)
- Un archivio completo di burn-scar Sentinel-2 per ogni detection. (Molti incendi recenti non hanno ancora un passaggio S-2 post-fire; il daemon farà rollup man mano che i passaggi arrivano.)
- Campi operativi prossimità WUI / strada / idrante. (Parziale: classe WUI da WorldCover per le ~64 celle coperte; strada e idrante da OSM non ancora rilasciati.)

---

## Cosa l'INGV dovrebbe controllare su `/wins.html` e `/api/...` per verificare qualunque claim v1.2

| Claim | Come verificare |
|---|---|
| Race-strict = 0 | `curl https://adr-wildfire.com/api/null_bootstrap` — observed.race_strict |
| Conteggio daemon | `gunicorn_conf.py` su GitHub commit `eadb2ed` (o successivo) |
| Soglie dNBR biome | Costante `BIOME_DNBR` in `scripts/grade_events.py` |
| Verifier funzionante | `curl https://adr-wildfire.com/api/burn_verification` — cercare righe con dNBR non-null o `verified_via_sar=True` |
| Overflow FRP risolto | `SELECT MAX(frp_mw) FROM internal_fires WHERE source='subpixel_v1_alpha'` — dovrebbe essere < 10 |
| Riproducibilità | Scarica `https://adr-wildfire.com/data/snapshots/2026-05-26/`, esegui `scripts/regrade.py`, diff contro `event_grades.csv` pubblicato |

---

*Questo documento Updates fa parte del pacchetto PHOENIX–INGV v1.2. Complementa l'EXSUM v1.2 e il Pack Tecnico completo v1.2 (rigenerato via GitHub Actions; scarica `PHOENIX_INGV_Pack_IT.pdf` dall'artifact del workflow più recente). Per la metodologia canonica e il ragionamento dietro ogni scelta, vedi il Pack Tecnico v1.2.*
