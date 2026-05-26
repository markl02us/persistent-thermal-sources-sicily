# PHOENIX — Sistema di Rilevamento Incendi Boschivi per la Sicilia
## Briefing per Partenariato con INGV (Istituto Nazionale di Geofisica e Vulcanologia)

**Versione del documento:** 1.2 (sostituisce 1.1 del 25 maggio 2026)
**Data preparazione:** 26 maggio 2026 (revisione post-audit)
**Punto di contatto:** Gaetano Zambito — folderdj@gmail.com — +39 366 545 0598
**Casella di progetto:** adrwildfi@gmail.com
**Preparato da:** Team PHOENIX ADR (Alessandria della Rocca, Sicilia)
**Licenza:** CC-BY 4.0 (dati) / MIT (codice) — apertura totale al riuso accademico e alla citazione
**Sistema live:** https://adr-wildfire.com/
**Codice open-source:** https://github.com/markl02us/persistent-thermal-sources-sicily
**DOI del catalogo dei falsi positivi:** 10.5281/zenodo.20369891

---

# SINTESI ESECUTIVA

## Il punto centrale, in apertura

PHOENIX è un sistema multi-sensore per il rilevamento di incendi boschivi e di condizioni pre-incendio, attivo sull'intera Sicilia, gestito da un team di volontariato grassroots composto da due persone con base ad Alessandria della Rocca (provincia di Agrigento). Richiediamo una collaborazione operativa con l'INGV — non finanziamenti, non esclusività, non una partnership commerciale — costruita attorno a **quattro scambi di dati specifici** che né noi né l'INGV possiamo produrre da soli e che migliorano materialmente la qualità degli allerta incendio per gli agricoltori e i residenti rurali siciliani.

**Cosa chiediamo all'INGV:**

1. **Catalogo delle anomalie termiche dell'Etna** — Il catalogo continuo delle anomalie termiche rilevate dall'INGV sui crateri sommitali e sulle bocche di fianco. Oggi PHOENIX semplicemente maschera un raggio di 15 km attorno alla sommità dell'Etna come zona di esclusione perché non riusciamo a distinguere un vero incendio boschivo sui fianchi dell'Etna (che si verificano realmente in vegetazione di pino e ginestra) dal rumore termico vulcanico di fondo. La sorveglianza esistente dell'INGV distingue routinariamente questi segnali. Un feed in tempo reale o quasi (anche solo un dump JSON giornaliero di "ubicazioni delle bocche attive + classi di intensità") sbloccherebbe per la prima volta il rilevamento di veri incendi sull'Etna.

2. **Contesto fire-weather co-localizzato con le stazioni sismiche** — La rete sismica INGV include molte stazioni in terreno pyroclastico/fire-prone remoto. Le condizioni meteorologiche locali in quei siti (dove strumentati) aiuterebbero il nostro modello di prior di ignizione a fare qualcosa che non possiamo fare dai soli dati meteorologici a griglia.

3. **Previsioni di tephra e pennacchio di cenere** — Le previsioni INGV di dispersione della cenere vulcanica influenzano direttamente la nostra logica di rilevamento fumo. Un pennacchio vulcanico di cenere appare come fumo da incendio a un modello YOLO addestrato sul fumo di incendi boschivi. Pre-posizionare una prior "non classificare come fumo da incendio se la previsione di cenere copre quest'area" eliminerebbe una modalità significativa di falso positivo.

4. **Atlante storico di interazione incendio-vulcano** — Il record istituzionale INGV degli incendi innescati da colate laviche, eventi piroclastici e cambiamenti di infiammabilità dei depositi di cenere è unico al mondo. Non abbiamo equivalenti. Un dump in sola lettura (anche solo per 1980-2020) ancorerebbe le nostre prior stagionali e le nostre regole di valutazione degli eventi.

**Cosa offriamo all'INGV in cambio:**

- Accesso libero, aperto e in tempo reale all'intero flusso di dati PHOENIX (REST/STAC/RSS/CSV/GeoJSON) su adr-wildfire.com — già licenziato CC-BY 4.0.
- Pubblicazione peer-reviewed co-autorata quando i metodi congiunti raggiungeranno la maturità. Ricercatori INGV-Sicilia e INGV-Catania benvenuti come autori principali o co-principali.
- Uso gratuito del nostro compute DGX-class per analisi congiunte (attualmente 18+ demoni di polling live, pipeline SAR NISAR L-band con autenticazione NASA Earthdata, ingestione MTG-FCI / MTG-LI, fusione multi-satellite joint Dozier, verifica fumo YOLO, previsione di ignizione Hawkes).
- Catalogo citabile dei falsi positivi (DOI Zenodo 10.5281/zenodo.20369891) delle sorgenti termiche persistenti in Sicilia — già utile a chiunque lavori sul telerilevamento siciliano.
- **Riproducibilità totale (rilasciato 26-05-2026)**: snapshot pubblici giornalieri a `/data/snapshots/YYYY-MM-DD/` (input grezzi + grades pubblicati + checksum SHA-256), reproducer standalone `scripts/regrade.py` (verificato zero mismatch contro i grades pubblicati su 2.172 eventi), bootstrap di distribuzione nulla a `/api/null_bootstrap` (pubblichiamo la nostra stessa falsificazione — p-value attuale = 1,00 vs casuale), e intervallo di confidenza Wilson al 95% su ogni claim di precisione. L'INGV può fare audit end-to-end di qualunque numero su `/wins.html` dai pull grezzi FIRMS / EUMETSAT / VVF senza contattarci.

**Chi siamo, onestamente:**

PHOENIX è gestito da un piccolo team di volontariato, tutti con lavori a tempo pieno. Il rappresentante siciliano e punto di contatto per l'INGV è **Gaetano Zambito** — basato a Milano durante la settimana, rientra ad Alessandria della Rocca per pochi giorni al mese, attualmente sta completando la laurea universitaria. La corrispondenza di progetto è benvenuta alla sua email diretta (folderdj@gmail.com), al suo cellulare italiano (+39 366 545 0598), e alla casella di gruppo del progetto (adrwildfi@gmail.com). Il lato ingegneristico-tecnico del progetto — ingestione dati satellitari, infrastruttura AI/ML, e operazioni del compute di classe DGX — è guidato da un contributore tecnico ADR-affiliato separato; quel ruolo non viene volutamente nominato pubblicamente qui. Non siamo una startup, non abbiamo intenti commerciali, nessuna raccolta fondi, nessuna richiesta di esclusività. I costi (compute, internet, hardware dei sensori di terra) sono sostenuti personalmente dai membri del team ADR-affiliati come contributo alla comunità.

**Perché ora:**

Nell'anno appena trascorso, un incendio residenziale fatale ad Alessandria della Rocca ha tolto la vita a un residente e ha rischiato di causare ulteriori danni a causa di materiali pericolosi all'interno della casa. Riferimento: https://www.youtube.com/watch?v=kgDIhfthQJM. Alessandria della Rocca è una piccola comunità quasi interamente agricola dell'entroterra siciliano dove il rischio annuale di incendi boschivi minaccia sia vite che mezzi di sostentamento. La nostra motivazione è prevenire la ripetizione di tragedie — non la pubblicazione accademica, non il rilevamento commerciale-come-servizio, non la costruzione di un marchio. Stiamo pubblicando dati in modo pubblico e aperto in modo che tutti nella regione ne traggano beneficio, inclusi i ricercatori dell'INGV se utile a loro.

**Cosa consegnamo:**

- Un sistema PHOENIX live già in funzione 24/7 con 18+ demoni di dati satellitari e citizen-data attivi, che copre l'intera Sicilia e un AOI specifico di Alessandria della Rocca + Agrigento.
- Una roadmap che copre sensori di terra (telecamere PTZ + telecamere ad ampio angolo + hub LoRa su hardware Pi 5 + Hailo-8 26 TOPS), sensori di incendio domestico via LoRa per allerta precoce residenziale (motivati dall'incendio fatale citato sopra), e un sensore VOC pre-ignizione sperimentale montato su pali.
- Un piano di consegna a 12 / 24 / 36 mesi che rispetteremo nonostante il vincolo dei due-persone, perché abbiamo già consegnato il sistema centrale.
- Un changelog pubblico su adr-wildfire.com per ogni modifica ad algoritmi, soglie o maschere — con la motivazione pubblicata accanto alla modifica. Ritrattazioni pubbliche quando scopriamo di aver pubblicato dati che si sono rivelati errati.

**Perché questa proposta — non un contratto o un RFP:**

Operiamo come un progetto grassroots di civic-tech. L'INGV è un'istituzione scientifica seria. Vi approcciamo con la convinzione che, anche alla nostra scala limitata, abbiamo **già costruito e reso pubblica** una sostanziale capacità di rilevamento incendi per la Sicilia che è onesta sui propri limiti e che qualsiasi ricercatore sicilia-centrico potrebbe trovare utile. Speriamo che la troviate abbastanza interessante per condividere con noi quattro specifiche tipologie di dati. Se sì: diteci di cosa avete bisogno dalla nostra parte. Se no: il sistema rimane attivo e utile per tutti indipendentemente.

---



<p align="center">
  <img src="../assets/maps/sicily_aoi_overview.png" alt="Area operativa siciliana di PHOENIX — AOI (sicily_full, agrigento), città principali, centri vulcanici (maschera di esclusione Etna 15 km), e l'incendio confermato non rilevato di Alessandria della Rocca del 24-05-2026." style="max-width:100%;"/>
</p>
<p align="center"><em>Figura: Area operativa siciliana di PHOENIX — AOI (sicily_full, agrigento), città principali, centri vulcanici (maschera di esclusione Etna 15 km), e l'incendio confermato non rilevato di Alessandria della Rocca del 24-05-2026.</em></p>

## Tabelle riassuntive di sintesi

### Attualmente live (validato al 25 maggio 2026)

| Componente | Stato | Note |
|---|---|---|
| Servizio web di produzione | LIVE | https://adr-wildfire.com/, HTTP 200 in 0,4 s, gunicorn su DGX, Tailscale |
| Demoni di dati satellitari | 21 attivi | FIRMS (4 piattaforme), MTG-FCI, MTG-AF-L2, MTG-LI, SLSTR FRP, verificatore di cicatrici Sentinel-2, rilevamento di cambiamento SAR Sentinel-1 (rilasciato 25-05-2026), NISAR L-band SAR (rilasciato 25-05-2026), TROPOMI HCHO, OSINT pubblico OroraTech (rilasciato 25-05-2026), worldcover, modis_viirs_sar, telecamere meteo, CEMS EFFIS RDA, notizie ANSA, RSS notizie italiane, Reddit + Mastodon, verificatore fumo YOLO, joint Dozier (FCI+SLSTR+S-2), previsione ignizione Hawkes |
| Livello di verifica | LIVE (attualmente degradato) | Verificatore di cicatrici dNBR Sentinel-2 (rotto, in attesa di fix P0.1; tutti gli 82 tentativi recenti restituiti nulli con HTTP 400 — fix preparato, vedere Sezione 8) |
| Valutazione eventi a tier | LIVE (v2.1) | T0/T1/T2/T3 + race-strict (lead < 50% della rivisita) + race-marginal (lead entro la rivisita ma ≥ 50%) + "first vs VVF/news*" + riconciliazione multi-stadio (T+72h → T+14g → T+45g) + soglie dNBR biome-aware (0,12 erba / 0,18 macchia / 0,27 foresta via ESA WorldCover) + classe WUI (U/I/W/A) + flag below-comparator-floor + comparator panel JSON. 2.287 eventi valutati al 26-05-2026. |
| Registro ground-truth | IN INIZIO | Primo caso di mancato rilevamento confermato registrato: incendio ADR 25-05-2024 a (37,562278°N, 13,440250°E) |
| Catalogo FP persistenti | LIVE | 18 zone (sommità Etna, raffineria di Gela, industriale Augusta-Priolo-Melilli, Termini, Milazzo, Catania, Stromboli, complessi di serre, siti minerari, parchi solari). Citabile come Zenodo 10.5281/zenodo.20369891 |
| API pubblica | LIVE | Specifica OpenAPI 3.1 su /api/openapi.json. Endpoint JSON / CSV / GeoJSON / RSS / iCal. CC-BY 4.0. |
| Repo GitHub | LIVE | https://github.com/markl02us/persistent-thermal-sources-sicily — v1.0.0 taggata |
| Compute DGX | LIVE | Elaborazione live 4-stream, modalità detector NISAR con autenticazione NASA Earthdata attiva |

### Roadmap a 12 mesi (impegni fermi)

| Milestone | Target | Stato |
|---|---|---|
| Fix di disciplina della verità P0 (verificatore di cicatrici + gate FRP + scoring a livello evento + source health simmetrico + lead headline vs sensed) | Deploy nelle prossime due settimane | Bundle di codice preparato offline, in attesa di finestra di deploy sicura |
| Watcher di mancato rilevamento confermato live | Stesso deploy | Codice pronto, monitora STAC Sentinel-2 per scene post-incendio sui mancati rilevamenti registrati |
| Demone di scoperta proattiva di cicatrici Sentinel-2 | +60 giorni | Inverso del verificatore attuale — segnala cicatrici su ogni passaggio S-2 chiaro sulla Sicilia, non solo dove PHOENIX ha già rilevato |
| Rilevamento attivo di incendi Sentinel-2 SWIR Banda 12 | +90 giorni | Cattura incendi attivi durante la finestra del passaggio S-2 |
| Ingestione Landsat 8/9 | +120 giorni | Rivisita combinata di 8 giorni + bande termiche a 100 m |
| Sentinel-1 dual-pol VH+VV + baseline rolling 14-giorni | +150 giorni | Per metodologia peer-reviewed Sardegna / Sicilia (Imperatore 2017, Mastro 2022) |
| Ingestione opportunistica Capella + Umbra Open Data (X-band) | +180 giorni | Risoluzione sub-metrica per validazione case-study post-incendio |
| Primo sensore di terra ADR dispiegato sulla torre FM Amica Radio | +180 giorni | Hardware acquisito, MoU in corso |
| Sensore incendio domestico via LoRa, prime 5 unità in residenze di Alessandria della Rocca | +270 giorni | Motivato dall'incendio fatale |
| Decisione di viabilità del sensore VOC pre-ignizione montato su palo | +365 giorni | Fase di ricerca |

### Costi ricorrenti (interamente a carico degli sviluppatori ADR, mai addebitati all'INGV o a chiunque altro)

| Voce | Costo annuo (USD) | Note |
|---|---|---|
| Potenza compute DGX-class + elettricità | Non fatturato separatamente — gestito dal proprietario | Host spark-b0c1, agganciato a Tailscale |
| Banda Internet per l'ingestione dati satellitari | A carico del proprietario | ~3-5 TB/anno di pulls Copernicus / MPC / EUMETSAT |
| Registrazione dominio (adr-wildfire.com) | ~15 USD | |
| Proxying / DNS / TLS Cloudflare | 0 — tier gratuito | |
| NASA Earthdata Login | 0 — self-serve gratuito | Attivo per ingestione NISAR L-band SAR |
| Copernicus Data Space (CDSE) | 0 — gratuito | |
| Accesso dati EUMETSAT | 0 — gratuito | |
| Microsoft Planetary Computer | 0 — letture anonime gratuite + accesso blob firmato SAS | |
| API FIRMS | 0 — gratuito | |
| Per sensore di terra ADR (capex) | ~8.290 USD/sito | Pi 5 + Hailo-8 (26 TOPS) + Hikvision PTZ 32× + Reolink ad ampio angolo + LiteBeam AREDN ch177 5835 MHz + RAK4631 LoRa + kit di alimentazione solare; 3-4 siti pianificati totali |
| Per sensore incendio domestico LoRa (capex) | TBD (stimato ~80-150 USD/unità) | Specifiche non ancora finalizzate |
| Sensore VOC pre-ignizione montato su palo | TBD (fase di ricerca, ~200-400 USD/unità se viabile) | Attualmente in valutazione MQ-series vs PID vs MOX cost-per-detection-range |

**All'INGV non viene chiesto alcun contributo finanziario.** Il costo ricorrente è di proprietà personale degli sviluppatori ADR come contributo alla comunità.

---

## Limiti onesti — cosa PHOENIX NON è

Prima di descrivere cosa è PHOENIX, ecco cosa non è:

1. **Non è ancora un sistema di allerta primario.** Oggi PHOENIX è una piattaforma di ricerca/osservazione con una dashboard pubblica. Non trasmette ancora direttamente agli agricoltori via SMS / WhatsApp / Telegram. Quella capacità è in roadmap (+270 giorni) ma **esplicitamente vincolata al raggiungimento di un tasso misurato di falsi positivi verificati inferiore al 5% rispetto a una baseline confermata da cicatrici di bruciatura**. Non diventeremo il sistema che grida "al lupo, al lupo" e viene poi ignorato.

2. **Non sta ancora battendo tutti i comparator su tutti gli AOI.** Un audit interno di maggio 2026 ha trovato che PHOENIX ha un lead time mediano positivo di +107,6 minuti rispetto ai comparator nell'AOI di Agrigento ma un lead mediano *negativo* di −98,9 minuti nell'AOI più ampio sicily_full. Il grader è stato aggiornato alla v2.1 con una definizione di race-validity più rigorosa. Sotto la soglia **race-strict** (anticipo PHOENIX > 0 AND lead < 50% della rivisita del comparator satellitare AND ≥ 1 comparator capace AND non sotto la soglia di rilevazione), abbiamo **0 vittorie negli ultimi 30 giorni**. Un bootstrap di distribuzione nulla per permutazione (200 repliche, shift dei timestamp ±24 h) restituisce media nulla = 12,7 / p-value = 1,00 — cioè sotto la soglia più rigorosa attualmente NON siamo statisticamente distinguibili dal caso, e lo pubblichiamo apertamente a `https://adr-wildfire.com/api/null_bootstrap`. Sotto la soglia race-valid più permissiva (lead entro 100% della rivisita), abbiamo **2 eventi PHOENIX-first negli ultimi 7 giorni**: un anticipo di +9,1 min vs EUMETSAT MTG-AF-L2 il 2026-05-24 (T1, race-marginal*) e un anticipo di +9,4 min vs Vigili del Fuoco il 2026-05-25 (T2, "first vs VVF*" — comparator a dispatch umano, non eligibile per race-strict perché non è una cadenza satellitare). Entrambi mostrati con asterischi espliciti su `/wins.html`. Il lavoro per chiudere il gap algoritmico sicily_full è nella roadmap P0/P1.

3. **Non sta attualmente facendo elaborazione real-time pixel-rate.** La maggior parte del nostro lavoro gira su cicli di polling di 10 minuti (FCI / MTG-AF-L2), 15 minuti (MTG-LI, notizie ANSA), 30 minuti (SLSTR, ARPA aria, fumo YOLO), orari (CEMS EFFIS) o più lunghi (Sentinel-2 6 ore, Sentinel-1 12 ore, NISAR 24 ore). Non generiamo allerta a millisecondi. Il rilevamento incendi a questa scala non ne ha bisogno; riconosciamo che un sistema di vigili del fuoco in real-time vero lo richiederebbe.

4. **Non commerciale, non academic-publishing-first, non una startup.** Non pianifichiamo di monetizzare PHOENIX. Non abbiamo affiliazione istituzionale. Siamo contributori individuali che hanno costruito e gestiscono questo sistema.

5. *(Disclosure v1.1 risolta — vedi #7 per il fix del verifier Sentinel-2 rilasciato il 26-05-2026.)*

6. **Overflow FRP subpixel_v1_alpha — RISOLTO 26-05-2026.** Una scoperta dell'audit v1.1 aveva flaggato valori di potenza radiativa fino a 3,9 petawatt (fisicamente impossibili) a causa di un bug di conversione di unità o overflow. Al 26-05-2026, la distribuzione FRP di questa fonte è sana: max = 9,09 MW, media = 2,73 MW, n = 5.524, zero outlier sopra 10 GW. Il fix è in produzione e la disclosure è aggiornata.

7. **Verifier burn-scar Sentinel-2 — RISOLTO 26-05-2026.** Una scoperta dell'audit v1.1 diceva che il verifier restituiva HTTP 400 ad ogni chiamata per un bug di formato STAC del Microsoft Planetary Computer. Tre bug indipendenti si sovrapponevano: (a) `detection_ts.isoformat() + "Z"` produceva un doppio timezone malformato RFC-3339 per datetime timezone-aware; (b) le letture COG delle bande ricevevano HTTP 409 perché i blob MPC Sentinel-2 L2A richiedono URL firmati SAS; (c) il broadcasting NBR falliva perché B8 (10 m) e B12 (20 m) tornano con shape diversi. Tutti e tre fissati (commit GitHub `eadb2ed`). Smoke test end-to-end sulla detection ADR del 26 aprile: pre_NBR = 0,3072, post_NBR = 0,3423, dNBR = −0,0351, verified_burn = False (corretto — quella detection era un FP noto). Primo burn-confermato-via-SAR-fallback già pubblicato (det_id 16802, 2026-05-25 fci_l1c a 37,689°N 12,743°E).

Divulghiamo queste cose nelle prime sei pagine di questo documento perché l'alternativa è che l'INGV le scopra fra sei mesi e concluda che non eravamo trasparenti. La trasparenza è parte di come definiamo l'essere affidabili.

---

---

*Questa è solo la Sintesi Esecutiva. Il pacchetto tecnico completo — che copre l'architettura attuale (Sezione 2), l'esempio elaborato del 24-05-2026 (Sezione 3), la roadmap dei sensori di terra (Sezione 4), i quattro scambi di dati INGV (Sezione 5), la capacità AI/ML (Sezione 6), costo / schedule / performance con Gantt completo (Sezione 7), limiti onesti e il bundle di deploy P0 preparato (Sezione 8), e le appendici inclusi BOM, link budget, schemi SQL, e il template MoU (Sezione 9) — è in `PHOENIX_INGV_Pack_IT.pdf` e nel suo compagno inglese.*