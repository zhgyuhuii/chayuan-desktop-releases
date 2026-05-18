<div align="center">

<img src="chayuan-client/images/logo.png" alt="Chayuan AI" width="120" height="120" />

# 察元 AI · デスクトップ単機版 (Chayuan AI Desktop)

**オフライン優先 · 国産OS対応 · フルスタック ローカルナレッジベース · マルチモデル対戦**

[![Tauri 2](https://img.shields.io/badge/Tauri-2-24c8db?logo=tauri&logoColor=white)](https://tauri.app/)
[![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

</div>

---

## Language / 言語

| Language | Document |
|----------|----------|
| 简体中文(主文档) | [README.md](README.md) |
| English | [README.en.md](README.en.md) |
| **日本語 (このファイル)** | **[README.ja.md](README.ja.md)** |
| Deutsch | [README.de.md](README.de.md) |
| Français | [README.fr.md](README.fr.md) |

詳細なパッケージングガイド: [PACKAGING.md](PACKAGING.md)

---

## 一行で説明

**察元 AI デスクトップ単機版** は、**インストールするだけで、インターネット接続なしで動作する** AI アシスタントです。
**マルチベンダー LLM ゲートウェイ**、**5 種類のナレッジソース**(ドキュメント / 構造化 / ベクトル / オフィス / 外部)、
**30+ の組込ツール**、**MCP プロトコル**対応、**マルチモデル対戦** を内蔵。
**Windows / macOS / Linux** および中国国産 OS ファミリ(**麒麟 / 統信 UOS / openKylin / deepin**)にネイティブ対応。
すべてのデータはユーザーが選択したディレクトリに保存され、**API キーやドキュメントが端末から出ることはありません**。

---

## 目次

- [1. ライセンス (AGPL-3.0)](#1-ライセンス-agpl-30)
- [2. ブランド表示の保持要件](#2-ブランド表示の保持要件)
- [3. chayuan-wps (WPS アドイン) との関係](#3-chayuan-wps-wps-アドイン-との関係)
- [4. プロダクト概要とシステムアーキテクチャ](#4-プロダクト概要とシステムアーキテクチャ)
- [5. 主要機能](#5-主要機能)
- [6. 対応OS](#6-対応os)
- [7. 豆包 / Cherry Studio などとの 60+ 項比較](#7-豆包--cherry-studio-などとの-60-項比較)
- [8. 機能一覧](#8-機能一覧)
- [9. HTTP API 概要](#9-http-api-概要)
- [10. 開発者向けセットアップ](#10-開発者向けセットアップ)
- [11. チュートリアル入口](#11-チュートリアル入口)
- [12. セキュリティ · プライバシー · オフライン](#12-セキュリティ--プライバシー--オフライン)
- [13. ロードマップ](#13-ロードマップ)
- [14. コミュニティ / フィードバック / 商用](#14-コミュニティ--フィードバック--商用)
- [15. 謝辞](#15-謝辞)
- [付録 A: FAQ](#付録-a-faq)
- [付録 B: 用語集](#付録-b-用語集)

---

## 1. ライセンス (AGPL-3.0)

本リポジトリは **[GNU Affero General Public License v3.0](LICENSE)** の下で公開されています。

> Apache-2.0 / MIT との主な違いは **§13 のネットワーク配信条項** にあります。
> 本ソフトウェアを改変したバージョンを **外部ユーザー向けのネットワークサービス**(SaaS、パブリッククラウドなど)として提供する場合、
> 当該ユーザーに対して **対応するソースコード**(改変分を含む)を同じ AGPL-3.0 で公開する義務があります。
> **組織内部利用、イントラネット配備、ローカル単機利用** ではこの開示要件は発動しません。

**実用上:**
- ✅ 個人 / チーム / 企業の内部展開、イントラネット、ローカル改変: **自由、商用制限なし**
- ✅ 改変済みインストーラの再配布(AGPL と著作権表示を保持): **可**
- ⚠ 改変済みバージョンを **SaaS / 公開ネットサービス** として配信: **改変分の公開が必要**
- ⚠ クローズドソース商品にバンドルして再販: **別途商用ライセンスが必要**

**企業商用ライセンス / OEM ホワイトラベル / 有償サポート契約** については [https://aidooo.com](https://aidooo.com) よりお問い合わせください。

### 著作権

本プロダクトは **北京智灵鸟科技中心 (Beijing Zhilingniao Technology Center)** が研究開発・運営しています。
UI 文言、デフォルトプロンプト、アイコン、ブランド素材などは、第三者コンポーネントが各々のライセンスに従う場合を除き、著作権法その他の知的財産権法で保護されます。

**免責事項:** 本ソフトウェアは「現状のまま」提供されます。LLM 生成内容に不正確・不適切な部分があり得ます。機密性、コンプライアンス、法的判断は人間と正式な手続きに従うべきです。「機密チェック」「AI 痕跡チェック」等の機能はあくまで **補助参考** であり、正式な認定ではありません。

---

## 2. ブランド表示の保持要件

ユーザーの知る権利、出所追跡可能性、ブランド一貫性を確保するため、エンドユーザー向け UI における **「察元」(Chayuan)** およびその固定的な製品名(「察元 AI」「察元 AI 助手」「察元智库」「察元对抗」「Chayuan について」など)は、**プロダクト出所およびブランド識別子の重要な構成要素** です:

- アプリ ウィンドウタイトル、Splash、Aboutダイアログ、設定 → Aboutページ
- システムトレイのツールチップ、デスクトップショートカット名(既定: `察元AI.lnk`)
- ヘルプセンター / フィードバック ダイアログのブランド文言
- 上記と同じ意味連鎖にあるユーザー可視文字列

**事前の書面承認なしに、再配布版またはカスタマイズ版において上記「察元」ブランド表示を置換、削除、隠蔽、希釈、または誤認を招く形で書き換えてはなりません**。

この制限は AGPL-3.0 が認める「ソースコード改変」自体を禁止するものでは **ありません** —— 内部ビルドでロジックを自由に変更できます。ただし、**エンドユーザー向けに配布可能なバイナリ**を第三者に提供する場合は、ブランド表示の顕著性を保持するか、所定の方式で別の出所を明示する書面承諾を事前に取得する必要があります。

---

## 3. chayuan-wps (WPS アドイン) との関係

察元 AI は **エンドツーエンドのオフィス AI プラットフォーム** で、現在は 2 つの相補的なオープンソースプロジェクトで提供されています:

| プロジェクト | リポジトリ | 形態 | 主なユーザー |
|---|---|---|---|
| **chayuan-desktop** (本リポジトリ) | (内部) | **デスクトップ単機アプリ** —— Tauri 2 シェル + 組込 Python バックエンド | API キー / ドキュメントを外部に出したくない個人・組織 |
| **chayuan-wps** | <https://github.com/zhgyuhuii/chayuan.git> | **WPS Writer アドイン** —— Vue 3 アドイン、WPS 内で動作 | WPS で公文書 / 契約 / 入札書類を作成する企業ユーザー |

### 連携方法

```
              ┌─────────────────────────────────────────────┐
              │   察元 AI Desktop (chayuan-desktop)         │
              │   • 組込 chayuan-server (Python)            │
              │   • KB / モデルゲートウェイ / ツール / MCP   │
              │   • 127.0.0.1:62581 でリッスン              │
              │   • データは CHAYUAN_ROOT に保存             │
              └─────────────────────┬───────────────────────┘
                                    │  HTTP/REST
                                    ▼
              ┌─────────────────────────────────────────────┐
              │   chayuan-wps (WPS アドイン, Vue 3 + Vite)  │
              │   • WPS Writer 内で動作                     │
              │   • 選択範囲 / 全文コンテキスト             │
              │   • 書き戻し: 挿入 / 置換 / コメント        │
              │   • Desktop の /api/* で AI と KB を呼出    │
              └─────────────────────────────────────────────┘
```

**典型的なセットアップ**: PC に **chayuan-desktop** をインストール → サイドカーが `127.0.0.1:62581` で動作。WPS に **chayuan-wps** アドインをインストール → 「サーバー URL」を `http://127.0.0.1:62581` に設定。両者は **同じナレッジベース、モデル設定、会話履歴** を共有します。WPS で開始した会話は Desktop クライアントの履歴にも表示されます。

WPS アドイン (v3.0+) は単機モードでキーレス接続できる `authMode: 'none'` をサポート。詳細は [chayuan-wps README](https://github.com/zhgyuhuii/chayuan/blob/main/README.md) をご覧ください。

---

## 4. プロダクト概要とシステムアーキテクチャ

### 4.1 概要

察元 AI Desktop は **エンタープライズグレードの AI バックエンド** をローカル PC に詰め込みました —— インストールしたらすぐ使える、Docker 不要、別途 Python のインストール不要、Redis / RabbitMQ / PostgreSQL も不要。すべての重い処理(LLM 呼出、KB インデキシング、ベクトル検索、ツール実行、ストリーム編成)はローカルプロセスで完結します。フロントエンドはネイティブウィンドウの React アプリ、バックエンドはインストーラに組み込まれた Python サイドカーです。

### 4.2 三層アーキテクチャ

```
┌──────────────────────────────────────────────────────────────────┐
│  フロントエンド  Tauri 2 + React 19 + Tailwind                    │
│  • ブラウザ風マルチタブ シェル(ホーム/チャット/KB/モデル広場)     │
│  • マルチレーン モデル対戦                                        │
│  • 折りたたみツール呼出 / 引用パネル / ストリーム reasoning       │
│  • ローカル SQLite 会話永続化(Tauri sql プラグイン)             │
│  • Stronghold 資格情報金庫(ChaCha20-Poly1305 + Argon2id)       │
└──────────────────────┬───────────────────────────────────────────┘
                       │  spawn + /healthz プローブ
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  サイドカー  chayuan-server (Python 3.12, FastAPI)                │
│  • 単機 profile: Redis/Celery/PostgreSQL なし                     │
│  • ベクトル: sqlite-vec(組込 SQLite 拡張)/ FAISS                │
│  • キャッシュ: cachetools / キュー: asyncio.Queue                 │
│  • 埋め込み: ONNX ローカル(既定 bge-m3-onnx) / Ollama / OpenAI │
│  • OCR: RapidOCR-ONNX(CPU のみ、~70 MB)                         │
└──────────────────────┬───────────────────────────────────────────┘
                       │  HTTP / OpenAI 互換
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  外部  ユーザー選択のモデルとデータソース                          │
│  • ローカル: Ollama / LM Studio / vLLM / Xinference              │
│  • クラウド LLM: OpenAI / DeepSeek / Qwen / Zhipu / Wenxin / Kimi │
│  • DB: MySQL / PostgreSQL / Oracle / DM / KingbaseES…            │
│  • 外部ベクトル: Milvus / Chroma / Elasticsearch / Zilliz        │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 起動シーケンス

1. ユーザーが **察元AI** デスクトップアイコンをダブルクリック
2. Tauri メインウィンドウが瞬時に表示(`backgroundColor = #0f172a`)、**最初のフレームでスプラッシュアニメーション** が再生(5 層ゼロレイテンシ マウント)
3. Tauri が組込 `chayuan-server` 子プロセスを spawn(`CHAYUAN_ROOT=<ユーザー選択ディレクトリ>` を注入)
4. フロントエンド `SidecarGate` が `/healthz` をポーリングし、バックエンドが ready になったらメイン UI を描画
5. 初回起動ウィザードでデータディレクトリを選択、以後はそれを再利用

---

## 5. 主要機能

### 5.1 オフライン優先

- **組込バックエンド**: Python ランタイム + 全 wheels + sqlite-vec 拡張 + リソースをインストーラに同梱
- **組込モデル**: ONNX 量子化 bge-m3 埋め込みモデル(~120 MB)を同梱
- **組込 OCR**: RapidOCR-ONNX 重みを同梱
- **オプション ローカル LLM**: Ollama / LM Studio / vLLM / Xinference にワンクリック接続
- **Doctor コマンド**: データディレクトリ・sqlite-vec 拡張・埋め込みモデルの整合性をセルフ診断

### 5.2 Knowledge Universe(察元智库)

> **統一クエリパス** —— ドキュメント、構造化 DB、外部ベクトル、オフィス私库、画像 KB の 5 種を `ku_id` で統一抽象化。
> フロントエンドが種別を選択するだけで、サーバーが該当する検索アダプタにルーティング。

- **`POST /api/v1/kb-query/search`** —— 単一エンドポイントで多源ハイブリッド検索
- 5 つのソース種別:
  - **`doc:<kb_name>`** —— ドキュメント KB(PDF / Word / Excel / Markdown / HTML / 画像…)
  - **`src:<source_id>`** —— 構造化(SQL / MongoDB / Elasticsearch)
  - **`vec:<collection>`** —— 外部ベクトル(Milvus / Chroma / Zilliz)
  - **`office:<owner>[:<group>]`** —— オフィス私库(企業 / チーム / 個人 三層)
  - **`img:<kb_name>`** —— 画像 KB(CLIP 埋め込み)
- 統一 `RetrievalChunk` + `Citation` で原文リンク / 信頼度 / 生成 SQL を返却
- **ハイブリッド検索**: ベクトル + BM25 並行、加重スコア
- **リランカー**: BAAI/bge-reranker-v2-m3 など オプション
- **診断情報**: 各クエリで `Diagnostic[]` を返し「答えがおかしい」原因を特定

### 5.3 察元 AI チャット

- **ブラウザ風マルチタブ シェル**: タブごとに会話 ID / モデル / KB 選択を独立保持
- **ストリーミング Markdown レンダリング**(Shiki シンタックスハイライト + reasoning トークン折りたたみ)
- **3 層折りたたみ式ツール呼出表示**(サマリチップ → args/output → フル JSON)
- **引用パネル**: KB ソース一覧 + 信頼度星 + ワンクリックで原文オープン / ダウンロード
- **添付**: ドラッグ / ペースト / クリック、自動 OCR・解析・コンテキスト投入
- **会話のローカル永続化**: Tauri SQLite プラグイン

### 5.4 マルチモデル対戦 (Model Arena)

- **N レーン無制限**、レーンごとにモデル独立選択
- **統一送信**: チェック ON で、いずれかのレーンに入力 → 全レーンへ同時送出
- **レーン操作**: 折りたたみ / 幅調整 / ドラッグ並べ替え / 追加 / 削除
- **折りたたみタブの見出し**: そのレーンの **最初のユーザー質問** を縦書きラベルとして自動表示

### 5.5 モデル広場

- 7 つのカテゴリタブ: **おすすめ / 全部 / ローカル / 国内 / 海外 / 集約 / カスタム**
- **モデル一覧の自動取得**: API キー入力 → blur → `/v1/models` を自動呼出
- **既定モデルの自動候補**: 設定で未指定の場合、能力ごとに第一候補を自動採用

---

## 6. 対応OS

| カテゴリ | OS | アーキテクチャ | 状態 |
|---|---|---|---|
| **Windows** | 10 (1809+) / 11 | x86_64 | ✅ 完全対応 |
| **macOS** | 11 (Big Sur)+ | Apple Silicon (arm64) / Intel (x86_64) | ✅ 完全対応 |
| **Linux** | Ubuntu 22.04+ / Debian 12+ | x86_64 / aarch64 | ✅ 完全対応 |
| **国産 Linux** | 麒麟 (Kylin) V10 | x86_64 / aarch64 / LoongArch64 | ✅ 互換 |
| **国産 Linux** | 統信 UOS (UnionTech OS) | x86_64 / aarch64 | ✅ 互換 |
| **国産 Linux** | openKylin | x86_64 / aarch64 | ✅ 互換 |
| **国産 Linux** | deepin | x86_64 | ✅ 互換 |
| **国産 Linux** | openEuler | x86_64 / aarch64 | ⚠ RPM 配布物 |

**国産エコシステム対応**: DM / KingbaseES / Doris(国産 DB)、DeepSeek / Qwen / Zhipu / Wenxin / Kimi / Doubao(国産 LLM)、RapidOCR-ONNX(国産 OCR)、bge-m3-onnx(国産埋め込み)。

---

## 7. 豆包 / Cherry Studio などとの 60+ 項比較

> 比較は **2026-05** 時点の各製品の公開機能セットに基づく。料金、データ所在、企業向けプライベート展開、規制認証は **各ベンダー公式ドキュメント** を参照してください。

### 7.1 名指し製品比較(横向き、抜粋)

| 項目 | **察元 AI Desktop** | 豆包 Desktop | Cherry Studio | ChatGPT Desktop | LM Studio | Open WebUI | AnythingLLM | Chatbox |
|---|---|---|---|---|---|---|---|---|
| 形態 | Tauri + 組込 Python(完全ローカル) | ByteDance クラウドバインド | Electron 多ベンダー | OpenAI 公式 | Electron ローカル推論 | Web UI | Electron + Node | Tauri クライアント |
| オフライン LLM | ✅ | ❌ | ✅一部 | ❌ | ✅主力 | ✅Ollama併用 | ✅一部 | ✅一部 |
| ローカル KB | ✅ 5 種ソース、組込 sqlite-vec | ✅ クラウド経由 | ✅ 単一(ベクトル) | ❌ | ❌ | ✅ 外部 | ✅ 単一 | 一部 |
| DB コネクタ(text2sql) | ✅ 17 方言 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 外部ベクトル | ✅ Milvus / Chroma / ES / Zilliz / PG-vector | ❌ | ❌ | ❌ | ❌ | 一部 | 一部 | ❌ |
| MCP プロトコル | ✅ stdio + sse、サーバー & クライアント両対応 | ❌ | ✅ クライアント | ❌ | ❌ | 一部 | ❌ | 一部 |
| 組込ツール数 | **30+** | 少 | 少 | OpenAI プラグイン | ❌ | ❌ | 少 | ❌ |
| マルチモデル対戦 | ✅ 無制限 + 統一送信 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 国産化 | ✅ 麒麟 / UOS + DM / Doris + 国産 LLM | ✅(ByteDance) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| データ所在 | **ローカルのユーザーディレクトリ** | ByteDance クラウド | ローカル + 各クラウド | OpenAI クラウド | ローカル | セルフホスト | ローカル | ローカル |
| ライセンス | **AGPL-3.0** | クローズド | Apache-2.0 | クローズド | クローズド | MIT | MIT | GPL-3.0 |

### 7.2 60 項比較(察元 AI vs 一般的なデスクトップ AI クライアント)

| # | 比較軸 | 察元 AI Desktop | 一般的デスクトップ AI クライアント |
|---:|---|---|---|
| 1 | インストーラ形態 | Tauri + 組込 Python サイドカー | Electron / ブラウザシェル |
| 2 | サイズ | 中(Python ランタイム + 既定モデルを同梱) | 小(フロント のみ) |
| 3 | 起動時にネット必須か | ❌ | ✅ 多数 |
| 4 | スプラッシュアニメ | 5 層ゼロレイテンシ マウント | 多数あり |
| 5 | 組込ベクトルストア | ✅ sqlite-vec | ❌ 外部 |
| 6 | 組込埋め込みモデル | ✅ ONNX bge-m3 | ❌ ユーザー設定 |
| 7 | 組込 OCR | ✅ RapidOCR-ONNX | ❌ |
| 8 | 組込 TTS / ASR | ✅ Piper / FunASR(任意) | ❌ |
| 9 | ドキュメント RAG 範囲 | ✅ PDF/Word/Excel/PPT/MD/HTML/画像 | 限定的 |
| 10 | マルチベンダーゲートウェイ | ✅ 18+ プロバイダ | 5–10 |
| 11 | OpenAI 互換ルート | ✅ 内蔵 `/openai/v1/*` | 一部 |
| 12 | プロバイダのモデル自動探知 | ✅ Key 入力で `/v1/models` 自動取得 | 一部 |
| 13 | 既定モデル自動候補 | ✅ chat / embed / image / rerank 各々 | ❌ |
| 14 | モデル分類 | chat/embed/vision/image-gen/rerank/audio/video | 多くは chat + embed |
| 15 | マルチモデル対戦 | ✅ 無制限 + 統一送信 | ❌ |
| 16 | ツール呼出 3 層折りたたみ | ✅ サマリ→args/output→フル JSON | ❌ |
| 17 | ストリーミング reasoning 折りたたみ | ✅ | ❌ |
| 18 | KB ソース引用チップ | ✅ 信頼度 + 1 クリックで原文 | 一部 |
| 19 | ソース種別ごとの引用区分 | ✅ doc/struct/vec/office/web | ❌ |
| 20 | text2sql 安全性 | ✅ 読取 AST + テーブル/列ホワイトリスト | ❌ |
| 21 | 国産 DB 対応 | ✅ DM / KingbaseES / Doris | ❌ |
| 22 | 国際 DB 対応 | ✅ MySQL/PG/Oracle/SQL Server/ClickHouse/Hive | 多くは 0–1 種 |
| 23 | MongoDB コネクタ | ✅ | ❌ |
| 24 | Elasticsearch コネクタ | ✅ | 一部 |
| 25 | 外部ベクトルコネクタ | ✅ 複数 | ❌ または 1 種 |
| 26 | MCP クライアント | ✅ stdio + sse | 一部 |
| 27 | MCP サーバー | ✅ 自身が MCP server に | ❌ |
| 28 | 組込ツール数 | 30+ | 0–10 |
| 29 | カスタム HTTP ツール (OpenAPI) | ✅ Swagger 解析 | 一部 |
| 30 | カスタムスクリプトツール | ✅ Python REPL / Shell | 一部 |
| 31 | KB 種別数 | 5(doc/struct/vec/office/img) | 多くは 1 |
| 32 | ドキュメント形式範囲 | PDF/Word/Excel/PPT/MD/HTML/画像/CSV | 多くは PDF + Word |
| 33 | フォルダ増分同期 | ✅ folder-sync ルート | 一部 |
| 34 | 知識構造の自動構築 | ✅ 監視 + 解析 + 投入の一気通貫 | ❌ |
| 35 | KB 並行検索 | ✅ `selectedKuIds` で複数源 | 一部 |
| 36 | 国産 OS 対応 | ✅ Kylin / UOS / openKylin / deepin | ❌ |
| 37 | マルチアーキテクチャ | x86_64 / aarch64 / loongarch64 | 多くは x86_64 のみ |
| 38 | データディレクトリ ユーザー選択可 | ✅ FirstRunSetup ウィザード | 多くは固定 |
| 39 | 認証スイッチ可能 | ✅ 単機モードで自動 OFF | ❌(概念なし) |
| 40 | 資格情報暗号化 | ✅ Tauri Stronghold + ChaCha20-Poly1305 | 一部 |
| 41 | API 外部呼出可 | ✅ `/api/*` を WPS アドインから呼出 | ❌ |
| 42 | 同機 WPS アドイン連携 | ✅ chayuan-wps v3.0 | ❌ |
| 43 | OpenAPI HMAC 認証 | ✅ X-App-Id / X-Sign / X-Timestamp | ❌ |
| 44 | OpenAI SDK でローカル直結 | ✅ `base_url=http://127.0.0.1:62581/openai/v1` | 一部 |
| 45 | マルチタブ並行チャット | ✅ ブラウザ風 | 一部 |
| 46 | タブ ドラッグ / コンテキストメニュー | ✅ | 一部 |
| 47 | テーマ(ダーク/ライト/システム) | ✅ | 多数 |
| 48 | フォントサイズ カスタム | ✅ | 一部 |
| 49 | 多言語 UI | ✅ zh / en / ja / de / fr | 一部 |
| 50 | 内蔵ヘルプセンター | ✅ | 一部 |
| 51 | フィードバック窓口 | ✅ QR + GitHub Issue | 一部 |
| 52 | 自動アップデート | ⏳ 計画中 | 多数 |
| 53 | システムトレイ | ✅ | 多数 |
| 54 | グローバルショートカット | ✅ | 一部 |
| 55 | デスクトップ通知 | ✅ | 多数 |
| 56 | ファイル ドラッグ&ドロップ | ✅ | 多数 |
| 57 | クリップボード連携 | ✅ | 多数 |
| 58 | 可観測性 (Langfuse) | ✅ オプション、既定 OFF | 一部 |
| 59 | 評価モジュール | ✅ KB recall 評価 | ❌ |
| 60 | 監査 / クォータ / RBAC | ✅ governance | ❌ |
| 61 | PII マスキング | ✅ governance/redact | ❌ |
| 62 | データリネージ | ✅ lineage | ❌ |
| 63 | マルチユーザー / マルチテナント | ✅(profile 切替) | ❌ |
| 64 | 開発者 doctor 自己診断 | ✅ `chayuan doctor` | 一部 |
| 65 | OSS ライセンス | **AGPL-3.0** | 各々 |

---

## 8. 機能一覧

### 8.1 チャット機能

- SSE ストリーミング、深考折りたたみ、3 層ツール呼出、添付処理、ローカル永続化、編集 / 再生成 / 分岐、統一送信、仮想スクロール、IME セーフ Enter

### 8.2 KB 種別とドキュメント形式

KB 種別: `doc` / `struct` / `vec` / `office` / `img`(各々 §5.2 参照)

ドキュメント形式: PDF, DOCX, DOC, XLSX, XLS, CSV, PPTX, MD, HTML, TXT, PNG/JPG/BMP/TIFF, JSON/YAML/TOML, EML/MSG, EPUB

### 8.3 DB コネクタ(構造化データ)

17 SQL 方言: **国際**(MySQL, PostgreSQL, SQLite, MS SQL Server, Oracle, ClickHouse, Hive)、**国産**(DM, KingbaseES, Apache Doris)、**ドキュメント / 全文**(MongoDB, Elasticsearch)

**安全性**: text2sql は読取専用 AST 検証を必須通過。INSERT/UPDATE/DELETE/DROP は実行前にブロック。テーブル/列名はスキーマキャッシュ ホワイトリスト検査。

### 8.4 ベクトルストア

sqlite-vec(既定、組込)、FAISS、Milvus / Lite、Chroma、Zilliz、Elasticsearch、PostgreSQL + pgvector、Relyt(中国製分散ベクトル)

### 8.5 モデルプロバイダ

**クラウド(国際)**: OpenAI、Anthropic Claude、Gemini、Mistral、Together、Groq
**クラウド(国内)**: DeepSeek、Qwen / Dashscope、Wenxin、Zhipu GLM、Moonshot Kimi、Doubao、Baichuan、MiniMax、Yi、SiliconFlow
**ローカル / OpenAI 互換**: Ollama、LM Studio、Xinference、vLLM、OneAPI、FastChat、LocalAI

### 8.6 埋め込み / リランカー / OCR

埋め込み: bge-m3-onnx(既定)、Ollama、Infinity、OpenAI text-embedding-3、Dashscope、ZhipuAI、Jina、Cohere

リランカー: BAAI/bge-reranker-v2-m3(既定)、bge-reranker-large、任意の cross-encoder

OCR: RapidOCR-ONNX(既定、CPU、~70 MB)、RapidOCR-Paddle(GPU 任意)

### 8.7 マルチモーダル

T2I(/api/image/text2image)、Vision LLM、TTS(Piper、~30 MB)、ASR(FunASR)、T2V(Qwen Wan / Doubao PixelDance)

### 8.8 組込ツール (30+)

データ/知識: search_local_knowledgebase, search_internet, url_reader, text2sql, text2promql
コード/システム: python_repl, shell
学術: arxiv, pubmed_search, semantic_scholar, wikipedia_search, stackexchange, wolfram
DevOps: github_tool, gitlab_tool, confluence_search, notion_search
メッセージング: dingtalk_message, wechat_work_message, lark_message
リアルタイム/地理: amap_poi_search, amap_weather, openweather, news_api, yahoo_finance_news
汎用: calculate, http_request, openapi_call, custom_tools_runtime
マルチモーダル: text2image, search_youtube

### 8.9 MCP

クライアント: stdio / sse 形態の MCP server を UI から登録、ツールが Composer の MCP ピッカーに自動追加
サーバー: 察元バックエンド自身が MCP server として組込ツールを公開可

### 8.10 マルチモデル対戦

レーン無制限、統一送信、独立会話 ID、レイアウト永続化、折りたたみラベルは最初の質問

### 8.11 ナレッジ構造の自動構築 (Folder Sync)

フォルダを KB に「マウント」、ファイル変更で自動: 解析 → OCR → チャンク → 埋め込み → 投入 → 引用メタ更新

エンドポイント: `/api/folder-sync/*`

### 8.12 引用表示

各 LLM 回答下に Citation Strip:信頼度星 + ソース種別アイコン + ワンクリック操作

| ソース種別 | アイコン | 操作 |
|---|---|---|
| document | 📄 | 原文オープン / 添付ダウンロード |
| structured | 🗃 | 生成 SQL + 表データ + 行数表示 |
| vector | 📚 | collection / vector_id / metadata 表示 |
| office | 🏢 | 企業/チーム/個人スコープ表示 |
| web | 🌐 | 外部リンク開く |

---

## 9. HTTP API 概要

バックエンド既定: `127.0.0.1:62581`。Swagger: `http://127.0.0.1:62581/docs`。

### 主要ルート

| プレフィックス | 用途 |
|---|---|
| `/healthz` | ヘルスチェック |
| `/api/chat/*` | チャット |
| `/api/v1/kb-query/*` | **統一ナレッジクエリ**(推奨) |
| `/api/kb/*` | KB CRUD |
| `/api/knowledge-source/*` | 外部ソース登録 |
| `/api/folder-sync/*` | フォルダ自動同期 |
| `/api/image/*` `/api/voice/*` `/api/video/*` | マルチモーダル |
| `/api/tool/*` `/api/mcp/*` | ツール / MCP |
| `/api/governance/*` `/api/admin/*` | 監査 / 管理 |
| `/api/provider/*` | LLM プラットフォーム |
| `/openai/v1/*` | **OpenAI 互換** |
| `/openapi/v1/*` | カスタムアプリ HMAC 認証 |

### OpenAI SDK で直結

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:62581/openai/v1", api_key="anything")
resp = client.chat.completions.create(
    model="qwen2.5:7b",
    messages=[{"role": "user", "content": "こんにちは"}],
    stream=True,
)
```

### 統一ナレッジクエリ

```http
POST /api/v1/kb-query/search
{
  "ku_ids": ["doc:製品文書", "src:user_db", "vec:milvus_research"],
  "query": "先月売上トップ 3 の製品は?",
  "top_k": 5
}
```

---

## 10. 開発者向けセットアップ

詳細: [PACKAGING.md](PACKAGING.md)。クイックスタート:

| ツール | バージョン |
|---|---|
| Python | 3.12 |
| Poetry | ≥ 1.8 |
| Node.js | 22 |
| pnpm | 9 |
| Rust | stable |

**ローカル開発**:
```bash
cd chayuan-server && poetry install && poetry run chayuan start -a --single-machine
cd chayuan-client && pnpm install && pnpm dev:desktop
```

**パッケージング**:
```powershell
.\build-desktop.cmd                  # Windows 完全ビルド
.\build-desktop.cmd -BundleOnly      # Tauri バンドルのみ再生成
```
```bash
./build-desktop.sh                   # macOS / Linux
./build-desktop.sh --bundle-only
```

---

## 11. チュートリアル入口

- アプリ内 ヘルプセンター(サイドバー → ヘルプ)
- アプリ内 About(設定 → About)
- 公式サイト: <https://aidooo.com>
- WPS アドイン: <https://github.com/zhgyuhuii/chayuan/blob/main/README.md>
- パッケージングガイド: [PACKAGING.md](PACKAGING.md)

---

## 12. セキュリティ · プライバシー · オフライン

- **データ所在**: すべて `CHAYUAN_ROOT`(ユーザー選択ディレクトリ)。察元サーバーへのアップロードなし
- **資格情報**: Tauri Stronghold(ChaCha20-Poly1305 + Argon2id)で保管
- **ネットワーク出口**: 完全オフライン構成では 0;ハイブリッド構成ではモデル API のみ
- **監査**: governance モジュール(監査ログ / PII マスキング / データリネージ)

---

## 13. ロードマップ

| フェーズ | ステータス | 内容 |
|---|---|---|
| 1–7 | ✅ | First-run ウィザード、PyInstaller、サイドカー配線、sqlite-vec、単機 profile、3 プラットフォーム CI、UX 仕上げ |
| **8** | ⏳ | **Server-as-truth + SSE マルチキャスト**(WPS とデスクトップで会話ストリーム共有) |
| 6.x | ⏳ | macOS notarize / Windows EV 署名 / SM2 国密 / Linux ARM ランナー |
| 8.x | ⏳ | 自動アップデート / 増分パッチ |
| 9 | ⏳ | モバイル / Web クライアントで同一バックエンド共有 |

---

## 14. コミュニティ / フィードバック / 商用

| チャネル | 用途 | アドレス |
|---|---|---|
| 公式サイト | プロダクト紹介 / 商用窓口 | <https://aidooo.com> |
| WeChat OA | リリースノート / 技術深掘り | 智灵鸟科技 |
| GitHub Issue (本) | 公開技術 Issue | (内部) |
| GitHub Issue (WPS) | WPS アドイン | <https://github.com/zhgyuhuii/chayuan/issues> |
| 商用メール | 企業ライセンス / OEM / カスタマイズ | 公式サイト参照 |

**コントリビュート**: PR の前に Issue で RFC、CLAUDE.md のスタイル準拠、ブランド文字列に触れない、`pnpm typecheck` と `pytest -q` を実行、PR 説明にフェーズ記載。

---

## 15. 謝辞

[Tauri](https://tauri.app/) · [FastAPI](https://fastapi.tiangolo.com/) · [LangChain](https://www.langchain.com/) · [sqlite-vec](https://github.com/asg017/sqlite-vec) · [RapidOCR](https://github.com/RapidAI/RapidOCR) · [bge-m3](https://huggingface.co/BAAI/bge-m3) · [Piper TTS](https://github.com/rhasspy/piper) · [FunASR](https://github.com/modelscope/FunASR) · [Ollama](https://ollama.ai/) · [onnxruntime](https://onnxruntime.ai/) · [React](https://react.dev/) · [TanStack](https://tanstack.com/) · [Zustand](https://github.com/pmndrs/zustand) · [Tailwind CSS](https://tailwindcss.com/) · [Radix UI](https://www.radix-ui.com/) · [Shiki](https://shiki.style/) · [Lucide Icons](https://lucide.dev/) · [Marked](https://marked.js.org/) · [DOMPurify](https://github.com/cure53/DOMPurify)

各コンポーネントは各自のライセンスに従って配布。集約物は **AGPL-3.0** で提供。

---

## 付録 A: FAQ

**Q1: 既定データディレクトリは?** —— `~/Library/Application Support/chayuan` (macOS) / `%APPDATA%\chayuan` (Win) / `~/.local/share/chayuan` (Linux)。初回起動で任意のパスに変更可。

**Q2: ネットなしで使える?** —— ローカル LLM(Ollama 等)を使えば完全動作。クラウド LLM 構成では会話以外(KB 検索 / 解析 / OCR)は引き続き動作。

**Q3: ショートカット名が「察元AI」(中国語)になっている理由は?** —— NSIS ポストインストール フックで Finish 後に既定 ASCII ショートカット名を中国語に Rename。

**Q4: バックエンドをサーバーに、フロントを別マシンにできる?** —— 現行単機版は「フロント+バックエンド同一機」設計。マルチユーザー / サーバー型は別プロダクトライン(`chayuan-server/packaging/README.md` 参照)。

**Q5: chayuan-wps と必ず併用する?** —— 不要。両者独立利用可。**同一バックエンドを使うときは KB / モデル / 履歴を共有**。

**Q6: AGPL-3.0 は商用制限?** —— 内部利用 / イントラネット展開 / 組織内配布は無制限。**改変版を SaaS / 公開ネット**で配信する場合のみ改変分の公開義務あり。OEM / クローズドソース統合は別途商用ライセンス要。

**Q7: マルチモデル対戦の使い方?** —— チャット ページのトップバー [+ 追加] でレーン追加 → 各レーンでモデル選択 → 「統一送信」チェック → 任意のレーンに入力すると全レーンで並行ストリーム生成。

**Q8: アップグレードでデータは失われる?** —— アンインストールは `CHAYUAN_ROOT` を削除しません。NSIS は `%APPDATA%\chayuan\` のポインタファイルのみクリアし、次回再選択を促します。実データ(会話 / KB インデックス)は保持。

---

## 付録 B: 用語集

| 用語 | 意味 |
|---|---|
| CHAYUAN_ROOT | ユーザー選択のデータディレクトリ |
| Sidecar | Tauri がスポーンする Python 子プロセス |
| ku_id | Knowledge-Universe ID(統一ナレッジソース識別子) |
| Knowledge Universe | 5 種ソース(doc/struct/vec/office/img)の統一抽象 |
| Model Arena | マルチレーン モデル比較 |
| Lane | モデル対戦の各ペイン |
| Composer | チャット下部の入力エリア |
| MCP | Model Context Protocol(Anthropic) |
| RAG | Retrieval-Augmented Generation |
| text2sql | 自然言語→SQL(AST 検証付き) |
| Stronghold | Tauri 資格情報金庫 プラグイン |
| Splash | 起動アニメーション(5 層ゼロレイテンシ) |

---

<div align="center">

**察元 AI · デスクトップ単機版** · Tauri 2 + React 19 + Python 3.12 + AGPL-3.0

**北京智灵鸟科技中心** · <https://aidooo.com>

</div>
