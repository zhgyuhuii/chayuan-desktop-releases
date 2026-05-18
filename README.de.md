<div align="center">

<img src="chayuan-client/images/logo.png" alt="Chayuan AI" width="120" height="120" />

# Chayuan AI · Desktop (Single-Machine Edition)

**Offline-First · Bereit für Chinesische Heimat-OS · Vollständige Lokale Wissensbasis · Multi-Model-Arena**

[![Tauri 2](https://img.shields.io/badge/Tauri-2-24c8db?logo=tauri&logoColor=white)](https://tauri.app/)
[![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

</div>

---

## Sprachen / Languages

| Sprache | Dokument |
|----------|----------|
| 简体中文 (Hauptdokument) | [README.md](README.md) |
| English | [README.en.md](README.en.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| **Deutsch (diese Datei)** | **[README.de.md](README.de.md)** |
| Français | [README.fr.md](README.fr.md) |

Detaillierte Build-Anleitung: [PACKAGING.md](PACKAGING.md)

---

## In einem Satz

**Chayuan AI Desktop (Single-Machine Edition)** ist ein KI-Assistent, der **per Klick installiert wird und ohne Internetverbindung läuft** —
mit eingebettetem **Multi-Vendor-LLM-Gateway**, **fünf Wissensquellentypen** (Dokumente / Strukturiert / Vektor / Office / Extern),
**30+ eingebauten Werkzeugen**, **MCP-Protokoll**-Unterstützung und **Multi-Model-Arena**.
Läuft nativ auf **Windows / macOS / Linux** und der **chinesischen Heimat-OS-Familie** (Kylin, UnionTech UOS, openKylin, deepin).
Alle Daten verbleiben in einem vom Benutzer gewählten Verzeichnis — **API-Schlüssel und Dokumente verlassen niemals das Gerät**.

---

## Inhaltsverzeichnis

- [1. Lizenz (AGPL-3.0)](#1-lizenz-agpl-30)
- [2. Markenidentität bewahren](#2-markenidentität-bewahren)
- [3. Beziehung zu chayuan-wps (WPS-Add-in)](#3-beziehung-zu-chayuan-wps-wps-add-in)
- [4. Produktübersicht und Systemarchitektur](#4-produktübersicht-und-systemarchitektur)
- [5. Kernfunktionen](#5-kernfunktionen)
- [6. Unterstützte Betriebssysteme](#6-unterstützte-betriebssysteme)
- [7. 60+ Punkte Vergleich vs. Doubao / Cherry Studio / etc.](#7-60-punkte-vergleich-vs-doubao--cherry-studio--etc)
- [8. Detaillierte Funktionsliste](#8-detaillierte-funktionsliste)
- [9. HTTP-API-Übersicht](#9-http-api-übersicht)
- [10. Entwickler-Setup](#10-entwickler-setup)
- [11. Tutorial-Einstiegspunkte](#11-tutorial-einstiegspunkte)
- [12. Sicherheit · Datenschutz · Offline](#12-sicherheit--datenschutz--offline)
- [13. Roadmap](#13-roadmap)
- [14. Community / Feedback / Kommerziell](#14-community--feedback--kommerziell)
- [15. Danksagungen](#15-danksagungen)
- [Anhang A: FAQ](#anhang-a-faq)
- [Anhang B: Glossar](#anhang-b-glossar)

---

## 1. Lizenz (AGPL-3.0)

Dieses Repository wird unter der **[GNU Affero General Public License v3.0](LICENSE)** veröffentlicht.

> Der Hauptunterschied zu Apache-2.0 / MIT ist die **§13 Netzwerkverbreitungsklausel**:
> Wenn Sie eine modifizierte Version dieser Software als Netzwerkdienst für **externe Nutzer** anbieten (SaaS, öffentliche Cloud usw.),
> **müssen** Sie diesen Nutzern den entsprechenden Quellcode (einschließlich Ihrer Modifikationen) unter derselben AGPL-3.0-Lizenz zur Verfügung stellen.
> Bei **interner Nutzung, Intranet-Bereitstellung oder lokaler Einzelmaschinen-Nutzung** wird die Quellcode-Offenlegungspflicht nicht ausgelöst.

**Praktisch bedeutet das:**
- ✅ Persönliche / Team- / Unternehmens-interne Bereitstellung, Intranet, lokale Modifikationen: **frei, keine kommerzielle Beschränkung**
- ✅ Modifizierte Installer weitergeben (mit AGPL- und Copyright-Hinweis): **erlaubt**
- ⚠ Modifizierte Version als **SaaS / öffentlichen Internetdienst** bereitstellen: **muss Modifikationen offenlegen**
- ⚠ In Ihr proprietäres kommerzielles Produkt einbinden und weiterverkaufen: **erfordert separate kommerzielle Lizenz**

Für **kommerzielle Lizenz / OEM-White-Label / kostenpflichtige Support-Verträge** kontaktieren Sie uns über [https://aidooo.com](https://aidooo.com).

### Urheberrecht

Produkt entwickelt und betrieben von **Beijing Zhilingniao Technology Center (北京智灵鸟科技中心)**. UI-Strings, Standard-Prompt-Templates, Symbole und Markenmaterial sind urheberrechtlich geschützt, mit Ausnahme von Drittanbieter-Komponenten unter eigenen Lizenzen.

**Haftungsausschluss:** Diese Software wird "wie sie ist" bereitgestellt. LLM-generierte Inhalte können ungenau oder unanwendbar sein; vertrauliche, regulatorische und rechtliche Entscheidungen sollten menschlichem Urteil und formellen Verfahren folgen.

---

## 2. Markenidentität bewahren

Zur Wahrung der Nutzertransparenz und Quellenrückverfolgbarkeit ist die Marke **"察元" (Chayuan)** und ihre festen Produktbezeichnungen ("察元 AI", "察元智库", "察元对抗" usw.) in der Endbenutzeroberfläche ein **wesentlicher Bestandteil der Produktherkunft und Markenidentifikation**:

- App-Fenstertitel, Splash-Screen, About-Dialog, Einstellungen → About
- Systray-Tooltip, Desktop-Verknüpfungsname (standardmäßig: `察元AI.lnk`)
- Marken-Texte in Help-Center / Feedback-Dialog

**Ohne vorherige schriftliche Genehmigung dürfen Weiterverbreitungen oder maßgeschneiderte Builds die obigen "Chayuan"-Markentexte nicht ersetzen, entfernen, verbergen, verwässern oder irreführend umschreiben.**

Diese Beschränkung verbietet **nicht** die unter AGPL-3.0 erlaubte Quellcode-Modifikation. Für **White-Labeling / vollständige Lokalisierung** mit Markenbezug ist eine **separate Genehmigung** über den Geschäftskanal einzuholen.

---

## 3. Beziehung zu chayuan-wps (WPS-Add-in)

Chayuan AI ist eine **End-to-End-Office-KI-Plattform**, derzeit über zwei sich ergänzende Open-Source-Projekte:

| Projekt | Repository | Form | Hauptnutzer |
|---|---|---|---|
| **chayuan-desktop** (dieses Repo) | (intern) | **Desktop Single-Machine App** — Tauri 2 Shell + eingebettetes Python-Backend | Einzelpersonen & Unternehmen mit Local-Only-KI-Anforderung |
| **chayuan-wps** | <https://github.com/zhgyuhuii/chayuan.git> | **WPS Writer Add-in** — Vue 3 Add-in, läuft in WPS | Behörden- / Unternehmensautoren mit hoher WPS-Nutzung |

### Wie sie zusammenarbeiten

```
              ┌─────────────────────────────────────────────┐
              │   Chayuan AI Desktop (chayuan-desktop)      │
              │   • Eingebetteter chayuan-server (Python)   │
              │   • KB / Modell-Gateway / Tools / MCP       │
              │   • Lauscht auf 127.0.0.1:62581             │
              └─────────────────────┬───────────────────────┘
                                    │  HTTP/REST
                                    ▼
              ┌─────────────────────────────────────────────┐
              │   chayuan-wps  (WPS Add-in, Vue 3 + Vite)   │
              │   • Läuft in WPS Writer                     │
              │   • Selektion / Volldokument-Kontext        │
              │   • Rückschreiben: Einfügen/Ersetzen/Kommt. │
              └─────────────────────────────────────────────┘
```

**Typisches Setup**: Installieren Sie **chayuan-desktop** als lokalen "KI-Server" → Sidecar läuft auf `127.0.0.1:62581`. Installieren Sie **chayuan-wps** in WPS → "Server-URL" auf `http://127.0.0.1:62581` setzen. Beide teilen **dieselbe Wissensbasis, Modellkonfiguration und Konversationshistorie**. WPS-Add-in v3.0+ unterstützt `authMode: 'none'` für schlüssellose lokale Verbindung.

---

## 4. Produktübersicht und Systemarchitektur

### 4.1 Was es ist

Chayuan AI Desktop packt ein **KI-Backend in Unternehmens-Qualität** in Ihren lokalen Rechner — installieren und nutzen, kein Docker, keine separate Python-Installation, kein Redis / RabbitMQ / PostgreSQL nötig. Alle anspruchsvollen Aufgaben (LLM-Aufrufe, KB-Indexierung, Vektor-Retrieval, Tool-Ausführung, Streaming-Orchestrierung) laufen in lokalen Prozessen.

### 4.2 Drei-Schichten-Architektur

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend  Tauri 2 + React 19 + Tailwind                          │
│  • Browser-artige Multi-Tab-Shell                                 │
│  • Multi-Lane Model Arena                                         │
│  • Aufklappbare Tool-Calls / Zitatpanel / Streaming-Reasoning     │
│  • Lokale SQLite-Konversationspersistenz                          │
│  • Stronghold-Anmeldetresor (ChaCha20-Poly1305 + Argon2id)        │
└──────────────────────┬───────────────────────────────────────────┘
                       │  spawn + /healthz Probe
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Sidecar  chayuan-server (Python 3.12, FastAPI)                   │
│  • Single-Machine-Profil: kein Redis / Celery / PostgreSQL        │
│  • Vektorspeicher: sqlite-vec (eingebettete SQLite-Erweiterung)   │
│  • Cache: cachetools / Queue: asyncio.Queue                       │
│  • Embeddings: ONNX lokal (Standard bge-m3-onnx)                  │
│  • OCR: RapidOCR-ONNX (nur CPU, ~70 MB)                           │
└──────────────────────┬───────────────────────────────────────────┘
                       │  HTTP / OpenAI-kompatibel
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Extern  Vom Benutzer gewählte Modelle und Datenquellen           │
│  • Lokal: Ollama / LM Studio / vLLM / Xinference                  │
│  • Cloud-LLM: OpenAI / DeepSeek / Qwen / Zhipu / Wenxin / Kimi…   │
│  • DBs: MySQL / PostgreSQL / Oracle / DM / KingbaseES…            │
│  • Externe Vektoren: Milvus / Chroma / Elasticsearch / Zilliz     │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 Startsequenz

1. Doppelklick auf das **察元AI**-Desktop-Symbol
2. Tauri-Hauptfenster erscheint sofort (`backgroundColor = #0f172a`); **erstes Frame zeigt die Splash-Animation** (5-Schichten-Zero-Latency-Mount)
3. Tauri spawnt eingebetteten `chayuan-server`-Subprozess mit `CHAYUAN_ROOT=<Benutzerverzeichnis>`
4. Frontend-`SidecarGate` pollt `/healthz`; Haupt-UI rendert nach Backend-Bereitschaft
5. Erstinstallations-Wizard für Datenverzeichnis-Auswahl

---

## 5. Kernfunktionen

### 5.1 Offline-First

- **Eingebettetes Backend**: Python-Interpreter + alle Wheels + sqlite-vec-Erweiterung + Ressourcen im Installer
- **Eingebettete Modelle**: ONNX-quantisiertes bge-m3 Embedding (~120 MB)
- **Eingebettetes OCR**: RapidOCR-ONNX-Gewichte
- **Optionales lokales LLM**: Ein-Klick-Integration mit Ollama / LM Studio / vLLM / Xinference
- **Doctor-Befehl**: Selbstdiagnose

### 5.2 Knowledge Universe (察元智库)

> **Vereinheitlichter Abfragepfad** — Dokumente, strukturierte DBs, externe Vektor-Collections, Office-private KBs und Bild-KBs werden als `ku_id` abstrahiert.

- **`POST /api/v1/kb-query/search`** — einzelner Endpoint für Multi-Source-Hybrid-Retrieval
- Fünf Quellenarten: `doc:`, `src:`, `vec:`, `office:`, `img:`
- Vereinheitlichtes `RetrievalChunk` + `Citation`-Rückgabeformat
- **Hybrid-Retrieval**: Vektor + BM25 parallel
- **Reranker**: optional BAAI/bge-reranker-v2-m3
- **Diagnostik**: `Diagnostic[]` zur Fehlersuche

### 5.3 Chayuan Chat

- **Browser-style Multi-Tab-Shell**
- **Streaming-Markdown-Rendering** mit Shiki-Syntax-Highlighting + Reasoning-Token-Faltung
- **3-Schichten Tool-Call-Anzeige** (Summary → Args/Output → Voll-JSON)
- **Zitatpanel**: KB-Quellen + Vertrauenssterne + Originaldokument öffnen
- **Anhänge**: Drag/Paste/Klick mit Auto-OCR
- **Lokale Konversationspersistenz**

### 5.4 Multi-Model Arena

- **N Lanes ohne Obergrenze**, jede Lane wählt eigenes Modell
- **Unified Send**: Tippen in einer Lane wird an alle Lanes parallel gesendet
- **Lane-Operationen**: Falten / Größe ändern / Drag-Reorder / Hinzufügen / Entfernen
- **Lane-Titel im gefalteten Zustand** zeigt automatisch die **erste Benutzerfrage** als vertikale Beschriftung

### 5.5 Modell-Marktplatz

- 7 Kategorie-Tabs: **Empfohlen / Alle / Lokal / Inland / International / Aggregiert / Benutzerdefiniert**
- **Auto-Abruf der Modellliste** beim Eingeben des API-Keys
- **Auto-Promote des Default-Modells** pro Fähigkeit

---

## 6. Unterstützte Betriebssysteme

| Kategorie | OS | Architektur | Status |
|---|---|---|---|
| **Windows** | 10 (1809+) / 11 | x86_64 | ✅ Voll |
| **macOS** | 11 (Big Sur)+ | Apple Silicon (arm64) / Intel (x86_64) | ✅ Voll |
| **Linux** | Ubuntu 22.04+ / Debian 12+ | x86_64 / aarch64 | ✅ Voll |
| **Heimat-Linux** | Kylin V10 (麒麟) | x86_64 / aarch64 / LoongArch64 | ✅ Kompatibel |
| **Heimat-Linux** | UnionTech UOS (统信) | x86_64 / aarch64 | ✅ Kompatibel |
| **Heimat-Linux** | openKylin | x86_64 / aarch64 | ✅ Kompatibel |
| **Heimat-Linux** | deepin | x86_64 | ✅ Kompatibel |
| **Heimat-Linux** | openEuler | x86_64 / aarch64 | ⚠ RPM-Bundle |

**Heimat-Ökosystem**: Heimische DBs (DM / KingbaseES / Doris), heimische LLMs (DeepSeek / Qwen / Zhipu / Wenxin / Kimi / Doubao / SiliconFlow / Baichuan / MiniMax), heimisches OCR (RapidOCR-ONNX), heimisches Embedding (bge-m3-onnx).

---

## 7. 60+ Punkte Vergleich vs. Doubao / Cherry Studio / etc.

> Vergleich basiert auf öffentlich bekannten Funktionsumfängen Stand **2026-05**. Preise, Datenstandort, Enterprise-Bereitstellung und regulatorische Zertifizierungen unterliegen den **offiziellen Hersteller-Dokumentationen**.

### 7.1 Namentlicher Produktvergleich (Auszug)

| Punkt | **Chayuan AI Desktop** | Doubao Desktop | Cherry Studio | ChatGPT Desktop | LM Studio | Open WebUI | AnythingLLM | Chatbox |
|---|---|---|---|---|---|---|---|---|
| Form | Tauri + eingebettetes Python (vollständig lokal) | ByteDance-Cloud-gebunden | Electron Multi-Vendor | OpenAI offiziell | Electron lokale Inferenz | Web UI | Electron + Node | Tauri Cross-Platform |
| Offline-LLM | ✅ Ollama/LM Studio/vLLM/Xinference | ❌ | ✅ teilweise | ❌ | ✅ Hauptzweck | ✅ mit Ollama | ✅ teilweise | ✅ teilweise |
| Lokale KB (RAG) | ✅ 5 Typen, eingebettetes sqlite-vec | ✅ aber Cloud-geroutet | ✅ einzeln (Vektor) | ❌ | ❌ | ✅ extern | ✅ einzeln | teilweise |
| DB-Konnektoren (text2sql) | ✅ 17 Dialekte | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Externe Vektor-Konnektoren | ✅ Milvus/Chroma/ES/Zilliz | ❌ | ❌ | ❌ | ❌ | teilweise | teilweise | ❌ |
| MCP-Protokoll | ✅ stdio + sse, beide Rollen | ❌ | ✅ Client | ❌ | ❌ | teilweise | ❌ | teilweise |
| Eingebaute Tools | **30+** | wenige | wenige | OpenAI-Plugins | ❌ | ❌ | wenige | ❌ |
| Multi-Model-Arena | ✅ unbegrenzt + Unified Send | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Heimat-Ökosystem (CN) | ✅ Kylin/UOS + DM/Doris + CN-LLMs | ✅ ByteDance-Stack | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Datenstandort | **Lokales Benutzerverzeichnis** | ByteDance-Cloud | Lokal + Clouds | OpenAI-Cloud | Lokal | Self-hosted | Lokal | Lokal |
| Lizenz | **AGPL-3.0** | Closed | Apache-2.0 | Closed | Closed | MIT | MIT | GPL-3.0 |

### 7.2 60-Punkte-Detailvergleich (Chayuan AI vs. typischer Desktop-KI-Client)

| # | Dimension | Chayuan AI Desktop | Typischer Desktop-KI-Client |
|---:|---|---|---|
| 1 | Installer-Form | Tauri-Native + eingebettetes Python | Electron / Browser-Shell |
| 2 | Installer-Größe | Mittel (Python-Runtime + Standardmodelle) | Klein (nur Frontend) |
| 3 | Internet beim Start nötig | ❌ | ✅ meist |
| 4 | Splash-Animation | 5-Schichten Zero-Latency | meist vorhanden |
| 5 | Eingebetteter Vektorspeicher | ✅ sqlite-vec | ❌ extern |
| 6 | Eingebettetes Embedding | ✅ ONNX bge-m3 | ❌ Benutzer-konfiguriert |
| 7 | Eingebettetes OCR | ✅ RapidOCR-ONNX | ❌ |
| 8 | Eingebettetes TTS / ASR | ✅ Piper / FunASR (optional) | ❌ |
| 9 | Dokument-RAG-Umfang | ✅ PDF/Word/Excel/PPT/MD/HTML/Bild | begrenzt |
| 10 | Multi-Vendor-Gateway | ✅ 18+ Anbieter | 5–10 |
| 11 | OpenAI-kompatible Routen | ✅ eingebaut `/openai/v1/*` | teilweise |
| 12 | Auto-Detect-Modelle | ✅ Key→Blur→`/v1/models` | teilweise |
| 13 | Auto-Promote Default-Modell pro Fähigkeit | ✅ chat/embed/image/rerank | ❌ |
| 14 | Modellkategorien | chat/embed/vision/image-gen/rerank/audio/video | meist chat + embed |
| 15 | Multi-Model-Arena | ✅ unbegrenzt + Unified Send | ❌ |
| 16 | 3-Schichten Tool-Call-Anzeige | ✅ | ❌ |
| 17 | Streaming-Reasoning-Faltung | ✅ | ❌ |
| 18 | Zitat-Chips (KB-Quellen) | ✅ Vertrauen + 1-Klick | teilweise |
| 19 | Zitate nach Quelltyp | ✅ doc/struct/vec/office/web | ❌ |
| 20 | text2sql-Sicherheit | ✅ Read-only AST + Whitelist | ❌ |
| 21 | Heimat-DB-Unterstützung | ✅ DM / KingbaseES / Doris | ❌ |
| 22 | Internationale DB-Unterstützung | ✅ MySQL/PG/Oracle/SQL Server/ClickHouse/Hive | meist 0–1 |
| 23 | MongoDB-Konnektor | ✅ | ❌ |
| 24 | Elasticsearch-Konnektor | ✅ | teilweise |
| 25 | Externe Vektor-Konnektoren | ✅ Milvus/Chroma/Zilliz/PG-vector/ES | ❌ oder 1 |
| 26 | MCP-Client | ✅ stdio + sse | teilweise |
| 27 | MCP-Server | ✅ selbst MCP-Server | ❌ |
| 28 | Eingebaute Tool-Anzahl | 30+ | 0–10 |
| 29 | Custom-HTTP-Tool (OpenAPI) | ✅ Swagger-Parsing | teilweise |
| 30 | Custom-Skript-Tool | ✅ Python REPL / Shell | teilweise |
| 31 | KB-Typen | 5 (doc/struct/vec/office/img) | meist 1 |
| 32 | Dokumentformate | PDF/Word/Excel/PPT/MD/HTML/Bild/CSV | meist PDF + Word |
| 33 | Inkrementelle Ordnersynchronisation | ✅ folder-sync-Route | teilweise |
| 34 | Auto-Aufbau Wissensstruktur | ✅ Watch + Parse + Ingest | ❌ |
| 35 | Multi-KB-Parallel-Retrieval | ✅ `selectedKuIds` | teilweise |
| 36 | Heimat-OS-Unterstützung | ✅ Kylin / UOS / openKylin / deepin | ❌ |
| 37 | Multi-Architektur | x86_64 / aarch64 / loongarch64 | meist nur x86_64 |
| 38 | Datenverzeichnis wählbar | ✅ FirstRunSetup-Wizard | meist fix |
| 39 | Auth umschaltbar | ✅ Single-Machine auto-aus | ❌ (kein Konzept) |
| 40 | Anmeldedaten-Verschlüsselung | ✅ Tauri Stronghold | teilweise |
| 41 | API extern aufrufbar | ✅ exponiert `/api/*` | ❌ |
| 42 | Same-Machine-WPS-Add-in-Integration | ✅ chayuan-wps v3.0 | ❌ |
| 43 | OpenAPI HMAC-Auth | ✅ X-App-Id / X-Sign | ❌ |
| 44 | OpenAI SDK direkt zu lokal | ✅ | teilweise |
| 45 | Multi-Tab-Parallel-Chat | ✅ Browser-Style | teilweise |
| 46 | Tab-Drag / Kontextmenü | ✅ | teilweise |
| 47 | Theme (Dunkel/Hell/System) | ✅ | meist |
| 48 | Custom-Schriftgröße | ✅ persistent | teilweise |
| 49 | i18n | ✅ zh / en / ja / de / fr | teilweise |
| 50 | Hilfe-Center (eingebettetes Markdown) | ✅ | teilweise |
| 51 | Feedback-Kanal | ✅ QR + GitHub-Issue | teilweise |
| 52 | Auto-Update | ⏳ geplant | meist |
| 53 | System-Tray | ✅ | meist |
| 54 | Globale Shortcuts | ✅ | teilweise |
| 55 | Desktop-Benachrichtigung | ✅ | meist |
| 56 | Datei-Drag-Drop-Upload | ✅ | meist |
| 57 | Zwischenablage-Integration | ✅ | meist |
| 58 | Beobachtbarkeit (Langfuse) | ✅ optional, Standard aus | teilweise |
| 59 | Eval-Modul | ✅ KB-Recall-Eval | ❌ |
| 60 | Audit / Quota / RBAC | ✅ governance-Modul | ❌ |
| 61 | PII-Maskierung | ✅ governance/redact | ❌ |
| 62 | Datenherkunft | ✅ lineage | ❌ |
| 63 | Multi-User / Multi-Tenancy | ✅ (Profil-Switch) | ❌ |
| 64 | Entwickler-Doctor | ✅ `chayuan doctor` | teilweise |
| 65 | Open-Source-Lizenz | **AGPL-3.0** | variiert |

---

## 8. Detaillierte Funktionsliste

### 8.1 Chat & Streaming

SSE-Streaming, Deep-Think-Faltung, 3-Schichten-Tool-Call, Anhang-Verarbeitung, Lokale Persistenz, Edit/Regenerate/Branch, Unified Send, virtualisiertes Scrollen, IME-sicheres Enter.

### 8.2 KB-Typen & Dokumentformate

KB-Typen: `doc` / `struct` / `vec` / `office` / `img` (siehe §5.2).

Dokumentformate: PDF, DOCX, DOC, XLSX, XLS, CSV, PPTX, MD, HTML, TXT, PNG/JPG/BMP/TIFF, JSON/YAML/TOML, EML/MSG, EPUB.

### 8.3 DB-Konnektoren (Strukturierte Daten)

17 SQL-Dialekte: **International** (MySQL, PostgreSQL, SQLite, MS SQL Server, Oracle, ClickHouse, Hive), **Heimat** (DM, KingbaseES, Apache Doris), **Dokument/Volltext** (MongoDB, Elasticsearch).

**Sicherheit**: Read-only AST-Validierung, Whitelist-Tabellen/Spalten.

### 8.4 Vektorspeicher

sqlite-vec (Standard, eingebettet), FAISS, Milvus / Lite, Chroma, Zilliz, Elasticsearch, PostgreSQL + pgvector, Relyt.

### 8.5 Modellanbieter

**Cloud (international)**: OpenAI, Anthropic Claude, Gemini, Mistral, Together, Groq.
**Cloud (Heimat)**: DeepSeek, Qwen / Dashscope, Wenxin, Zhipu GLM, Moonshot Kimi, Doubao, Baichuan, MiniMax, Yi, SiliconFlow.
**Lokal / OpenAI-kompatibel**: Ollama, LM Studio, Xinference, vLLM, OneAPI, FastChat, LocalAI.

### 8.6 Embeddings / Reranker / OCR

Embeddings: bge-m3-onnx (Standard), Ollama, Infinity, OpenAI, Dashscope, ZhipuAI, Jina, Cohere.

Reranker: BAAI/bge-reranker-v2-m3 (Standard).

OCR: RapidOCR-ONNX (Standard, CPU, ~70 MB).

### 8.7 Multimodal

T2I, Vision LLM, TTS (Piper, ~30 MB), ASR (FunASR), T2V.

### 8.8 Eingebaute Tools (30+)

Daten/Wissen, Code/System, Akademisch, DevOps, Messaging, Realtime/Geo, Generisch, Multimodal — jeweils umfangreich (siehe Hauptdokument).

### 8.9 MCP

Client und Server beide Rollen unterstützt (stdio / sse).

### 8.10 Multi-Model Arena

N Lanes unbegrenzt, Unified Send, Layout persistent, Lane-Titel = erste Benutzerfrage.

### 8.11 Auto-Aufbau Wissensstruktur (Folder Sync)

Endpoint: `/api/folder-sync/*`. Watch + Parse + OCR + Chunk + Embed + Ingest in einem Schritt.

### 8.12 Zitatanzeige

Vertrauenssterne + Quelltyp-Icons + 1-Klick auf Original.

---

## 9. HTTP-API-Übersicht

Backend: `127.0.0.1:62581`. Swagger: `http://127.0.0.1:62581/docs`.

Hauptrouten: `/healthz`, `/api/chat/*`, `/api/v1/kb-query/*` (empfohlen), `/api/kb/*`, `/api/folder-sync/*`, `/api/image/*`, `/api/voice/*`, `/api/tool/*`, `/api/mcp/*`, `/api/governance/*`, `/api/admin/*`, `/api/provider/*`, `/openai/v1/*` (OpenAI-kompatibel), `/openapi/v1/*` (HMAC).

```python
# OpenAI SDK direkt zu lokal
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:62581/openai/v1", api_key="anything")
resp = client.chat.completions.create(model="qwen2.5:7b", messages=[...], stream=True)
```

---

## 10. Entwickler-Setup

Voll: [PACKAGING.md](PACKAGING.md). Schnellstart:

| Tool | Version |
|---|---|
| Python | 3.12 |
| Poetry | ≥ 1.8 |
| Node.js | 22 |
| pnpm | 9 |
| Rust | stable |

```bash
# Backend
cd chayuan-server && poetry install && poetry run chayuan start -a --single-machine

# Tauri-Dev
cd chayuan-client && pnpm install && pnpm dev:desktop
```

```powershell
# Windows-Build
.\build-desktop.cmd
.\build-desktop.cmd -BundleOnly
```

```bash
# macOS / Linux
./build-desktop.sh
./build-desktop.sh --bundle-only
```

---

## 11. Tutorial-Einstiegspunkte

- In-App Help Center (Sidebar → Hilfe)
- In-App About (Einstellungen → About)
- Offizielle Website: <https://aidooo.com>
- WPS-Add-in: <https://github.com/zhgyuhuii/chayuan/blob/main/README.md>
- Build-Anleitung: [PACKAGING.md](PACKAGING.md)

---

## 12. Sicherheit · Datenschutz · Offline

- **Datenstandort**: `CHAYUAN_ROOT` (vom Benutzer gewählt). Keine Uploads zu Chayuan-Servern.
- **Anmeldedaten**: Tauri Stronghold (ChaCha20-Poly1305 + Argon2id) verschlüsselt
- **Netzwerk-Egress**: Vollständig offline möglich; bei Cloud-LLM nur deren API-Endpunkt
- **Audit**: governance-Modul (Audit-Log / PII-Maskierung / Datenherkunft)

---

## 13. Roadmap

| Phase | Status | Inhalt |
|---|---|---|
| 1–7 | ✅ | First-Run-Wizard, PyInstaller, Sidecar-Wiring, sqlite-vec, Single-Machine-Profil, 3-Plattform-CI |
| **8** | ⏳ | **Server-as-Truth + SSE-Multicast** |
| 6.x | ⏳ | macOS-Notarize / Windows-EV-Signierung / SM2-Krypto / Linux-ARM-Runner |
| 8.x | ⏳ | Auto-Update / inkrementelle Patches |
| 9 | ⏳ | Mobile / Web-Client mit gleichem Backend |

---

## 14. Community / Feedback / Kommerziell

| Kanal | Zweck | Adresse |
|---|---|---|
| Offizielle Website | Produkt / Geschäft | <https://aidooo.com> |
| WeChat OA | Release Notes | 智灵鸟科技 |
| GitHub Issue (dieses) | Öffentliche Fragen | (intern) |
| GitHub Issue (WPS) | WPS-Add-in | <https://github.com/zhgyuhuii/chayuan/issues> |
| Geschäfts-E-Mail | Enterprise / OEM | über Website |

---

## 15. Danksagungen

[Tauri](https://tauri.app/) · [FastAPI](https://fastapi.tiangolo.com/) · [LangChain](https://www.langchain.com/) · [sqlite-vec](https://github.com/asg017/sqlite-vec) · [RapidOCR](https://github.com/RapidAI/RapidOCR) · [bge-m3](https://huggingface.co/BAAI/bge-m3) · [Piper TTS](https://github.com/rhasspy/piper) · [FunASR](https://github.com/modelscope/FunASR) · [Ollama](https://ollama.ai/) · [onnxruntime](https://onnxruntime.ai/) · [React](https://react.dev/) · [TanStack](https://tanstack.com/) · [Zustand](https://github.com/pmndrs/zustand) · [Tailwind CSS](https://tailwindcss.com/) · [Radix UI](https://www.radix-ui.com/) · [Shiki](https://shiki.style/) · [Lucide Icons](https://lucide.dev/) · [Marked](https://marked.js.org/) · [DOMPurify](https://github.com/cure53/DOMPurify)

Jede Komponente wird unter eigener Lizenz verteilt. Die aggregierte Arbeit steht unter **AGPL-3.0**.

---

## Anhang A: FAQ

**Q1: Wo liegt das Standard-Datenverzeichnis?** —— `~/Library/Application Support/chayuan` (macOS) / `%APPDATA%\chayuan` (Win) / `~/.local/share/chayuan` (Linux). Beim ersten Start frei wählbar.

**Q2: Funktioniert es ohne Internet?** —— Mit lokalem LLM (Ollama etc.): voll funktionsfähig. Mit Cloud-LLM: KB-Retrieval / Parsing / OCR weiterhin verfügbar, nur Chat nicht.

**Q3: Warum heißt das Symbol "察元AI" (Chinesisch)?** —— NSIS-Post-Install-Hook benennt die Standard-ASCII-Verknüpfung nach der Finish-Seite um.

**Q4: Kann ich Backend separat deployen?** —— Aktuelle Single-Machine-Edition ist für "Frontend + Backend auf gleicher Maschine" gedacht. Für Multi-User / Server-Deployment siehe `chayuan-server/packaging/README.md`.

**Q5: Muss ich chayuan-wps und Desktop zusammen nutzen?** —— Nein, beide funktionieren unabhängig. Bei gleichem Backend: KB / Modelle / Historie geteilt.

**Q6: Schränkt AGPL-3.0 kommerzielle Nutzung ein?** —— **Keine Einschränkung für interne Nutzung / Intranet / Org-Verteilung.** Nur "modifizierte Version als SaaS" erfordert Quellcode-Offenlegung. Für OEM-Bündelung bitte separate kommerzielle Lizenz anfragen.

**Q7: Wie nutze ich Multi-Model-Arena?** —— Chat-Seite → Topbar [+ Hinzufügen] → jede Lane wählt eigenes Modell → "Unified Send" Checkbox → Tippen in einer Lane fanned out an alle.

**Q8: Gehen Daten beim Upgrade verloren?** —— Uninstaller berührt `CHAYUAN_ROOT` **nicht**. Nur die Pointer-Datei in `%APPDATA%\chayuan\` wird gelöscht. Echte Daten bleiben erhalten.

---

## Anhang B: Glossar

| Begriff | Bedeutung |
|---|---|
| CHAYUAN_ROOT | Vom Benutzer gewähltes Datenverzeichnis |
| Sidecar | Tauri-spawned Python-Subprozess |
| ku_id | Knowledge-Universe ID |
| Knowledge Universe | Vereinheitlichte Abstraktion von 5 Quellenarten |
| Model Arena | Multi-Lane Modellvergleich |
| Lane | Ein Modell-Arena-Pane |
| Composer | Chat-Eingabebereich unten |
| MCP | Model Context Protocol (Anthropic) |
| RAG | Retrieval-Augmented Generation |
| text2sql | NL → SQL mit AST-Validierung |
| Stronghold | Tauri-Anmeldetresor-Plugin |
| Splash | Startanimation, 5-Schichten-Mount |

---

<div align="center">

**Chayuan AI · Desktop (Single-Machine Edition)** · Tauri 2 + React 19 + Python 3.12 + AGPL-3.0

By **Beijing Zhilingniao Technology Center** · <https://aidooo.com>

</div>
