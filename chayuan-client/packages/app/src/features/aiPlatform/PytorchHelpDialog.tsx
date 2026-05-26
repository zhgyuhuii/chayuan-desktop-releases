/**
 * 「手动安装 PyTorch」帮助弹窗。
 *
 * 触发方:LocalRuntimeServicesSection 里 PyTorch 行标题后的「?」帮助图标。
 * 内容:离线 / 内网环境下,从下载 → 解压 → 放置 → 验证的全流程说明。
 * 取代了原先内嵌的「想自己下载 PyTorch?」<details> 折叠区 —— 折叠区只能塞
 * 一两句话,完整步骤放弹窗里更清楚。
 *
 * 动态信息(建议版本 / 官方下载地址 / 放置目录)从 PytorchStatus 取,
 * 跟面板其它地方同一处真源,版本调整时这里自动跟随。
 */

import type { PytorchStatus } from '@chayuan/api';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@chayuan/ui';
import type * as React from 'react';

export interface PytorchHelpDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 面板当前的 PyTorch 状态;用于填建议版本 / 下载地址。null 时退化成通用文案。 */
  status: PytorchStatus | null;
  /** 「放置目录」绝对路径;null 时提示先在面板上看路径。 */
  targetDir: string | null;
}

/** 行内代码片段,统一样式。 */
const Code: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <code className="rounded bg-[var(--cy-surface-base)] px-1 text-[var(--cy-text-primary)] break-all">
    {children}
  </code>
);

/** 一个步骤块:序号圆点 + 标题 + 正文。 */
const Step: React.FC<{ n: number; title: string; children: React.ReactNode }> = ({
  n,
  title,
  children,
}) => (
  <div className="flex gap-2.5">
    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--cy-accent,#2563eb)] text-[11px] font-semibold text-white">
      {n}
    </span>
    <div className="min-w-0 flex-1 space-y-1">
      <div className="text-sm font-medium text-[var(--cy-text-primary)]">{title}</div>
      <div className="space-y-1 text-[12px] leading-relaxed text-[var(--cy-text-secondary)]">
        {children}
      </div>
    </div>
  </div>
);

export const PytorchHelpDialog: React.FC<PytorchHelpDialogProps> = ({
  open,
  onOpenChange,
  status,
  targetDir,
}) => {
  const torchVer = status?.pinned_torch_version ?? '(见面板「建议版本」)';
  const tvVer = status?.pinned_torchvision_version ?? '(见面板「建议版本」)';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>手动安装 PyTorch(离线 / 内网环境)</DialogTitle>
          <DialogDescription>
            联网环境直接点面板上的「安装」即可 —— 它会自动逐个验证候选下载链接、
            命中能下的那个才下,不会撞死链。下面这套流程是给离线 / 内网、或想自己
            挑版本的用户:把 wheel 下好、解压、放进目录,再扫描验证即可。
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[62vh] space-y-4 overflow-y-auto pr-1">
          <div className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2.5 text-[12px] text-[var(--cy-text-secondary)]">
            PyTorch 体积大(CPU 版约 250 MB,GPU 版 2–3 GB),没有打进安装包,
            所以需要单独准备。整个过程不要求这台机器联网 —— 让能上网的机器把 wheel 下好,拷过来即可。
          </div>

          <Step n={1} title="下载两个 wheel 文件">
            <p>
              需要 <strong>两个</strong> <Code>.whl</Code> 文件:<Code>torch</Code> 和{' '}
              <Code>torchvision</Code>,版本必须严格配套(ABI 绑定,错配运行时报{' '}
              <Code>torchvision::nms does not exist</Code>)。本次要下的版本:
              torch <Code>{torchVer}</Code> · torchvision <Code>{tvVer}</Code>。
            </p>

            <p>
              联网的话直接点面板上的「安装」最省事 —— 它内置了下载 + 解压,
              <strong>不需要 pip</strong>。下面这套是手动办法(离线 / 内网,
              或想自己挑版本):
            </p>
            <p>① 用浏览器打开这两个文件夹(CPU 版 torch / torchvision 的 wheel 列表):</p>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                torch:<Code>https://download.pytorch.org/whl/cpu/torch/</Code>
              </li>
              <li>
                torchvision:
                <Code>https://download.pytorch.org/whl/cpu/torchvision/</Code>
              </li>
            </ul>
            <p>
              页面是一长串 <Code>.whl</Code> 文件链接。按 <Code>Ctrl+F</Code> 在页面里
              搜下面的文件名,搜到后<strong>点那个链接</strong>即开始下载。
            </p>
            <p>
              ② 文件名(本机 Python 3.12 对应 <Code>cp312</Code>,
              <Code>&lt;平台&gt;</Code> 按系统替换):
            </p>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                <Code>torch-{torchVer}+cpu-cp312-cp312-&lt;平台&gt;.whl</Code>
              </li>
              <li>
                <Code>torchvision-{tvVer}+cpu-cp312-cp312-&lt;平台&gt;.whl</Code>
              </li>
            </ul>
            <p>
              <Code>&lt;平台&gt;</Code> 取值:Windows = <Code>win_amd64</Code>;
              Linux x86_64 = <Code>linux_x86_64</Code>(页面里搜不到再试{' '}
              <Code>manylinux_2_28_x86_64</Code> / <Code>manylinux2014_x86_64</Code>,
              都是 Linux x86_64 wheel);macOS = <Code>macosx_11_0_arm64</Code>
              (Apple 芯片)/ <Code>macosx_10_9_x86_64</Code>(Intel)。
            </p>
            <p>例 —— Linux x86_64 要下的就是这两个文件:</p>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                <Code>torch-{torchVer}+cpu-cp312-cp312-linux_x86_64.whl</Code>
              </li>
              <li>
                <Code>torchvision-{tvVer}+cpu-cp312-cp312-linux_x86_64.whl</Code>
              </li>
            </ul>
            <p className="text-[var(--cy-text-tertiary)]">
              装 GPU(CUDA)版:把上面所有 <Code>cpu</Code> 换成 <Code>cuXXX</Code>
              (如 <Code>cu124</Code>)、文件夹换成{' '}
              <Code>https://download.pytorch.org/whl/cu124/torch/</Code>。没 NVIDIA
              显卡别装 GPU 版,CPU 版日常够用。
            </p>
          </Step>

          <Step n={2} title="把两个 wheel 解压到同一个目录">
            <p>
              <Code>.whl</Code> 本质就是 zip 压缩包。第 1 步下到的两个文件都要解压,
              而且<strong>解压进同一个目录</strong>(比如新建一个 <Code>torch-pkg</Code>{' '}
              文件夹)。<strong>不需要 pip / Python</strong>,用系统自带的解压就行:
            </p>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                Windows:把文件后缀 <Code>.whl</Code> 改成 <Code>.zip</Code>,
                右键「全部解压」;两个文件都解压到同一个 <Code>torch-pkg</Code> 文件夹。
              </li>
              <li>
                Linux / macOS:<Code>unzip 文件名.whl -d torch-pkg</Code>
                (或 <Code>tar -xf 文件名.whl -C torch-pkg</Code>);两个 wheel 都解。
              </li>
            </ul>
            <p>
              解压完 <Code>torch-pkg/</Code> 里会有 <Code>torch/</Code>、
              <Code>torchvision/</Code> 两个包目录(外加几个 <Code>*.dist-info</Code>)
              —— 这就是下一步要放进去的内容。
            </p>
          </Step>

          <Step n={3} title="放进「放置目录」">
            <p>
              把解压出来的 <Code>torch/</Code>、<Code>torchvision/</Code> 等目录,
              整体复制到面板的「放置目录」下:
            </p>
            <p>
              {targetDir ? (
                <Code>{targetDir}</Code>
              ) : (
                <span className="text-[var(--cy-text-tertiary)]">
                  路径见面板上「放置目录」一行,旁边有「复制路径」按钮。
                </span>
              )}
            </p>
            <p>
              放好后,放置目录应该长这样 —— <Code>torch/</Code> 和{' '}
              <Code>torchvision/</Code> 两个目录【并排、同时直接】位于其下:
            </p>
            <pre className="overflow-x-auto rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-2.5 text-[11px] leading-relaxed text-[var(--cy-text-primary)]">
{`放置目录/
├─ torch/                          ← 必须有(里面有 __init__.py)
├─ torchvision/                    ← 必须有(里面有 __init__.py)
├─ torchgen/   functorch/   …      ← 解压出的其它包目录,一并放进来
├─ torch-${torchVer}+cpu.dist-info/
└─ torchvision-${tvVer}+cpu.dist-info/`}
            </pre>
            <p className="text-[var(--cy-text-tertiary)]">
              硬性要求:<Code>torch/</Code> 与 <Code>torchvision/</Code> 必须
              <strong>同时、直接</strong>位于放置目录下、并排 —— 系统只会把这{' '}
              <strong>一个</strong>目录加进运行路径,torch 和 torchvision 都得能
              从它 import 到。自检:放置目录下 <Code>torch/__init__.py</Code> 和{' '}
              <Code>torchvision/__init__.py</Code> 两个文件都在,才算放对。不要多
              套一层文件夹,也不要把 torch、torchvision 各放进一个子目录。
            </p>
            <p className="text-[var(--cy-text-tertiary)]">
              迁移已装好的 PyTorch:如果你之前在别处装过 / 解压过 torch,把那一整套{' '}
              <Code>torch/</Code>、<Code>torchvision/</Code>、<Code>transformers/</Code>{' '}
              等目录复制进上面这个放置目录(并排放,别多套一层),再按下一步「扫描」即可。
            </p>
          </Step>

          <Step n={4} title="扫描 / 验证">
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                放好后点面板的「扫描」按钮 —— 它会识别出{' '}
                <Code>torch x.x.x(CPU/GPU 版)· torchvision x.x.x</Code>。
              </li>
              <li>验证通过的标志:面板状态徽标变绿,并显示 torch 与 torchvision 的版本号。</li>
            </ul>
          </Step>

          <div className="space-y-1 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-2.5 text-[12px] text-[var(--cy-text-secondary)]">
            <div className="font-medium text-[var(--cy-text-primary)]">常见问题</div>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>
                报 <Code>No module named 'torchvision'</Code> —— torchvision 没和
                torch 放在同一个目录。检查放置目录下 <Code>torch/</Code> 和{' '}
                <Code>torchvision/</Code> 是否并排、都直接在里面(见上方目录结构图)。
              </li>
              <li>
                报 <Code>torchvision::nms does not exist</Code> —— torch 与 torchvision
                版本不配套,重新下载配套的两个 wheel。
              </li>
              <li>
                扫描不到 —— 确认 <Code>torch/</Code> 目录(不是 <Code>torch-x.x.x.dist-info</Code>
                )直接位于放置目录下。
              </li>
              <li>GPU 版要先有 NVIDIA 显卡 + 足够新的驱动;没有就用 CPU 版。</li>
            </ul>
          </div>
        </div>

        <DialogFooter>
          <Button type="button" size="sm" onClick={() => onOpenChange(false)}>
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default PytorchHelpDialog;
