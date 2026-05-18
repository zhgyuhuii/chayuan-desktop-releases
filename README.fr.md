<div align="center">

<img src="chayuan-client/images/logo.png" alt="Chayuan AI" width="120" height="120" />

# Chayuan AI · Édition Bureau (Mode Local)

**Hors-ligne d'abord · Compatible OS Souverains Chinois · Base de Connaissances Locale Complète · Arène Multi-Modèles**

[![Tauri 2](https://img.shields.io/badge/Tauri-2-24c8db?logo=tauri&logoColor=white)](https://tauri.app/)
[![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

</div>

---

## Langues / Languages

| Langue | Document |
|----------|----------|
| 简体中文 (document principal) | [README.md](README.md) |
| English | [README.en.md](README.en.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| Deutsch | [README.de.md](README.de.md) |
| **Français (ce fichier)** | **[README.fr.md](README.fr.md)** |

Guide d'empaquetage détaillé : [PACKAGING.md](PACKAGING.md)

---

## En une phrase

**Chayuan AI Édition Bureau (Mode Local)** est un assistant IA qui **s'installe en un clic et fonctionne sans connexion Internet** —
intégrant un **gateway LLM multi-fournisseurs**, **cinq types de sources de connaissances** (documents / structuré / vectoriel / bureautique / externes),
**30+ outils embarqués**, le **protocole MCP** et un **arène multi-modèles**.
Fonctionne nativement sur **Windows / macOS / Linux** et la **famille des OS souverains chinois** (Kylin, UnionTech UOS, openKylin, deepin).
Toutes les données restent dans un répertoire choisi par l'utilisateur — **les clés API et documents ne quittent jamais la machine**.

---

## Sommaire

- [1. Licence (AGPL-3.0)](#1-licence-agpl-30)
- [2. Préservation de l'identité de marque](#2-préservation-de-lidentité-de-marque)
- [3. Relation avec chayuan-wps (Add-in WPS)](#3-relation-avec-chayuan-wps-add-in-wps)
- [4. Vue d'ensemble du produit et architecture](#4-vue-densemble-du-produit-et-architecture)
- [5. Fonctionnalités principales](#5-fonctionnalités-principales)
- [6. Systèmes d'exploitation pris en charge](#6-systèmes-dexploitation-pris-en-charge)
- [7. Comparaison 60+ critères vs Doubao / Cherry Studio / etc.](#7-comparaison-60-critères-vs-doubao--cherry-studio--etc)
- [8. Liste détaillée des fonctionnalités](#8-liste-détaillée-des-fonctionnalités)
- [9. Aperçu de l'API HTTP](#9-aperçu-de-lapi-http)
- [10. Configuration développeur](#10-configuration-développeur)
- [11. Points d'entrée tutoriels](#11-points-dentrée-tutoriels)
- [12. Sécurité · Confidentialité · Hors-ligne](#12-sécurité--confidentialité--hors-ligne)
- [13. Feuille de route](#13-feuille-de-route)
- [14. Communauté / Retours / Commercial](#14-communauté--retours--commercial)
- [15. Remerciements](#15-remerciements)
- [Annexe A : FAQ](#annexe-a--faq)
- [Annexe B : Glossaire](#annexe-b--glossaire)

---

## 1. Licence (AGPL-3.0)

Ce dépôt est distribué sous la **[GNU Affero General Public License v3.0](LICENSE)**.

> La différence clé avec Apache-2.0 / MIT est la **clause §13 de distribution réseau** :
> si vous offrez une version modifiée comme service réseau à des **utilisateurs externes** (SaaS, cloud public, etc.),
> vous **devez fournir le code source correspondant** (y compris vos modifications) sous la même licence AGPL-3.0.
> Pour un **usage interne, déploiement intranet ou utilisation locale en mode local**, l'obligation de divulgation ne s'applique pas.

**En pratique :**
- ✅ Déploiement personnel / équipe / entreprise interne, intranet, modifications locales : **libre, sans restriction commerciale**
- ✅ Redistribution d'un installateur modifié (en conservant AGPL et copyright) : **autorisé**
- ⚠ Déploiement d'une version modifiée comme **service SaaS / Internet public** : **doit divulguer vos modifications**
- ⚠ Intégration dans votre produit commercial fermé pour revente : **nécessite une licence commerciale séparée**

Pour une **licence commerciale entreprise / OEM marque blanche / contrat de support payant**, contactez-nous via [https://aidooo.com](https://aidooo.com).

### Droits d'auteur

Produit développé et exploité par **Beijing Zhilingniao Technology Center (北京智灵鸟科技中心)**.
Les chaînes UI, modèles de prompt par défaut, icônes et matériel de marque sont protégés par le droit d'auteur, sauf composants tiers sous leurs propres licences.

**Avertissement :** Logiciel fourni « tel quel ». Le contenu généré par LLM peut être inexact ou inapproprié ; les décisions confidentielles, conformes et légales doivent suivre le jugement humain et les procédures officielles.

---

## 2. Préservation de l'identité de marque

Pour préserver la transparence pour l'utilisateur et la traçabilité de l'origine, la marque **« 察元 » (Chayuan)** et ses désignations produit fixes (« 察元 AI », « 察元智库 », « 察元对抗 », etc.) figurant dans l'interface utilisateur final constituent un **élément essentiel de l'origine du produit et des identifiants de marque** :

- Titre de fenêtre, splash screen, dialogue À propos, Paramètres → À propos
- Info-bulle de la zone de notification, nom de raccourci bureau (par défaut : `察元AI.lnk`)
- Texte de marque dans le centre d'aide / dialogue de retour

**Sans autorisation écrite préalable, aucune redistribution ou build personnalisé ne doit modifier, supprimer, masquer, diluer ou réécrire trompeusement le texte de marque « Chayuan » ci-dessus.**

Cette restriction n'**interdit pas** la modification du code source autorisée par AGPL-3.0. Pour le **white-labeling / la localisation totale** touchant les chaînes de marque, obtenez une **autorisation séparée** via le canal commercial.

---

## 3. Relation avec chayuan-wps (Add-in WPS)

Chayuan AI est une **plateforme IA bureautique de bout en bout**, actuellement délivrée par deux projets open source complémentaires :

| Projet | Dépôt | Forme | Utilisateurs principaux |
|---|---|---|---|
| **chayuan-desktop** (ce dépôt) | (interne) | **App bureau mode local** — Tauri 2 + backend Python embarqué | Particuliers / entreprises voulant IA locale uniquement |
| **chayuan-wps** | <https://github.com/zhgyuhuii/chayuan.git> | **Add-in WPS Writer** — Vue 3, tourne dans WPS | Auteurs gouvernementaux / entreprise utilisant fortement WPS |

### Comment ils coopèrent

```
              ┌─────────────────────────────────────────────┐
              │   Chayuan AI Bureau (chayuan-desktop)       │
              │   • chayuan-server embarqué (Python)        │
              │   • KB / gateway modèles / outils / MCP     │
              │   • Écoute sur 127.0.0.1:62581              │
              └─────────────────────┬───────────────────────┘
                                    │  HTTP/REST
                                    ▼
              ┌─────────────────────────────────────────────┐
              │   chayuan-wps  (Add-in WPS, Vue 3 + Vite)   │
              │   • Tourne dans WPS Writer                  │
              │   • Contexte sélection / document complet   │
              │   • Écriture : insérer/remplacer/commenter  │
              └─────────────────────────────────────────────┘
```

**Configuration typique** : Installer **chayuan-desktop** comme « serveur IA » local → sidecar tourne sur `127.0.0.1:62581`. Installer **chayuan-wps** dans WPS → définir « URL serveur » à `http://127.0.0.1:62581`. Les deux partagent **les mêmes bases de connaissances, configurations modèles et historique de conversation**. L'add-in WPS v3.0+ supporte `authMode: 'none'` pour connexion locale sans clé.

---

## 4. Vue d'ensemble du produit et architecture

### 4.1 Ce que c'est

Chayuan AI Bureau intègre un **backend IA de niveau entreprise** dans votre machine locale — installer et utiliser, pas de Docker, pas d'installation Python séparée, pas de Redis / RabbitMQ / PostgreSQL nécessaire. Tous les traitements lourds (appels LLM, indexation KB, recherche vectorielle, exécution d'outils, orchestration streaming) s'effectuent dans des processus locaux.

### 4.2 Architecture trois couches

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend  Tauri 2 + React 19 + Tailwind                          │
│  • Shell multi-onglets style navigateur                           │
│  • Arène Multi-Modèles multi-couloirs                             │
│  • Appels d'outils repliables / panneau citations / reasoning     │
│  • Persistance SQLite locale (plugin Tauri sql)                   │
│  • Coffre-fort Stronghold (ChaCha20-Poly1305 + Argon2id)          │
└──────────────────────┬───────────────────────────────────────────┘
                       │  spawn + sonde /healthz
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Sidecar  chayuan-server (Python 3.12, FastAPI)                   │
│  • Profil mode local : pas de Redis / Celery / PostgreSQL         │
│  • Stockage vectoriel : sqlite-vec (extension SQLite embarquée)   │
│  • Cache : cachetools / Queue : asyncio.Queue                     │
│  • Embeddings : ONNX local (par défaut bge-m3-onnx)               │
│  • OCR : RapidOCR-ONNX (CPU uniquement, ~70 MB)                   │
└──────────────────────┬───────────────────────────────────────────┘
                       │  HTTP / compatible OpenAI
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Externe  Modèles et sources choisis par l'utilisateur            │
│  • Local : Ollama / LM Studio / vLLM / Xinference                 │
│  • LLM Cloud : OpenAI / DeepSeek / Qwen / Zhipu / Wenxin / Kimi…  │
│  • BDD : MySQL / PostgreSQL / Oracle / DM / KingbaseES…           │
│  • Vecteurs externes : Milvus / Chroma / Elasticsearch / Zilliz   │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 Séquence de démarrage

1. Double-clic sur l'icône **察元AI**
2. Fenêtre principale Tauri apparaît instantanément (`backgroundColor = #0f172a`) ; **première frame est l'animation splash** (montage 5 couches zéro latence)
3. Tauri spawn le sous-processus `chayuan-server` embarqué avec `CHAYUAN_ROOT=<répertoire choisi>`
4. Le `SidecarGate` frontend sonde `/healthz` ; UI principale rendue après que le backend soit prêt
5. Assistant premier lancement pour choisir un répertoire de données

---

## 5. Fonctionnalités principales

### 5.1 Hors-ligne d'abord

- **Backend embarqué** : interpréteur Python + tous les wheels + extension sqlite-vec + ressources dans l'installateur
- **Modèles embarqués** : modèle d'embedding bge-m3 quantifié ONNX (~120 MB)
- **OCR embarqué** : poids RapidOCR-ONNX
- **LLM local optionnel** : intégration en un clic avec Ollama / LM Studio / vLLM / Xinference
- **Commande Doctor** : auto-diagnostic

### 5.2 Univers de Connaissances (察元智库)

> **Chemin de requête unifié** — documents, BDD structurées, collections vectorielles externes, KB privées bureautiques et KB d'images sont abstraits en `ku_id`.

- **`POST /api/v1/kb-query/search`** — point de terminaison unique pour recherche hybride multi-sources
- Cinq types de sources : `doc:`, `src:`, `vec:`, `office:`, `img:`
- Format de retour unifié `RetrievalChunk` + `Citation`
- **Recherche hybride** : vecteur + BM25 en parallèle
- **Reranker** : optionnel BAAI/bge-reranker-v2-m3
- **Diagnostics** : `Diagnostic[]` pour résoudre les problèmes

### 5.3 Chat Chayuan

- **Shell multi-onglets style navigateur**
- **Rendu Markdown streaming** avec coloration Shiki + repliage de tokens reasoning
- **Affichage 3 couches des appels d'outils** (résumé → args/sortie → JSON complet)
- **Panneau de citations** : sources KB + étoiles de confiance + 1-clic pour ouvrir l'original
- **Pièces jointes** : drag/coller/cliquer avec auto-OCR
- **Persistance locale des conversations**

### 5.4 Arène Multi-Modèles

- **N couloirs sans limite supérieure**, chaque couloir choisit son propre modèle indépendamment
- **Envoi unifié** : taper dans n'importe quel couloir distribue à tous les couloirs simultanément
- **Opérations couloir** : repli / redimensionner / glisser-réorganiser / ajouter / supprimer
- **Titre du couloir replié** affiche automatiquement la **première question utilisateur** comme étiquette verticale

### 5.5 Marketplace de Modèles

- 7 onglets de catégorie : **Recommandé / Tous / Local / Domestique / International / Agrégé / Personnalisé**
- **Auto-récupération de la liste de modèles** à la saisie de la clé API
- **Auto-promotion du modèle par défaut** par capacité

---

## 6. Systèmes d'exploitation pris en charge

| Catégorie | OS | Architecture | Statut |
|---|---|---|---|
| **Windows** | 10 (1809+) / 11 | x86_64 | ✅ Complet |
| **macOS** | 11 (Big Sur)+ | Apple Silicon (arm64) / Intel (x86_64) | ✅ Complet |
| **Linux** | Ubuntu 22.04+ / Debian 12+ | x86_64 / aarch64 | ✅ Complet |
| **Linux Souverain** | Kylin V10 (麒麟) | x86_64 / aarch64 / LoongArch64 | ✅ Compatible |
| **Linux Souverain** | UnionTech UOS (统信) | x86_64 / aarch64 | ✅ Compatible |
| **Linux Souverain** | openKylin | x86_64 / aarch64 | ✅ Compatible |
| **Linux Souverain** | deepin | x86_64 | ✅ Compatible |
| **Linux Souverain** | openEuler | x86_64 / aarch64 | ⚠ Bundle RPM |

**Écosystème souverain** : BDD (DM / KingbaseES / Doris), LLM (DeepSeek / Qwen / Zhipu / Wenxin / Kimi / Doubao / SiliconFlow / Baichuan / MiniMax), OCR (RapidOCR-ONNX), embedding (bge-m3-onnx).

---

## 7. Comparaison 60+ critères vs Doubao / Cherry Studio / etc.

> Comparaison basée sur les ensembles de fonctionnalités publiquement connus en **2026-05**.

### 7.1 Comparaison nominative (extrait)

| Critère | **Chayuan AI Bureau** | Doubao Bureau | Cherry Studio | ChatGPT Bureau | LM Studio | Open WebUI | AnythingLLM | Chatbox |
|---|---|---|---|---|---|---|---|---|
| Forme | Tauri + Python embarqué (totalement local) | Lié au cloud ByteDance | Electron multi-fournisseurs | OpenAI officiel | Electron inférence locale | UI Web | Electron + Node | Tauri cross-platform |
| LLM hors-ligne | ✅ Ollama/LM Studio/vLLM/Xinference | ❌ | ✅ partiel | ❌ | ✅ principal | ✅ avec Ollama | ✅ partiel | ✅ partiel |
| KB locale (RAG) | ✅ 5 types, sqlite-vec embarqué | ✅ mais routé cloud | ✅ unique (vecteur) | ❌ | ❌ | ✅ externe | ✅ unique | partiel |
| Connecteurs BDD (text2sql) | ✅ 17 dialectes | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Connecteurs vectoriels externes | ✅ Milvus/Chroma/ES/Zilliz | ❌ | ❌ | ❌ | ❌ | partiel | partiel | ❌ |
| Protocole MCP | ✅ stdio + sse, serveur & client | ❌ | ✅ client | ❌ | ❌ | partiel | ❌ | partiel |
| Outils embarqués | **30+** | peu | peu | plugins OpenAI | ❌ | ❌ | peu | ❌ |
| Arène Multi-Modèles | ✅ illimité + envoi unifié | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Écosystème souverain CN | ✅ Kylin/UOS + DM/Doris + LLM CN | ✅ stack ByteDance | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Résidence des données | **Répertoire utilisateur local** | Cloud ByteDance | Local + clouds | Cloud OpenAI | Local | Auto-hébergé | Local | Local |
| Licence | **AGPL-3.0** | Fermée | Apache-2.0 | Fermée | Fermée | MIT | MIT | GPL-3.0 |

### 7.2 60+ critères détaillés (Chayuan AI vs client IA bureau générique)

| # | Dimension | Chayuan AI Bureau | Client IA bureau générique |
|---:|---|---|---|
| 1 | Forme installateur | Tauri natif + sidecar Python embarqué | Electron / shell navigateur |
| 2 | Taille installateur | Moyenne (runtime Python + modèles par défaut) | Petite (uniquement front) |
| 3 | Internet requis au lancement | ❌ | ✅ généralement |
| 4 | Animation splash | Montage 5 couches zéro latence | Souvent présent |
| 5 | Stockage vectoriel embarqué | ✅ sqlite-vec | ❌ externe |
| 6 | Modèle embedding embarqué | ✅ ONNX bge-m3 | ❌ utilisateur configure |
| 7 | OCR embarqué | ✅ RapidOCR-ONNX | ❌ |
| 8 | TTS / ASR embarqué | ✅ Piper / FunASR (optionnel) | ❌ |
| 9 | Étendue RAG documents | ✅ PDF/Word/Excel/PPT/MD/HTML/image | limitée |
| 10 | Gateway multi-fournisseurs | ✅ 18+ fournisseurs | 5–10 |
| 11 | Routes compatibles OpenAI | ✅ intégrées `/openai/v1/*` | partiel |
| 12 | Auto-détection modèles fournisseur | ✅ Clé→blur→`/v1/models` | partiel |
| 13 | Auto-promotion modèle par défaut par capacité | ✅ chat/embed/image/rerank | ❌ |
| 14 | Catégories de modèles | chat/embed/vision/image-gen/rerank/audio/vidéo | majoritairement chat + embed |
| 15 | Arène Multi-Modèles | ✅ illimité + envoi unifié | ❌ |
| 16 | Affichage 3 couches appels d'outils | ✅ | ❌ |
| 17 | Repliage reasoning streaming | ✅ | ❌ |
| 18 | Puces de citation (sources KB) | ✅ confiance + 1-clic | partiel |
| 19 | Citations par type de source | ✅ doc/struct/vec/office/web | ❌ |
| 20 | Sécurité text2sql | ✅ AST lecture seule + whitelist | ❌ |
| 21 | Support BDD souveraines | ✅ DM / KingbaseES / Doris | ❌ |
| 22 | Support BDD internationales | ✅ MySQL/PG/Oracle/SQL Server/ClickHouse/Hive | majoritairement 0–1 |
| 23 | Connecteur MongoDB | ✅ | ❌ |
| 24 | Connecteur Elasticsearch | ✅ | partiel |
| 25 | Connecteurs vectoriels externes | ✅ Milvus/Chroma/Zilliz/PG-vector | ❌ ou 1 |
| 26 | Client MCP | ✅ stdio + sse | partiel |
| 27 | Serveur MCP | ✅ peut être serveur MCP | ❌ |
| 28 | Nombre d'outils embarqués | 30+ | 0–10 |
| 29 | Outil HTTP personnalisé (OpenAPI) | ✅ parsing Swagger | partiel |
| 30 | Outil script personnalisé | ✅ Python REPL / Shell | partiel |
| 31 | Nombre de types KB | 5 (doc/struct/vec/office/img) | majoritairement 1 |
| 32 | Étendue formats documents | PDF/Word/Excel/PPT/MD/HTML/image/CSV | majoritairement PDF + Word |
| 33 | Sync incrémentale dossier | ✅ route folder-sync | partiel |
| 34 | Construction auto structure de connaissances | ✅ watch + parse + ingestion | ❌ |
| 35 | Recherche KB parallèle | ✅ `selectedKuIds` | partiel |
| 36 | Support OS souverain | ✅ Kylin / UOS / openKylin / deepin | ❌ |
| 37 | Multi-architecture | x86_64 / aarch64 / loongarch64 | majoritairement x86_64 seulement |
| 38 | Répertoire de données utilisateur configurable | ✅ assistant FirstRunSetup | majoritairement fixe |
| 39 | Auth basculable | ✅ mode local auto désactivé | ❌ (pas de concept) |
| 40 | Chiffrement identifiants | ✅ Tauri Stronghold | partiel |
| 41 | API appelable de l'extérieur | ✅ expose `/api/*` | ❌ |
| 42 | Intégration add-in WPS même machine | ✅ chayuan-wps v3.0 | ❌ |
| 43 | Auth HMAC OpenAPI | ✅ X-App-Id / X-Sign | ❌ |
| 44 | SDK OpenAI directement vers local | ✅ | partiel |
| 45 | Chat parallèle multi-onglets | ✅ style navigateur | partiel |
| 46 | Drag onglet / menu contextuel | ✅ | partiel |
| 47 | Thème (sombre/clair/système) | ✅ | majoritaire |
| 48 | Taille de police personnalisée | ✅ persistante | partiel |
| 49 | i18n | ✅ zh / en / ja / de / fr | partiel |
| 50 | Centre d'aide (markdown embarqué) | ✅ | partiel |
| 51 | Canal de retour | ✅ QR + GitHub Issue | partiel |
| 52 | Mise à jour automatique | ⏳ planifié | majoritaire |
| 53 | Zone de notification | ✅ | majoritaire |
| 54 | Raccourci global | ✅ | partiel |
| 55 | Notification bureau | ✅ | majoritaire |
| 56 | Upload fichier glisser-déposer | ✅ | majoritaire |
| 57 | Intégration presse-papier | ✅ | majoritaire |
| 58 | Observabilité (Langfuse) | ✅ optionnel, off par défaut | partiel |
| 59 | Module d'évaluation | ✅ éval KB recall | ❌ |
| 60 | Audit / quota / RBAC | ✅ module governance | ❌ |
| 61 | Masquage PII | ✅ governance/redact | ❌ |
| 62 | Lignée de données | ✅ lineage | ❌ |
| 63 | Multi-utilisateur / multi-tenancy | ✅ (bascule profil) | ❌ |
| 64 | Doctor développeur | ✅ `chayuan doctor` | partiel |
| 65 | Licence open-source | **AGPL-3.0** | varie |

---

## 8. Liste détaillée des fonctionnalités

### 8.1 Chat & streaming

Streaming SSE, repliage deep-think, appels d'outils 3 couches, traitement de pièces jointes, persistance locale, édition/régénération/branche, envoi unifié, défilement virtualisé, Enter sécurisé IME.

### 8.2 Types de KB & formats de document

Types : `doc` / `struct` / `vec` / `office` / `img` (cf §5.2).

Formats : PDF, DOCX, DOC, XLSX, XLS, CSV, PPTX, MD, HTML, TXT, PNG/JPG/BMP/TIFF, JSON/YAML/TOML, EML/MSG, EPUB.

### 8.3 Connecteurs BDD (données structurées)

17 dialectes SQL : **International** (MySQL, PostgreSQL, SQLite, MS SQL Server, Oracle, ClickHouse, Hive), **Souverain** (DM, KingbaseES, Apache Doris), **Document/Plein-texte** (MongoDB, Elasticsearch).

**Sécurité** : validation AST lecture seule, whitelist tables/colonnes.

### 8.4 Stockages vectoriels

sqlite-vec (par défaut, embarqué), FAISS, Milvus / Lite, Chroma, Zilliz, Elasticsearch, PostgreSQL + pgvector, Relyt.

### 8.5 Fournisseurs de modèles

**Cloud (international)** : OpenAI, Anthropic Claude, Gemini, Mistral, Together, Groq.
**Cloud (souverain)** : DeepSeek, Qwen / Dashscope, Wenxin, Zhipu GLM, Moonshot Kimi, Doubao, Baichuan, MiniMax, Yi, SiliconFlow.
**Local / compatible OpenAI** : Ollama, LM Studio, Xinference, vLLM, OneAPI, FastChat, LocalAI.

### 8.6 Embeddings / Rerankers / OCR

Embeddings : bge-m3-onnx (par défaut), Ollama, Infinity, OpenAI, Dashscope, ZhipuAI, Jina, Cohere.
Rerankers : BAAI/bge-reranker-v2-m3 (par défaut).
OCR : RapidOCR-ONNX (par défaut, CPU, ~70 MB).

### 8.7 Multimodal

T2I, Vision LLM, TTS (Piper, ~30 MB), ASR (FunASR), T2V.

### 8.8 Outils embarqués (30+)

Données/Connaissances, Code/Système, Académique, DevOps, Messagerie, Temps réel/Géo, Générique, Multimodal — chacun riche (cf document principal).

### 8.9 MCP

Client et serveur tous deux supportés (stdio / sse).

### 8.10 Arène Multi-Modèles

Couloirs illimités, envoi unifié, layout persistant, titre du couloir replié = première question.

### 8.11 Construction auto structure de connaissances (Folder Sync)

Endpoint : `/api/folder-sync/*`. Watch + parse + OCR + chunk + embedding + ingestion en une étape.

### 8.12 Affichage des citations

Étoiles de confiance + icônes type de source + actions 1-clic.

---

## 9. Aperçu de l'API HTTP

Backend : `127.0.0.1:62581`. Swagger : `http://127.0.0.1:62581/docs`.

Routes principales : `/healthz`, `/api/chat/*`, `/api/v1/kb-query/*` (recommandé), `/api/kb/*`, `/api/folder-sync/*`, `/api/image/*`, `/api/voice/*`, `/api/tool/*`, `/api/mcp/*`, `/api/governance/*`, `/api/admin/*`, `/api/provider/*`, `/openai/v1/*` (compatible OpenAI), `/openapi/v1/*` (HMAC).

```python
# SDK OpenAI directement vers local
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:62581/openai/v1", api_key="anything")
resp = client.chat.completions.create(model="qwen2.5:7b", messages=[...], stream=True)
```

---

## 10. Configuration développeur

Complet : [PACKAGING.md](PACKAGING.md). Démarrage rapide :

| Outil | Version |
|---|---|
| Python | 3.12 |
| Poetry | ≥ 1.8 |
| Node.js | 22 |
| pnpm | 9 |
| Rust | stable |

```bash
cd chayuan-server && poetry install && poetry run chayuan start -a --single-machine
cd chayuan-client && pnpm install && pnpm dev:desktop
```

```powershell
# Windows
.\build-desktop.cmd
.\build-desktop.cmd -BundleOnly
```

```bash
# macOS / Linux
./build-desktop.sh
./build-desktop.sh --bundle-only
```

---

## 11. Points d'entrée tutoriels

- Centre d'aide intégré (Sidebar → Aide)
- À propos intégré (Paramètres → À propos)
- Site officiel : <https://aidooo.com>
- Add-in WPS : <https://github.com/zhgyuhuii/chayuan/blob/main/README.md>
- Guide d'empaquetage : [PACKAGING.md](PACKAGING.md)

---

## 12. Sécurité · Confidentialité · Hors-ligne

- **Résidence des données** : tout dans `CHAYUAN_ROOT` (choisi par l'utilisateur). Aucun upload aux serveurs Chayuan.
- **Identifiants** : Tauri Stronghold (ChaCha20-Poly1305 + Argon2id) chiffré
- **Sortie réseau** : entièrement hors-ligne possible ; LLM cloud configuré → uniquement endpoint API du modèle
- **Audit** : module governance (audit log / masquage PII / lignée de données)

---

## 13. Feuille de route

| Phase | Statut | Contenu |
|---|---|---|
| 1–7 | ✅ | Assistant premier lancement, PyInstaller, sidecar wiring, sqlite-vec, profil mode local, CI 3 plateformes |
| **8** | ⏳ | **Server-as-truth + multicast SSE** |
| 6.x | ⏳ | macOS notarize / signature EV Windows / SM2 crypto / runners ARM Linux |
| 8.x | ⏳ | Mise à jour auto / patches incrémentaux |
| 9 | ⏳ | Mobile / client Web partageant même backend |

---

## 14. Communauté / Retours / Commercial

| Canal | Usage | Adresse |
|---|---|---|
| Site officiel | Produit / commercial | <https://aidooo.com> |
| WeChat OA | Notes de version | 智灵鸟科技 |
| GitHub Issue (ce dépôt) | Questions techniques publiques | (interne) |
| GitHub Issue (WPS) | Add-in WPS | <https://github.com/zhgyuhuii/chayuan/issues> |
| E-mail commercial | Licence entreprise / OEM | via site officiel |

---

## 15. Remerciements

[Tauri](https://tauri.app/) · [FastAPI](https://fastapi.tiangolo.com/) · [LangChain](https://www.langchain.com/) · [sqlite-vec](https://github.com/asg017/sqlite-vec) · [RapidOCR](https://github.com/RapidAI/RapidOCR) · [bge-m3](https://huggingface.co/BAAI/bge-m3) · [Piper TTS](https://github.com/rhasspy/piper) · [FunASR](https://github.com/modelscope/FunASR) · [Ollama](https://ollama.ai/) · [onnxruntime](https://onnxruntime.ai/) · [React](https://react.dev/) · [TanStack](https://tanstack.com/) · [Zustand](https://github.com/pmndrs/zustand) · [Tailwind CSS](https://tailwindcss.com/) · [Radix UI](https://www.radix-ui.com/) · [Shiki](https://shiki.style/) · [Lucide Icons](https://lucide.dev/) · [Marked](https://marked.js.org/) · [DOMPurify](https://github.com/cure53/DOMPurify)

Chaque composant est distribué sous sa propre licence. L'œuvre agrégée est sous **AGPL-3.0**.

---

## Annexe A : FAQ

**Q1 : Où est le répertoire de données par défaut ?** —— `~/Library/Application Support/chayuan` (macOS) / `%APPDATA%\chayuan` (Win) / `~/.local/share/chayuan` (Linux). Configurable au premier lancement.

**Q2 : Fonctionne sans Internet ?** —— Avec LLM local (Ollama, etc.) : pleinement opérationnel. Avec LLM cloud configuré : recherche KB / parsing / OCR fonctionnels, seul le chat est désactivé.

**Q3 : Pourquoi l'icône s'appelle-t-elle "察元AI" (chinois) ?** —— Hook NSIS post-install renomme le raccourci ASCII par défaut après la page Finish.

**Q4 : Puis-je déployer le backend séparément ?** —— L'édition mode local actuelle est conçue pour "frontend + backend même machine". Pour déploiement multi-utilisateur / serveur, voir `chayuan-server/packaging/README.md`.

**Q5 : Dois-je utiliser chayuan-wps et Bureau ensemble ?** —— Non, les deux fonctionnent indépendamment. Avec le même backend : KB / modèles / historique partagés.

**Q6 : AGPL-3.0 limite-t-elle l'usage commercial ?** —— **Aucune restriction pour usage interne / intranet / distribution interne.** Seul "déployer une version modifiée comme SaaS" requiert la divulgation. Pour OEM / bundling fermé, contacter pour licence commerciale séparée.

**Q7 : Comment utiliser l'Arène Multi-Modèles ?** —— Page chat → topbar [+ Ajouter] → chaque couloir choisit son modèle → checkbox "Envoi unifié" → taper dans n'importe quel couloir distribue à tous.

**Q8 : Les données sont-elles perdues lors de la mise à jour ?** —— Le désinstalleur **ne touche pas** `CHAYUAN_ROOT`. Seul le fichier pointeur dans `%APPDATA%\chayuan\` est nettoyé. Les vraies données sont préservées.

---

## Annexe B : Glossaire

| Terme | Signification |
|---|---|
| CHAYUAN_ROOT | Répertoire de données choisi par l'utilisateur |
| Sidecar | Sous-processus Python spawné par Tauri |
| ku_id | Knowledge-Universe ID |
| Knowledge Universe | Abstraction unifiée de 5 types de sources |
| Model Arena | Comparaison multi-couloirs de modèles |
| Lane | Un panneau d'arène modèle |
| Composer | Zone d'entrée bas de page chat |
| MCP | Model Context Protocol (Anthropic) |
| RAG | Retrieval-Augmented Generation |
| text2sql | NL → SQL avec validation AST |
| Stronghold | Plugin coffre-fort identifiants Tauri |
| Splash | Animation de démarrage, montage 5 couches |

---

<div align="center">

**Chayuan AI · Édition Bureau (Mode Local)** · Tauri 2 + React 19 + Python 3.12 + AGPL-3.0

Par **Beijing Zhilingniao Technology Center** · <https://aidooo.com>

</div>
