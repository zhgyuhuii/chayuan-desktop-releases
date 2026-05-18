<div align="center">

<img src="chayuan-client/images/logo.png" alt="Chayuan AI" width="120" height="120" />

# Chayuan AI · Desktop (Single-Machine Edition)

**Offline-first · Made-in-China OS Ready · Full-Stack Local Knowledge Base · Multi-Model Arena**

[![Tauri 2](https://img.shields.io/badge/Tauri-2-24c8db?logo=tauri&logoColor=white)](https://tauri.app/)
[![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

</div>

---

## Language / 语言

| Language | Document |
|----------|----------|
| 简体中文(主文档,完整版) | [README.md](README.md) |
| **English (this file)** | **[README.en.md](README.en.md)** |
| 日本語 | [README.ja.md](README.ja.md) |
| Deutsch | [README.de.md](README.de.md) |
| Français | [README.fr.md](README.fr.md) |

Detailed packaging guide: [PACKAGING.md](PACKAGING.md)

---

## In One Sentence

**Chayuan AI Desktop (Single-Machine Edition)** is an AI assistant that **installs in one click and runs without an internet connection** —
it ships with a **multi-vendor LLM gateway**, **five categories of knowledge sources** (documents / structured / vector / office / external),
**30+ built-in tools**, **MCP protocol** support, and **multi-model arena**.
It runs natively on **Windows / macOS / Linux** and the **Chinese domestic OS family** (Kylin, UnionTech UOS, openKylin, deepin).
All data stays in a user-chosen directory — **API keys and documents never leave your machine**.

---

## Table of Contents

- [1. License (AGPL-3.0)](#1-license-agpl-30)
- [2. Brand Identity Preservation](#2-brand-identity-preservation)
- [3. Relationship with chayuan-wps (WPS Add-in)](#3-relationship-with-chayuan-wps-wps-add-in)
- [4. Product Overview & System Architecture](#4-product-overview--system-architecture)
- [5. Core Features](#5-core-features)
- [6. Supported Operating Systems](#6-supported-operating-systems)
- [7. 60+ Item Comparison vs Doubao / Cherry Studio / etc.](#7-60-item-comparison-vs-doubao--cherry-studio--etc)
- [8. Detailed Feature List](#8-detailed-feature-list)
- [9. HTTP API Overview](#9-http-api-overview)
- [10. Developer Setup](#10-developer-setup)
- [11. Tutorial Entry Points](#11-tutorial-entry-points)
- [12. Security · Privacy · Offline](#12-security--privacy--offline)
- [13. Roadmap](#13-roadmap)
- [14. Community / Feedback / Commercial](#14-community--feedback--commercial)
- [15. Acknowledgments](#15-acknowledgments)
- [Appendix A: FAQ](#appendix-a-faq)
- [Appendix B: Glossary](#appendix-b-glossary)

---

## 1. License (AGPL-3.0)

This repository is licensed under the **[GNU Affero General Public License v3.0](LICENSE)**.

> The key difference vs. Apache-2.0 / MIT is the **§13 network distribution clause**:
> if you offer a modified version of this software as a network service to **external users** (SaaS, public cloud, etc.),
> you **must release the corresponding source code** (including your modifications) under the same AGPL-3.0 license to those users.
> For **internal use, intranet deployment, or local single-machine usage**, the source-disclosure trigger does not apply.

**Practically:**
- ✅ Personal / team / enterprise internal deployment, intranet, local modifications: **free, no commercial restriction**
- ✅ Redistributing modified installer (preserving AGPL & copyright): **allowed**
- ⚠ Deploying a modified version as **a SaaS / public-internet service**: **must disclose your modifications**
- ⚠ Bundling into your closed-source commercial product for resale: **requires a separate commercial license**

For **enterprise commercial license / OEM white-label / paid support contracts**, contact us via [https://aidooo.com](https://aidooo.com).

### Copyright

Product developed and operated by **Beijing Zhilingniao Technology Center (北京智灵鸟科技中心)**.
UI strings, default prompt templates, icons, and brand assets are protected under copyright law except where third-party components are governed by their own licenses.

**Disclaimer:** This software is provided "as-is". LLM-generated content may be inaccurate or inapplicable;
classified, compliance, and legal decisions should follow human judgment and formal procedures.
Any "checking" features (confidentiality check, AI-trace check, etc.) are **assistive references only** and do not constitute formal certifications.

---

## 2. Brand Identity Preservation

To maintain user transparency, source traceability, and brand consistency, the **"察元" (Chayuan)** trademark and its fixed product names
(including but not limited to "察元 AI", "察元 AI 助手", "察元智库", "察元对抗", "About Chayuan", etc.) appear in the following positions
of **end-user-facing UI** and constitute essential **product origin and brand identifiers**:

- App window title, splash screen, About dialog, Settings → About
- System tray tooltip, desktop shortcut name (default: `察元AI.lnk`)
- Help center / feedback dialog brand text
- Other user-visible strings in the same semantic chain

**Without prior written authorization, any redistribution or customized build must NOT alter, remove, obscure, dilute, or misleadingly rewrite the above "Chayuan" brand text** (e.g., renaming to a different commercial name while still pointing to this software, causing users to misidentify the origin).

This restriction does **not** prohibit code modification per AGPL-3.0 — you may freely change logic in internal builds.
However, if you distribute **end-user-installable artifacts** to third parties, you must preserve the brand identity prominence
or obtain prior written consent for an alternative attribution scheme.
For **white-labeling / full localization** that touches brand strings, obtain a **separate authorization** via the business channel.

---

## 3. Relationship with chayuan-wps (WPS Add-in)

Chayuan AI is an **end-to-end office AI platform** currently delivered through two complementary open-source projects:

| Project | Repository | Form | Primary Users |
|---|---|---|---|
| **chayuan-desktop** (this repo) | (internal) | **Desktop single-machine app** — Tauri 2 shell + embedded Python backend | Individuals & enterprises wanting local-only AI |
| **chayuan-wps** | <https://github.com/zhgyuhuii/chayuan.git> | **WPS Writer add-in** — Vue 3 add-in running inside WPS | Government / enterprise authors heavily using WPS for documents / contracts / bids |

### How They Cooperate

```
              ┌─────────────────────────────────────────────┐
              │   Chayuan AI Desktop (chayuan-desktop)      │
              │   • Embedded chayuan-server (Python)        │
              │   • KB / model gateway / tools / MCP        │
              │   • Listens on 127.0.0.1:62581              │
              │   • Data lands at CHAYUAN_ROOT              │
              └─────────────────────┬───────────────────────┘
                                    │  HTTP/REST
                                    │  /api/v1/kb-query/search
                                    │  /api/chat/completions
                                    │  /openapi/v1/*  (HMAC-signed)
                                    ▼
              ┌─────────────────────────────────────────────┐
              │   chayuan-wps  (WPS add-in, Vue 3 + Vite)   │
              │   • Runs inside WPS Writer                  │
              │   • Selection / full-document context       │
              │   • Writeback: insert / replace / comment   │
              │   • Calls Desktop's /api/* for AI & KB      │
              └─────────────────────────────────────────────┘
```

**Typical setup**: Install **chayuan-desktop** as a local "AI server" → its sidecar runs at `127.0.0.1:62581`.
Install **chayuan-wps** in WPS → set "Server URL" to `http://127.0.0.1:62581`.
Both share the **same knowledge bases, model configurations, and conversation history**.
A conversation started in WPS appears in the Desktop client's history.

The WPS add-in (v3.0+) supports `authMode: 'none'` for keyless local connections in single-machine mode.
See [chayuan-wps README](https://github.com/zhgyuhuii/chayuan/blob/main/README.md) for details.

---

## 4. Product Overview & System Architecture

### 4.1 What It Is

Chayuan AI Desktop packs an **enterprise-grade AI backend** into your local machine — install and use, no Docker, no separate Python install, no Redis / RabbitMQ / PostgreSQL required. All heavy lifting (LLM calls, KB indexing, vector retrieval, tool execution, streaming orchestration) happens in local processes. The frontend is a native window React app; the backend is an embedded Python sidecar.

### 4.2 Three-Layer Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend  Tauri 2 + React 19 + Tailwind                             │
│  • Browser-like multi-tab shell (home / chat / KB / marketplace)     │
│  • Multi-lane Model Arena                                            │
│  • Collapsible tool calls / citation panel / streaming reasoning     │
│  • Local SQLite conversation persistence (Tauri sql plugin)          │
│  • Stronghold credential vault (ChaCha20-Poly1305 + Argon2id)        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │  spawn + /healthz probe
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Sidecar  chayuan-server (Python 3.12, FastAPI)                      │
│  • Single-machine profile: no Redis / Celery / PostgreSQL            │
│  • Vector store: sqlite-vec (embedded SQLite extension) / FAISS      │
│  • Cache: cachetools TTLCache / Queue: asyncio.Queue                 │
│  • Embeddings: ONNX local (default bge-m3-onnx) / Ollama / OpenAI    │
│  • OCR: RapidOCR-ONNX (CPU-only, ~70 MB)                             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │  HTTP / OpenAI-compatible
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  External  user-chosen models & data sources                         │
│  • Local: Ollama / LM Studio / vLLM / Xinference                     │
│  • Cloud LLM: OpenAI / DeepSeek / Qwen / Zhipu / Wenxin / Kimi…      │
│  • Databases: MySQL / PostgreSQL / Oracle / DM / KingbaseES…         │
│  • External vectors: Milvus / Chroma / Elasticsearch / Zilliz        │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 Startup Sequence

1. User double-clicks the **Chayuan AI** desktop icon
2. Tauri main window appears instantly (`backgroundColor = #0f172a`); **first frame is the splash animation** (5-layer zero-latency mount)
3. Tauri spawns the embedded `chayuan-server` subprocess with `CHAYUAN_ROOT=<user-chosen dir>`
4. Frontend `SidecarGate` polls `/healthz`; main UI renders once backend is ready
5. First-launch wizard prompts user to pick a data directory (default: platform standard); subsequent launches reuse it

---

## 5. Core Features

### 5.1 Offline-First

- **Embedded backend**: Python interpreter + all wheels + sqlite-vec extension + resources are bundled in the installer
- **Embedded models**: ONNX-quantized bge-m3 embedding model (~120 MB) ships with the installer
- **Embedded OCR**: RapidOCR-ONNX weights bundled
- **Optional local LLM**: one-click integration with Ollama / LM Studio / vLLM / Xinference
- **Doctor command**: self-diagnoses data directory, sqlite-vec extension, embedding-model integrity

### 5.2 Knowledge Universe

> **Unified query path** — documents, structured databases, external vector collections, office private KBs, and image KBs are abstracted as `ku_id` (Knowledge-Universe ID).
> Frontend selects sources by type; the server auto-routes to the matching retrieval adapter.

- **`POST /api/v1/kb-query/search`** — single endpoint handles multi-source hybrid retrieval
- Five source kinds:
  - **`doc:<kb_name>`** — document KB (PDF / Word / Excel / Markdown / HTML / image…)
  - **`src:<source_id>`** — structured (SQL / MongoDB / Elasticsearch)
  - **`vec:<collection>`** — external vector (Milvus / Chroma / Zilliz)
  - **`office:<owner>[:<group>]`** — office private (enterprise / team / individual three-tier)
  - **`img:<kb_name>`** — image KB (CLIP embeddings)
- Unified `RetrievalChunk` + `Citation` return format with origin links / trust score / generated SQL DSL
- **Hybrid retrieval**: vector + BM25 keyword in parallel, weighted scoring
- **Reranker**: optional BAAI/bge-reranker-v2-m3 etc.
- **Diagnostics**: each query returns `Diagnostic[]` to debug "wrong answer" issues

### 5.3 Chayuan Chat

- **Browser-style multi-tab shell**: open multiple conversations like browser tabs; each tab holds its own conversationId / model / KB selection
- **Streaming markdown rendering** with Shiki code highlight + reasoning (deep-think) token folding
- **3-layer tool call display**:
  - L1: summary chip (icon + tool name + "called N times")
  - L2: expand to see args/output summary
  - L3: click again for full JSON
- **Citation panel**: KB source list + trust stars + one-click open original / download attachment
- **Attachments**: drag / paste / click; auto-OCR + ingest into context
- **Local conversation persistence**: Tauri SQLite plugin

### 5.4 Multi-Model Arena

- **N lanes per page** (no upper limit), each lane independently selects a model
- **Unified send**: with checkbox enabled, typing in any lane fans out to all lanes simultaneously
- **Lane operations**: collapse / resize / drag-reorder / add / remove
- **Collapsed lane title** auto-uses the **first user question** as a vertical label (e.g., "你是谁"), not the model name

### 5.5 Model Marketplace

- 7 category tabs: **Recommended / All / Local / Domestic (国内) / International / Aggregated / Custom**
- Provider card grid: logo + name + tags + enable toggle + settings cog
- **Auto-fetch model list**: enter API Key → blur → auto-call provider's `/v1/models`
- **Auto-promote default**: if no default is set in Settings, auto-elevate the first candidate per capability (chat / embed / image / rerank)
- Model categories: chat / embedding / image-gen / vision / rerank / TTS / ASR / video

---

## 6. Supported Operating Systems

| Category | OS | Architecture | Status |
|---|---|---|---|
| **Windows** | Windows 10 (1809+) / 11 | x86_64 | ✅ Full (NSIS installer + WebView2 auto-install) |
| **macOS** | macOS 11 (Big Sur)+ | Apple Silicon (arm64) / Intel (x86_64) | ✅ Full (.dmg + notarization-ready) |
| **Linux** | Ubuntu 22.04+ / Debian 12+ | x86_64 / aarch64 | ✅ Full (.deb / .rpm / .AppImage) |
| **Domestic Linux** | **Kylin V10** (银河麒麟) | x86_64 / aarch64 / LoongArch64 | ✅ Compatible |
| **Domestic Linux** | **UnionTech UOS** (统信) | x86_64 / aarch64 | ✅ Compatible |
| **Domestic Linux** | **openKylin** | x86_64 / aarch64 | ✅ Compatible |
| **Domestic Linux** | **deepin** | x86_64 | ✅ Compatible |
| **Domestic Linux** | **openEuler** | x86_64 / aarch64 | ⚠ RPM bundle |

**Domestic ecosystem support**:
- **Domestic databases**: 达梦 DM, 人大金仓 KingbaseES, Apache Doris (see §8.3)
- **Domestic LLMs**: DeepSeek, Qwen, Zhipu GLM, Wenxin, Kimi, Doubao, SiliconFlow, Baichuan, MiniMax (see §8.5)
- **Domestic OCR**: RapidOCR-ONNX (PaddleOCR-derived, runs on CPU)
- **Domestic embeddings**: BAAI/bge-m3-onnx (default)

---

## 7. 60+ Item Comparison vs Doubao / Cherry Studio / etc.

> Comparison based on **2026-05** publicly known feature sets. Pricing, data residency, enterprise private deployment, and regulatory certifications are governed by **vendor official documentation**.

### 7.1 Named Product Comparison

| Item | **Chayuan AI Desktop** | **Doubao Desktop** | **Cherry Studio** | **ChatGPT Desktop** | **LM Studio** | **Open WebUI** | **AnythingLLM** | **Chatbox** |
|---|---|---|---|---|---|---|---|---|
| **Form** | Tauri + embedded Python — full local stack | ByteDance Doubao client (cloud-bound) | Electron multi-vendor client | OpenAI official desktop | Electron local-inference workbench | Web UI (with Ollama etc.) | Electron + Node backend | Tauri cross-platform chat |
| **Backend** | **Embedded Python sidecar** | ByteDance cloud | Direct API proxying | OpenAI cloud | Local llama.cpp | Self-hosted web | Node + embedded vector DB | Direct vendor API |
| **Offline LLM** | ✅ Ollama/LM Studio/vLLM/Xinference | ❌ | ✅ partial | ❌ | ✅ primary | ✅ with Ollama | ✅ partial | ✅ partial |
| **Local KB (RAG)** | ✅ 5 source kinds, embedded sqlite-vec | ✅ but cloud-routed | ✅ single (vector) | ❌ | ❌ | ✅ external | ✅ single | partial |
| **Database connectors (text2sql)** | ✅ 17 dialects: MySQL/PG/Oracle/DM/KingbaseES/Doris/ClickHouse/Hive… | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **External vector connectors** | ✅ Milvus/Chroma/ES/Zilliz/PG-vector | ❌ | ❌ | ❌ | ❌ | partial | partial | ❌ |
| **Office private KB tier (3-tier)** | ✅ `office:owner[:group]` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP protocol** | ✅ stdio + sse, both server & client | ❌ | ✅ client | ❌ | ❌ | partial | ❌ | partial |
| **Built-in tools** | **30+** | few | few | OpenAI plugins | ❌ | ❌ | few | ❌ |
| **Multi-model Arena** | ✅ N lanes + unified send | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Multimodal (T2I/T2V/TTS/ASR)** | ✅ | partial | partial | partial | ❌ | ❌ | ❌ | partial |
| **Domestic OS / DB / LLM** | ✅ Kylin/UOS + DM/KingbaseES/Doris + Chinese LLMs | ✅ ByteDance ecosystem | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Data residency** | **Local user dir** | ByteDance cloud | Local + clouds | OpenAI cloud | Local | Self-hosted | Local | Local |
| **License** | **AGPL-3.0** | Closed | Apache-2.0 | Closed | Closed | MIT | MIT | GPL-3.0 |
| **Auditable source** | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |

### 7.2 60+ Detailed Items (Chayuan AI vs Generic Desktop AI Client)

> "Generic desktop AI client" = the common feature combination of products like Cherry Studio / Chatbox / Doubao / Kimi Desktop / Qwen Desktop / Open WebUI / LM Studio.

| # | Dimension | Chayuan AI Desktop | Generic Desktop AI Client |
|---:|---|---|---|
| 1 | Installer form | Tauri native window + embedded Python sidecar | Electron / browser shell |
| 2 | Installer size | Medium (full Python runtime + default models) | Small (front-end only) |
| 3 | Internet required at launch | ❌ | ✅ usually |
| 4 | Splash animation | 5-layer zero-latency mount | usually present |
| 5 | Embedded vector store | ✅ sqlite-vec | ❌ external Milvus / FAISS |
| 6 | Embedded embedding model | ✅ ONNX bge-m3 (default) | ❌ user-configured |
| 7 | Embedded OCR | ✅ RapidOCR-ONNX | ❌ |
| 8 | Embedded TTS / ASR | ✅ Piper / FunASR (optional) | ❌ |
| 9 | Document RAG breadth | ✅ PDF/Word/Excel/PPT/Markdown/HTML/image | limited |
| 10 | Multi-vendor gateway | ✅ 18+ LLM providers | 5–10 |
| 11 | OpenAI-compatible routes | ✅ built-in `/openai/v1/*` | partial |
| 12 | Auto-detect provider models | ✅ Key→blur→fetch `/v1/models` | partial |
| 13 | Auto-promote default model per capability | ✅ chat/embed/image/rerank first candidate | ❌ |
| 14 | Model categories | chat/embed/vision/image-gen/rerank/audio/video | mostly chat + embed |
| 15 | Multi-model arena | ✅ unlimited lanes + unified send | ❌ |
| 16 | 3-layer tool call display | ✅ summary→args/output→full JSON | ❌ |
| 17 | Streaming reasoning fold | ✅ | ❌ |
| 18 | Citation chips (KB sources) | ✅ trust + 1-click to source/download | partial |
| 19 | Citation by source type | ✅ doc/struct/vec/office/web | ❌ |
| 20 | text2sql safety | ✅ readonly AST + whitelist tables/columns | ❌ |
| 21 | Domestic DB support | ✅ DM / KingbaseES / Doris | ❌ |
| 22 | International DB support | ✅ MySQL/PG/Oracle/SQLServer/ClickHouse/Hive/SQLite | usually 0–1 |
| 23 | MongoDB connector | ✅ | ❌ |
| 24 | Elasticsearch connector | ✅ | partial |
| 25 | External vector connectors | ✅ Milvus/Chroma/Zilliz/PG-vector/ES/Relyt | ❌ or 1 |
| 26 | MCP client | ✅ stdio + sse | partial |
| 27 | MCP server | ✅ self can be MCP server | ❌ |
| 28 | Built-in tool count | 30+ | 0–10 |
| 29 | Custom HTTP tool (OpenAPI) | ✅ Swagger parsing | partial |
| 30 | Custom script tool | ✅ Python REPL / Shell | partial |
| 31 | KB type count | 5 (doc/struct/vec/office/img) | mostly 1 |
| 32 | Document format coverage | PDF/Word/Excel/PPT/MD/HTML/image/CSV | mostly PDF + Word |
| 33 | Folder incremental sync | ✅ folder-sync route | partial |
| 34 | Auto-build knowledge structure | ✅ watch + parse + ingest end-to-end | ❌ |
| 35 | Multi-KB parallel retrieval | ✅ `selectedKuIds` carries multiple sources | partial |
| 36 | Domestic OS support | ✅ Kylin / UOS / openKylin / deepin | ❌ |
| 37 | Multi-architecture | x86_64 / aarch64 / loongarch64 | usually x86_64 only |
| 38 | User-selectable data dir | ✅ FirstRunSetup wizard | mostly fixed |
| 39 | Auth toggle-able | ✅ single-machine auto-disabled | ❌ (no concept) |
| 40 | Credential encryption | ✅ Tauri Stronghold + ChaCha20-Poly1305 | partial |
| 41 | API callable by external apps | ✅ exposes `/api/*` for WPS add-in | ❌ |
| 42 | Same-machine WPS add-in integration | ✅ chayuan-wps v3.0 direct connect to 127.0.0.1 | ❌ |
| 43 | OpenAPI HMAC auth | ✅ X-App-Id / X-Sign / X-Timestamp | ❌ |
| 44 | OpenAI SDK directly to local | ✅ `base_url=http://127.0.0.1:62581/openai/v1` | partial |
| 45 | Multi-tab parallel chat | ✅ browser-style | partial |
| 46 | Tab drag / context menu | ✅ close / close others / close all | partial |
| 47 | Theme (dark/light/system) | ✅ | mostly |
| 48 | Custom font size | ✅ persisted | partial |
| 49 | i18n (multilingual UI) | ✅ zh / en / ja / de / fr | partial |
| 50 | Help center (embedded markdown) | ✅ | partial |
| 51 | Feedback channel | ✅ embedded QR + GitHub Issue | partial |
| 52 | Auto update | ⏳ planned | mostly |
| 53 | System tray | ✅ tray-icon plugin | mostly |
| 54 | Global shortcut | ✅ global-shortcut plugin | partial |
| 55 | Desktop notification | ✅ notification plugin | mostly |
| 56 | File drag-drop upload | ✅ | mostly |
| 57 | Clipboard integration | ✅ | mostly |
| 58 | Observability (Langfuse) | ✅ optional, default off | partial |
| 59 | Eval module | ✅ KB recall eval | ❌ |
| 60 | Audit / quota / RBAC | ✅ governance module | ❌ |
| 61 | PII redaction | ✅ governance/redact | ❌ |
| 62 | Data lineage | ✅ lineage tracking | ❌ |
| 63 | Multi-user / multi-tenancy | ✅ (toggle profile, single-machine off by default) | ❌ |
| 64 | Developer-grade doctor | ✅ `chayuan doctor` | partial |
| 65 | Open-source license | **AGPL-3.0** | varies |

---

## 8. Detailed Feature List

### 8.1 Chat & Streaming

- **SSE streaming output** (token-by-token, code/table/citation co-rendered)
- **Deep-think folding** (`<think>` blocks become collapsible details)
- **3-layer tool call display** (see §5.3)
- **Attachment processing**: drag / paste / select; auto-OCR + parse + embed into context
- **Local persistence**: Tauri SQLite, `conversations` + `messages` tables, upsert semantics
- **Edit / regenerate / branch**: bubble-level operations, branches don't disrupt main thread
- **Unified Send**: in Model Arena, typing in any lane broadcasts to all
- **Virtualized history**: 60+ messages → windowing with OVERSCAN=6
- **IME-safe Enter**: composition mode (Chinese/Japanese input) doesn't trigger send

### 8.2 Knowledge Base Types & Document Formats

| KB Type | `ku_kind` | Description | Indexing |
|---|---|---|---|
| **Document** | `doc` | PDF / Word / Excel / PPT / Markdown / HTML / image | chunk + embed + BM25 dual index |
| **Structured** | `struct` | SQL / MongoDB / ES databases | schema + sample rows + DDL hints |
| **Vector** | `vec` | External Milvus / Chroma / Zilliz / PG-vector | direct external collection |
| **Office** | `office` | Enterprise / team / personal three-tier private KB | document namespace |
| **Image** | `img` | Image + caption | CLIP embedding |

**Document formats**: PDF, DOCX, DOC, XLSX, XLS, CSV, PPTX, MD, HTML, TXT, PNG/JPG/BMP/TIFF (image), JSON/YAML/TOML, EML/MSG (email), EPUB

### 8.3 Database Connectors (Structured Data)

17 SQL dialects via `chayuan/server/knowledge_source/sql/dialects.py`:

**International**: MySQL, PostgreSQL, SQLite, Microsoft SQL Server, Oracle, ClickHouse, Hive/Impala
**Domestic**: 达梦 DM, 人大金仓 KingbaseES, Apache Doris
**Document/Full-text**: MongoDB, Elasticsearch

**Safety**: text2sql goes through **read-only AST validation** — any INSERT/UPDATE/DELETE/DROP/CREATE is blocked before execution. Table/column names are checked against schema-cache whitelist. Aggregation queries (count / sum / top-N) require hitting `structured_aggregate` intent and produce auditable SQL + row count + column definitions.

### 8.4 Vector Stores

| Store | Single-Machine | Service | Notes |
|---|---|---|---|
| **sqlite-vec** | ✅ default | ⚠ single-only | Embedded, lands in SQLite file |
| FAISS | ✅ | ⚠ in-process | flat / IVF / HNSW |
| Milvus | ❌ | ✅ | standalone / cluster |
| Milvus Lite | ✅ | — | in-process |
| Chroma | ✅ | ✅ | persistent / HTTP |
| Zilliz | ❌ | ✅ | Milvus cloud |
| Elasticsearch | ❌ | ✅ | 8.x+, dense_vector |
| PostgreSQL + pgvector | ❌ | ✅ | |
| Relyt | ❌ | ✅ | Chinese distributed vector |

### 8.5 Model Providers

**Cloud (international)**: OpenAI, Anthropic Claude, Google Gemini, Mistral AI, Together AI, Groq
**Cloud (domestic)**: DeepSeek, Qwen / Dashscope, Wenxin / Qianfan, Zhipu GLM, Moonshot Kimi, Doubao, Baichuan, MiniMax, Yi, SiliconFlow
**Local / OpenAI-compatible**: Ollama, LM Studio, Xinference, vLLM, OneAPI, FastChat, LocalAI

### 8.6 Embeddings / Reranker / OCR

**Embeddings**: bge-m3-onnx (default, ONNX local), Ollama, Infinity, OpenAI text-embedding-3, Dashscope, ZhipuAI, Jina, Cohere

**Rerankers**: BAAI/bge-reranker-v2-m3 (default), bge-reranker-large, any sentence_transformers cross-encoder

**OCR**: RapidOCR-ONNX (default, CPU, ~70 MB), RapidOCR-Paddle (optional GPU)

### 8.7 Multimodal

| Modality | Endpoint | Default | Optional |
|---|---|---|---|
| T2I | `/api/image/text2image` | DALL-E / Doubao Wenshengtu | ComfyUI / SD WebUI |
| Vision | `/api/chat` (vision LLM) | Qwen-VL / GPT-4V / Claude 3 Vision | |
| TTS | `/api/voice/tts` | **Piper** (CPU, ~30 MB) | CosyVoice |
| ASR | `/api/voice/asr` | FunASR (Alibaba) | Whisper |
| T2V | `/api/video/text2video` | Qwen Wan / Doubao PixelDance | cloud vendors |

### 8.8 Built-in Tools (30+)

Source: `chayuan/server/agent/tools_factory/`.

**Data/Knowledge**: search_local_knowledgebase, search_internet, url_reader, text2sql, text2promql
**Code/System**: python_repl, shell
**Academic**: arxiv, pubmed_search, semantic_scholar, wikipedia_search, stackexchange, wolfram
**DevOps**: github_tool, gitlab_tool, confluence_search, notion_search
**Messaging**: dingtalk_message, wechat_work_message, lark_message
**Realtime/Geo**: amap_poi_search, amap_weather, openweather, news_api, yahoo_finance_news
**Generic**: calculate, http_request, openapi_call, custom_tools_runtime
**Multimodal**: text2image, search_youtube

### 8.9 MCP (Model Context Protocol)

- **Client**: register stdio or sse MCP servers via UI; tools auto-appear in Composer's MCP picker
- **Server**: Chayuan backend itself can act as an MCP server (via stdio) for Cursor / Claude Desktop / other MCP clients

### 8.10 Multi-Model Arena

> Open N independent lanes per chat page; pick a different model per lane; **Unified Send** broadcasts one prompt to all for side-by-side comparison.

### 8.11 Auto-Build Knowledge Structure (Folder Sync)

> Mount a folder to a KB; any file change (add / modify / delete) triggers: parse → OCR (if needed) → chunk → embed → ingest → update citation metadata.

- Endpoint: `/api/folder-sync/*`
- mtime + size + hash change detection; startup incremental scan + runtime watchdog
- Failed files quarantined, don't block others
- One-click reindex from KB detail page

### 8.12 Citation Display

Each LLM answer carries a **Citation Strip** below: trust stars + source-type icons + 1-click action.

| Source kind | Icon | Action |
|---|---|---|
| `document` | 📄 | Open original / download attachment |
| `structured` | 🗃 | Show generated SQL + table results + row count |
| `vector` | 📚 | Show collection / vector_id / metadata (no download) |
| `office` | 🏢 | Show enterprise/team/personal scope + filename |
| `web` | 🌐 | Open external link |

---

## 9. HTTP API Overview

> Backend defaults to `127.0.0.1:62581`. Live OpenAPI Swagger at `http://127.0.0.1:62581/docs`.

### 9.1 Major Route Groups

| Prefix | Purpose |
|---|---|
| `/healthz` | Health check |
| `/api/chat/*` | Chat (stream/sync, KB mode, multi-source) |
| `/api/file-chat/*` | File-scoped chat (upload + Q&A) |
| `/api/v1/kb-query/*` | **Unified knowledge query** (recommended entry) |
| `/api/kb/*` | KB CRUD (create / upload / search / sync) |
| `/api/knowledge-source/*` | External source registration |
| `/api/knowledge-universe/*` | Multi-source orchestration / federation |
| `/api/folder-sync/*` | Folder auto-sync |
| `/api/storage/*` | File upload/download |
| `/api/image/*` | Image models (T2I / image embedding) |
| `/api/voice/*` | TTS / ASR |
| `/api/tool/*` | Tool execution / registry |
| `/api/mcp/*` | MCP lifecycle |
| `/api/governance/*` | Audit / quota / PII / policy |
| `/api/admin/*` | Config / user / quota / platform overrides |
| `/api/provider/*` | LLM platform catalog / model listing / connectivity test |
| `/api/auth/*` | Login (disabled in single-machine) |
| `/openai/v1/*` | **OpenAI-compatible endpoints** (chat / embedding / image) |
| `/openapi/v1/*` | Custom app HMAC auth |
| `/openapi/ws/*` | WebSocket app events |

### 9.2 OpenAI SDK Drop-in

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:62581/openai/v1",
    api_key="anything",
)
resp = client.chat.completions.create(
    model="qwen2.5:7b",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content, end="", flush=True)
```

### 9.3 Unified Knowledge Query

```http
POST /api/v1/kb-query/search
Content-Type: application/json

{
  "ku_ids": ["doc:Product Docs", "src:user_db", "vec:milvus_research"],
  "query": "Top 3 products by sales last month?",
  "top_k": 5
}
```

### 9.4 OpenAPI HMAC Auth (for external apps)

```
X-App-Id:    <app_id>
X-Timestamp: <unix_ms>
X-Sign:      HMAC-SHA256(secret, "{app_id}\n{timestamp}\n{path}\n{body_md5}")
```

See [chayuan-wps authClient.js](https://github.com/zhgyuhuii/chayuan/blob/main/src/services/kb/authClient.js).

---

## 10. Developer Setup

Full end-to-end packaging guide: [PACKAGING.md](PACKAGING.md). Quickstart:

### 10.1 Prerequisites

| Tool | Version |
|---|---|
| Git | ≥ 2.30 |
| Python | **3.12.x** |
| Poetry | ≥ 1.8 |
| Node.js | **22.x** |
| pnpm | **9.x** |
| Rust | stable |

### 10.2 Local Development

```bash
# Terminal 1: backend
cd chayuan-server
poetry install
poetry run chayuan start -a --single-machine

# Terminal 2: Tauri dev
cd chayuan-client
pnpm install
pnpm dev:desktop
```

### 10.3 Local Packaging

**Windows**:
```powershell
.\build-desktop.cmd
.\build-desktop.cmd -BundleOnly
.\build-desktop.cmd -SkipServer
```

**macOS / Linux**:
```bash
./build-desktop.sh
./build-desktop.sh --skip-server
./build-desktop.sh --bundle-only
```

### 10.4 Project Structure

```
/chayuan-desktop/
├── README.md / README.{en,ja,de,fr}.md
├── PACKAGING.md
├── LICENSE                           ← AGPL-3.0
├── build-desktop.{cmd,ps1,sh}
├── chayuan-server/                   ← Python backend
│   ├── libs/chayuan-server/chayuan/server/
│   │   ├── api_server/               ← FastAPI routes
│   │   ├── chat/
│   │   ├── knowledge_base/
│   │   ├── knowledge_source/
│   │   ├── retrieval/
│   │   ├── kb_query/
│   │   ├── agent/tools_factory/
│   │   ├── mcp_server/
│   │   ├── ai_platform/
│   │   ├── governance/
│   │   └── profiles/single_machine.py
│   └── packaging/pyinstaller/
└── chayuan-client/                   ← Tauri frontend
    ├── apps/desktop/
    │   ├── src-tauri/
    │   └── src/
    └── packages/{app,api,ui,transport,platform-tauri,platform-web}
```

---

## 11. Tutorial Entry Points

| Entry | Content |
|---|---|
| **In-app Help Center** | Sidebar → Help → embedded markdown (getting-started.md) |
| **In-app About** | Settings → About → version / publisher / website / WeChat QR |
| **In-app Feedback** | Settings → Feedback → embedded QR + GitHub Issue link |
| **Official site** | <https://aidooo.com> |
| **WPS add-in tutorial** | <https://github.com/zhgyuhuii/chayuan/blob/main/README.md> |
| **Packaging guide** | [PACKAGING.md](PACKAGING.md) |

---

## 12. Security · Privacy · Offline

### 12.1 Data Residency

All user data lands in `CHAYUAN_ROOT` (user-chosen at first launch):
- macOS: `~/Library/Application Support/chayuan`
- Windows: `%APPDATA%\chayuan`
- Linux: `~/.local/share/chayuan`

Including: conversation SQLite / KB vector index / uploaded files / audit logs / model weight cache. **No data is uploaded to Chayuan servers.** Telemetry (Langfuse) is **off by default**.

### 12.2 Credentials

Sensitive fields (API keys etc.) are encrypted at rest via **Tauri Stronghold**:
- Cipher: **ChaCha20-Poly1305**
- KDF: **Argon2id**

### 12.3 Network Egress

- **Fully offline** (all-local models): **0 external network egress**
- **Hybrid** (cloud LLM configured): only model API endpoint egress; domain whitelist available in enterprise edition

### 12.4 Audit

`governance/` module: audit log / PII redaction / data lineage. Single-machine mode logs locally; intranet deployment can integrate SIEM.

---

## 13. Roadmap

| Phase | Status | Content |
|---|---|---|
| 1–7 | ✅ | First-run wizard, PyInstaller, sidecar wiring, sqlite-vec, single-machine profile, tri-platform CI, UX polish |
| **8** | ⏳ | **Server-as-truth + SSE multicast** (let WPS and Desktop share one conversation stream) |
| 5.y | ⏳ | Redis → cachetools cache layer |
| 5.z | ⏳ | Celery → asyncio.Queue queue layer |
| 6.x | ⏳ | macOS notarize / Windows EV signing / SM2 (Chinese national crypto) / Linux ARM runners |
| 7.x | ⏳ | Data directory copy / verify automation |
| 8.x | ⏳ | Auto update / incremental patches |
| 9 | ⏳ | Mobile / Web client sharing same backend |

---

## 14. Community / Feedback / Commercial

| Channel | Purpose | Address |
|---|---|---|
| Official site | Product info / business | <https://aidooo.com> |
| WeChat OA | Release notes / deep dives | 智灵鸟科技 |
| GitHub Issue (this) | Public technical issues | (internal) |
| GitHub Issue (WPS) | WPS add-in issues | <https://github.com/zhgyuhuii/chayuan/issues> |
| Business email | Enterprise license / OEM / customization | see official site |

**Contributing (brief)**:
1. Open an Issue RFC before any PR
2. Follow CLAUDE.md style and directory conventions
3. Don't touch brand strings (see §2)
4. Run `pnpm typecheck` and `pytest -q` before submitting
5. State the Phase in your PR description

---

## 15. Acknowledgments

This project stands on the shoulders of:

- [Tauri](https://tauri.app/) · [FastAPI](https://fastapi.tiangolo.com/) · [LangChain](https://www.langchain.com/) · [sqlite-vec](https://github.com/asg017/sqlite-vec) · [RapidOCR](https://github.com/RapidAI/RapidOCR) · [bge-m3](https://huggingface.co/BAAI/bge-m3) · [Piper TTS](https://github.com/rhasspy/piper) · [FunASR](https://github.com/modelscope/FunASR) · [Ollama](https://ollama.ai/) · [onnxruntime](https://onnxruntime.ai/) · [React](https://react.dev/) · [TanStack Router/Query](https://tanstack.com/) · [Zustand](https://github.com/pmndrs/zustand) · [Tailwind CSS](https://tailwindcss.com/) · [Radix UI](https://www.radix-ui.com/) · [Shiki](https://shiki.style/) · [Lucide Icons](https://lucide.dev/) · [Marked](https://marked.js.org/) · [DOMPurify](https://github.com/cure53/DOMPurify) · [axios](https://axios-http.com/)

Each component is distributed under its own license. The aggregated work is released under **AGPL-3.0**.

---

## Appendix A: FAQ

**Q1: Why is the default data directory `%APPDATA%\chayuan`?**
A: Each OS has standard "app data" location guidance; Tauri Path API picks the matching one. Users can pick any path at first launch.

**Q2: Can it run with internet disabled?**
A: Depends on your model config. All-local Ollama / LM Studio / vLLM: **fully usable**. Cloud LLM configured: chat unavailable, but KB retrieval / parsing / OCR remain working.

**Q3: Why does the desktop icon say "察元AI" (Chinese) instead of "Chayuan"?**
A: NSIS post-install hook renames the default ASCII shortcut to Chinese after the Finish page. See `chayuan-client/apps/desktop/src-tauri/installer.nsh`.

**Q4: Can I deploy backend on a server and connect from a thin client?**
A: The current Single-Machine Edition is designed for "frontend + backend on same machine". For multi-user / server deployment, see `chayuan-server/packaging/README.md` (separate product line).

**Q5: Must I use chayuan-wps and Desktop together?**
A: No. Both work independently. **When using the same backend, they share KB / models / history.**

**Q6: Does AGPL-3.0 restrict commercial use?**
A: **No restriction on internal use, intranet deployment, in-organization distribution.** Only "deploying a modified version as a SaaS / public service" requires source disclosure of your modifications. For OEM / closed-source bundling, contact business for a separate license.

**Q7: How to use Multi-Model Arena?**
A: In a chat page → top-bar [+ Add] adds a lane → each lane picks its own model → top-bar checkbox "Unified Send" → typing in any lane fans out to all lanes streaming concurrently.

**Q8: Will my data be lost on upgrade?**
A: Uninstaller does **not** touch the real `CHAYUAN_ROOT`. NSIS uninstall only clears the "pointer file" at `%APPDATA%\chayuan\` so the next install can re-pick. Real data (conversations / KB indexes) is preserved.

---

## Appendix B: Glossary

| Term | Meaning |
|---|---|
| **CHAYUAN_ROOT** | User-chosen data directory holding all user data |
| **Sidecar** | Tauri-spawned Python subprocess (chayuan-server) |
| **ku_id** | Knowledge-Universe ID, unified knowledge source identifier (e.g., `doc:ProductDocs`) |
| **Knowledge Universe** | Unified abstraction of doc / struct / vec / office / image source kinds |
| **Model Arena** | Multi-lane side-by-side model comparison |
| **Lane** | A model-arena pane, independent conversationId / model |
| **Composer** | Chat-page bottom input area (model / KB / tools / text input) |
| **MCP** | Model Context Protocol (Anthropic's tool protocol; stdio / sse) |
| **RAG** | Retrieval-Augmented Generation |
| **text2sql** | Natural-language → SQL with AST validation |
| **OCR / TTS / ASR** | Optical character recognition / text-to-speech / speech-to-text |
| **AGPL-3.0** | This repo's license |
| **Stronghold** | Tauri credential vault plugin |
| **Splash** | Startup animation, 5-layer zero-latency mounted |

---

<div align="center">

**Chayuan AI · Desktop (Single-Machine Edition)** · Tauri 2 + React 19 + Python 3.12 + AGPL-3.0

By **Beijing Zhilingniao Technology Center** · <https://aidooo.com>

</div>
