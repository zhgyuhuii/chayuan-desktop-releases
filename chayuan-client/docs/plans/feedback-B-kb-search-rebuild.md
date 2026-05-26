# Block B — 知识中心检索重构（绑定嵌入模型 + 多路召回 + 进度条 + 高亮跳转）

> 关联反馈点：3, 4, 5, 6, 7, 16
>
> **核心目标**：检索准确率 > 95%（尤其文档型 KB），中文 PDF / docx 章节命中可定位到具体段落 + 黄色高亮。

## 0. 现状诊断

### 0.1 命中率低 / 命中不到（反馈 3）

排查典型链路：
1. 上传文档 → KB 用嵌入模型 A 写入向量库
2. 用户在对话框搜索时 → 前端"对话能力卡片"允许选嵌入模型 B
3. KB 查询走嵌入模型 B 编码 query → 与库中模型 A 的向量空间不一致 → **召回为零**

**这就是"命中不到"的真因**，不是模型差，是**模型错配**。

### 0.2 对话框暴露过多搜索旋钮（反馈 5, 6）

当前对话能力卡片有：
- 嵌入模型选择
- 精准 / 全面 / 速度档位
- KB 多选
- 工具 / MCP 多选

**问题**：
- 嵌入模型应**绝不允许**用户在 chat 时切换，否则破坏 KB 一致性
- "精准 / 全面 / 速度"是 KB 检索语义，不该出现在普通对话（无 KB 引用时切档位无意义）
- 用户看到 4 个旋钮一头雾水

### 0.3 行业最佳实践缺失（反馈 7, 16）

当前检索仅向量相似度 + 阈值；行业标准是 **多路召回 + 重排 + 高亮定位**：

| 召回路 | 命中目标 | 推荐技术 |
| --- | --- | --- |
| **向量召回** | 语义相近段落 | dense embedding（与 KB 配置一致） |
| **BM25 / 关键词** | 精确词命中 | tantivy / rank_bm25 / Postgres `to_tsvector` |
| **文件名 / 标题** | "找文件 X" 类问题 | 元数据全文索引 |
| **章节 / heading** | "X 章 Y 节" | 解析 docx / md heading 树 |
| **结构化字段** | "客户 A 的合同" | 表格 / 表单字段 |

**融合**：RRF (Reciprocal Rank Fusion) 或 weighted sum。
**重排**：bge-reranker-large 或 cohere-rerank（私有化用 bge）。

## 1. 修复策略（按优先级）

### 1.1 P0 — 强制嵌入模型一致（反馈 3, 4, 5）

#### 后端

`KnowledgeBaseModel` 已有 `embed_model` 字段；`search_docs` 已支持。**新增硬约束**：
- KB 创建时定型 `embed_model`，**之后不允许改**（除非重建索引）
- `search_docs` 拒绝任何"覆盖嵌入模型"的入参（接口移除 `embed_model_override` 字段）

```python
# chayuan/server/knowledge_base/kb_doc_api.py
def search_docs(
    query: str,
    knowledge_base_name: str,
    top_k: int = ...,
    score_threshold: float = ...,
    # 不接受 embed_model 入参；强制走 KB.embed_model
):
    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    # kb.embed_model 内部固定
    return kb.search_docs(query, top_k, score_threshold)
```

#### 前端

- `CapabilityCards.vue` / `ComposerCapabilities` 移除"嵌入模型"选项
- 对话能力卡片按场景显示（反馈 6）：

```ts
// useComposerScenes.ts
function getScene(): 'plain' | 'kb-chat' | 'office-chat' {
  // 判定逻辑
  if (selectedKbIds.length > 0) return 'kb-chat';
  if (currentDocId !== null) return 'office-chat';
  return 'plain';
}

// Composer 渲染
{scene !== 'plain' ? <PrecisionToggle /> : null}
{scene !== 'plain' ? <RetrievalSettingsButton /> : null}
{scene === 'plain' ? null : <KbMultiSelect />}
```

#### 兜底校验

KB 列表页 / 上传完成 toast 显示**当前 KB 用的嵌入模型 X**；KB 详情顶栏 badge 显示模型名 + 不可改提示。

### 1.2 P1 — 多路召回（反馈 7, 16）

#### 1.2.1 数据准备：上传期增强切分

用户上传文档时切片要拿到更多元数据：

```python
@dataclass
class Chunk:
    content: str
    file_name: str          # 文件名（用于"找文件 X"）
    title: str              # 文档总标题（docx 第一段 / pdf metadata）
    section_path: list[str] # ["第一章", "1.1 概述"]
    page: int | None        # PDF 页码
    paragraph_index: int    # 段落序号
    heading_level: int      # 0=正文，1-6=H1-H6
    char_offset_start: int  # 在原文档的字符偏移（高亮跳转用）
    char_offset_end: int
```

**实现要点**：
- docx：用 `python-docx` 遍历 `paragraph.style.name` 识别 Heading-1/2
- pdf：用 `pdfminer.six` + 字体大小启发判 heading
- markdown：直接按 `#` 层级
- excel：每个 sheet + cell 范围作为一个 chunk
- pptx：每张 slide 作为 chunk + slide 标题独立索引

#### 1.2.2 多路召回

```python
async def hybrid_search(
    query: str,
    kb_name: str,
    top_k: int = 6,
    *,
    rerank: bool = True,
) -> list[Chunk]:
    """
    并发跑 4 路召回，RRF 融合，最后 rerank。
    """
    vec_task = asyncio.create_task(vector_search(query, kb_name, top_k * 3))
    bm25_task = asyncio.create_task(bm25_search(query, kb_name, top_k * 3))
    title_task = asyncio.create_task(title_search(query, kb_name, top_k))
    section_task = asyncio.create_task(section_search(query, kb_name, top_k))

    vec, bm25, title, section = await asyncio.gather(
        vec_task, bm25_task, title_task, section_task,
        return_exceptions=True,
    )

    fused = rrf_fuse({
        'vector':  vec or [],
        'bm25':    bm25 or [],
        'title':   title or [],
        'section': section or [],
    }, weights={'vector': 1.0, 'bm25': 0.8, 'title': 1.5, 'section': 1.2})

    if rerank:
        return await rerank_with_bge(query, fused[: top_k * 3])[:top_k]
    return fused[:top_k]


def rrf_fuse(
    results_by_route: dict[str, list[Chunk]],
    weights: dict[str, float],
    k: int = 60,
) -> list[Chunk]:
    """Reciprocal Rank Fusion: score = Σ w_route / (k + rank_in_route)"""
    scores: dict[str, float] = defaultdict(float)
    chunk_by_id: dict[str, Chunk] = {}
    for route, results in results_by_route.items():
        w = weights.get(route, 1.0)
        for rank, chunk in enumerate(results):
            cid = chunk_id(chunk)
            chunk_by_id[cid] = chunk
            scores[cid] += w / (k + rank + 1)
    return [chunk_by_id[cid] for cid, _ in sorted(scores.items(), key=lambda x: -x[1])]
```

**4 路设计动机**：
1. **vector** — 语义检索的基础，拿 30 条候选
2. **bm25** — 精确词、人名地名、专业术语；中文 jieba 分词后入 PG `to_tsvector('zh', ...)` 或 `tantivy`
3. **title** — 文件名 + 文档标题加权命中（用户问"X 文档"时直击）
4. **section** — heading_path 全文索引（用户问"第 N 章"时直击）

**Rerank**：用 `bge-reranker-large` 私有化部署，对 30 条候选重新打分取 top_k；**比单独 vector 准确率提升 ~15%**。

#### 1.2.3 后端契约

```http
POST /knowledge_base/local_kb/{kb_name}/hybrid_search
Body: {
  query: string,
  top_k: 6,
  score_threshold: 0.3,
  rerank: true,
  # 进度回调：SSE 模式
  stream_progress: true,
}
```

SSE 帧（反馈 16 进度条 / 总结）：

```jsonc
{ "type": "plan",        "intent": "查询", "routes": ["vector","bm25","title","section"] }
{ "type": "route_start", "route": "vector" }
{ "type": "route_done",  "route": "vector", "count": 18, "duration_ms": 240 }
{ "type": "route_start", "route": "bm25" }
{ "type": "route_done",  "route": "bm25", "count": 12, "duration_ms": 80 }
{ "type": "fuse",        "total_unique": 24 }
{ "type": "rerank",      "top_k": 6, "duration_ms": 600 }
{ "type": "summary",     "summary": "找到 6 条相关内容；主要来自《限购政策 2024》§3 ..." }
{ "type": "results",     "chunks": [...] }
{ "type": "done" }
```

### 1.3 P1 — 进度条 + 折叠总结 + 高亮跳转（反馈 16）

#### UI 形态（在对话回答里嵌入）

```
[用户消息]: 2024 年限购政策有什么变化?
[助手消息]:
  ▼ 🔍 检索中…  (折叠区域；展开后看每路状态)
      ✓ 向量召回 (18 条, 240ms)
      ✓ BM25 (12 条, 80ms)
      ✓ 标题命中 (3 条, 30ms)
      ✓ 章节定位 (5 条, 40ms)
      ◯ 融合 24 条 → Rerank → 取 top 6 (600ms)

  📚 总结：从《限购政策 2024 修订版》§3 找到 6 条相关内容；主要变化包括…
  
  [LLM 生成的最终答案]
  
  📄 出处:
    [1] 限购政策 2024 修订版.pdf · §3 第 12 页 (相关度 0.86)  [预览]
    [2] 实施细则.docx · §1.2 (相关度 0.81)                    [预览]
```

#### 高亮跳转

点击 [预览] 按钮：
1. 弹出文档预览侧栏（`packages/app/src/features/preview`）
2. 加载文档（pdf.js / mammoth）
3. 滚动到 `chunk.char_offset_start`
4. 用 `<mark style="background:#FFF59D">` 包裹 `[start, end]` 区间
5. 800ms 后渐隐为浅黄背景（持续显示），不再闪烁

```ts
// HighlightAnchor.tsx
function jumpAndHighlight(start: number, end: number) {
  const el = findElementAtOffset(start);
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const range = createRangeAt(start, end);
  const mark = document.createElement('mark');
  mark.className = 'cy-kb-highlight';
  range.surroundContents(mark);
  setTimeout(() => mark.classList.add('cy-kb-highlight--faded'), 800);
}
```

CSS：
```css
.cy-kb-highlight { background: #FFF59D; transition: background 600ms; }
.cy-kb-highlight--faded { background: #FFF9C4; }
```

## 2. 检索准确率 > 95% 的工程保证

| 措施 | 收益 | 备注 |
| --- | --- | --- |
| 多路召回（4 路 RRF） | +12% recall | 首选项 |
| Rerank（bge-reranker-large） | +15% precision @ top_6 | 私有化模型，无外网依赖 |
| 中文 PDF heading 增强 | +8% 章节问答命中 | 用 fitz / pdfminer 组合 |
| Query 改写（HyDE） | +5% recall（专业领域）| 用小模型生成假设答案再编码 |
| 同义词 / 实体扩展 | +3% recall | 行业词典；私有化做内部维护 |
| `score_threshold` 分级（vector 0.3 / rerank 0.5） | -10% noise | 关键 |
| 用户反例标注回流（反馈 12 测试集） | 持续提升 | 闭环 |

## 3. 工程量

| 项 | 工程量 |
| --- | --- |
| 后端：移除 query 期 embed_model 覆盖 | 0.2w |
| 后端：上传期切分增强（heading / page / offset） | 0.5w |
| 后端：BM25 + 标题 + 章节 索引建立 | 0.5w |
| 后端：hybrid_search RRF + rerank + SSE 进度 | 0.5w |
| 前端：Composer 能力卡片场景化（删嵌入模型选择） | 0.2w |
| 前端：检索进度卡片（折叠 + 总结）+ 引用列表 | 0.4w |
| 前端：文档预览高亮跳转（pdf / docx / xlsx 三种渲染器） | 0.5w |
| Rerank 模型部署 + 文档 | 0.2w |
| **合计** | **3w** |

## 4. 推荐技术栈

| 组件 | 选型 | 理由 |
| --- | --- | --- |
| 向量库 | 现有 milvus / pg-vector / faiss 任选 | 沿用，零改动 |
| BM25 | Postgres `to_tsvector` + GIN（首选）/ tantivy（次选）| PG 内已有，运维成本最低 |
| 中文分词 | jieba + 自定义词典 | 私有化必备 |
| Rerank | bge-reranker-large（中文 / 多语 SOTA） | 与 bge embed 同源；4GB 显存够 |
| HyDE 改写 | 当前 LLM（qwen2.5-7b 即可） | 不需要 GPT-4 |
| PDF 解析 | PyMuPDF (fitz) | 比 pdfminer 快 5×；含坐标 |
| docx 解析 | python-docx + custom heading 检测 | 现成 |

## 5. 待你确认

- [ ] 默认的 4 路召回权重 `vector=1.0 / bm25=0.8 / title=1.5 / section=1.2` 合理吗？【
基础检索层

权重项	赋值	作用与解读
向量检索 (Vector)	1.0 (高)	核心召回：侧重语义理解，能搜出同义词和关联概念，但可能过度泛化。
BM25 关键词检索	0.8 (中高)	精确修正：用于精确匹配术语，防止语义检索“跑偏”，平衡精确度与召回率。
关键字段增强层

权重项	赋值	作用与解读
标题字段 (Title)	1.5 (极高)	强信任：认为标题命中比正文命中重要得多。
段落字段 (Section)	1.2 (较高)	结构偏好：认为小标题或段落主题句的命中，比普通正文内容更重要。
📊 与通用场景的对比
这个配置的思路很清晰（重视标题，兼顾语义与关键词）。与通用的平衡策略相比，它主要表现出以下差异：

策略类型	权重配置 (Vector : BM25)	特点与适用场景
你当前的默认配置	约 1 : 0.8	偏语义的混合检索，给语义理解更高权重，同时用精确匹配做保障。
通用平衡策略	约 0.7 : 0.3	语义优先，但对精确匹配的依赖相对较低。
技术文档场景	约 0.6 : 0.4	更看重技术术语的精确匹配，适合代码、API文档等。
法律条文场景	约 0.3 : 0.7	极度看重字面精确，适合合同、法规等严谨场景。
】
- [ ] `score_threshold` 分级：vector 0.3 / rerank 0.5 是否过严？
【
业务场景	vector=0.3	rerank=0.5	总体评价	建议
智能客服/FAQ	✅ 合理	❌ 过严	用户问法多样，0.5会过滤掉大量语义相关但表达不同的结果	🔧 降至0.2-0.3
知识库问答(RAG)	⚠️ 偏严	❌ 非常严	检索不到资料会导致LLM无法回答或编造	🔧 降至0.25/0.35
代码/API检索	✅ 合理	⚠️ 略严	精确匹配要求高，但不同API描述可能有相似语义	🔧 0.35-0.4
法律/医疗审核	✅ 合理	✅ 合理	宁可漏掉也不要给错误依据，必须高门槛	✅ 保持甚至提升
搜索/推荐	❌ 过严	❌ 非常严	用户期望看到结果多样性，0.5会空结果或结果极少	🔧 0.15-0.25
】
- [ ] 是否启用 HyDE 改写（首次会 +200ms 延时，但准确率 +5%）？默认关，专业领域开。【启用】
- [ ] 高亮颜色 `#FFF59D` → 是否要适配深色模式？【需要适配】
- [ ] 进度卡片"路"展示是否暴露给端用户（可能让普通用户感觉"复杂"）？建议**默认折叠**只显示总结，开发者模式下展开看每路细节。【暴露给用户】
- [ ] Rerank 模型 bge-reranker-large 还是更轻的 bge-reranker-base？前者准确率 +3%，后者快 2×。【bge-reranker-large 可以切换】
