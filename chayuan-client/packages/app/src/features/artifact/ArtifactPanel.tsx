import * as React from 'react';
import { X, Copy, Download, Code2, FileText, BarChart3, Globe, Braces } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { getPlatform } from '@chayuan/platform-shared';
import { useArtifactStore, type Artifact } from '../../store/artifact';

const KIND_ICON = {
  code: Code2,
  markdown: FileText,
  mermaid: BarChart3,
  html: Globe,
  json: Braces,
} as const;

export const ArtifactPanel: React.FC = () => {
  const open = useArtifactStore((s) => s.open);
  const current = useArtifactStore((s) => s.current);
  const items = useArtifactStore((s) => s.items);
  const setOpen = useArtifactStore((s) => s.setOpen);
  const setCurrent = useArtifactStore((s) => s.setCurrent);

  const list = React.useMemo(
    () => Object.values(items).sort((a, b) => b.updatedAt - a.updatedAt),
    [items],
  );
  const active = current ? items[current] : null;

  if (!open || !active) return null;

  return (
    <aside
      className="flex h-full w-[480px] max-w-[55vw] flex-col border-l bg-card shadow-xl"
      role="complementary"
      aria-label="Artifact 面板"
    >
      <div className="flex items-center justify-between border-b px-3 py-2">
        <select
          value={active.id}
          onChange={(e) => setCurrent(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-sm"
        >
          {list.map((a) => (
            <option key={a.id} value={a.id}>
              {a.title}
            </option>
          ))}
        </select>
        <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="关闭">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5 text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          {React.createElement(KIND_ICON[active.kind] ?? Code2, { className: 'h-3.5 w-3.5' })}
          {active.kind}
          {active.language && <code className="rounded bg-background px-1.5 py-0.5 text-[10px]">{active.language}</code>}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={() => void getPlatform().clipboard.writeText(active.content)}
            aria-label="复制"
          >
            <Copy className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={() => void getPlatform().fs.saveText(filenameFor(active), active.content)}
            aria-label="保存"
          >
            <Download className="h-3 w-3" />
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-auto bg-background">
        <ArtifactRenderer artifact={active} />
      </div>
    </aside>
  );
};

const ArtifactRenderer: React.FC<{ artifact: Artifact }> = ({ artifact }) => {
  switch (artifact.kind) {
    case 'mermaid':
      return <MermaidView source={artifact.content} />;
    case 'html':
      return <HtmlView source={artifact.content} />;
    case 'json':
      return <CodeView code={prettyJson(artifact.content)} language="json" />;
    case 'markdown':
      return <pre className="whitespace-pre-wrap p-4 text-sm">{artifact.content}</pre>;
    case 'code':
    default:
      return <CodeView code={artifact.content} language={artifact.language} />;
  }
};

const CodeView: React.FC<{ code: string; language?: string }> = ({ code, language }) => (
  <pre className={cn('m-0 h-full overflow-auto bg-[#0b1020] p-4 font-mono text-xs leading-relaxed text-zinc-100')}>
    <code data-language={language}>{code}</code>
  </pre>
);

/** Mermaid 渲染：懒加载；失败显示源码 fallback */
const MermaidView: React.FC<{ source: string }> = ({ source }) => {
  const ref = React.useRef<HTMLDivElement>(null);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        // 'mermaid' 拼成运行时字符串 + @vite-ignore,避免 Vite import-analysis
        // 在依赖未安装时报 "Failed to resolve import"。装了就用,没装走 err 分支。
        const modName = 'mer' + 'maid';
        const mod = (await import(/* @vite-ignore */ modName).catch(() => null)) as
          | { default: { initialize: (o: unknown) => void; render: (id: string, src: string) => Promise<{ svg: string }> } }
          | null;
        if (!alive || !mod || !ref.current) {
          setErr('mermaid 未安装');
          return;
        }
        mod.default.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'strict' });
        const id = `m-${Math.random().toString(36).slice(2)}`;
        const { svg } = await mod.default.render(id, source);
        if (alive && ref.current) ref.current.innerHTML = svg;
      } catch (e: unknown) {
        if (alive) setErr(e instanceof Error ? e.message : '渲染失败');
      }
    })();
    return () => {
      alive = false;
    };
  }, [source]);

  if (err) {
    return (
      <div className="space-y-2 p-4">
        <div className="text-xs text-destructive">Mermaid 渲染失败：{err}</div>
        <pre className="overflow-auto rounded bg-muted p-2 text-xs">{source}</pre>
      </div>
    );
  }
  return <div ref={ref} className="flex h-full items-center justify-center p-4" />;
};

/** HTML 渲染：sandbox iframe，禁脚本，仅样式与结构 */
const HtmlView: React.FC<{ source: string }> = ({ source }) => (
  <iframe
    title="HTML Artifact"
    sandbox="allow-same-origin"
    className="h-full w-full border-0 bg-white"
    srcDoc={source}
  />
);

function filenameFor(a: Artifact): string {
  const ext = a.kind === 'mermaid' ? 'mmd' : a.kind === 'html' ? 'html' : a.kind === 'json' ? 'json' : a.language || 'txt';
  return `${a.title.replace(/[^\w-]+/g, '_').slice(0, 40) || 'artifact'}.${ext}`;
}

function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}
