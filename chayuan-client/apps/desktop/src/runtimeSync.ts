/**
 * 运行状态同步 —— 后台静默上报本安装实例的存活心跳。
 *
 * 行为:启动 30s 后首次上报,之后每 30 分钟一次。
 * 上报内容:匿名安装标识(hid)、产品、版本、操作系统信息、品牌完整性。
 * 任何失败(断网 / DNS 失败 / 超时)全部静默吞掉 —— 不报错、不打印、不提示。
 *
 * 合规提示:本模块会上报 IP(由网络请求自动携带)与操作系统信息,
 * 属《个人信息保护法》个人信息,须在《隐私政策》中向用户告知。
 */

const PRODUCT = 'desktop';
const FIRST_DELAY_MS = 30 * 1000;
const INTERVAL_MS = 30 * 60 * 1000;
const HID_KEY = 'cy_rid';

// 上报地址:'aidooo.com' 经 XOR 0x5a + base64 混淆,运行时还原。
// 客户端运行时必须连接该域名,无法真正"加密",此处仅做源码混淆 ——
// 防止直接 grep 源码 / 肉眼扫读拿到地址;抓包仍可见。
const _H = 'OzM+NTU1dDk1Nw==';
function endpoint(): string {
  const raw = atob(_H);
  let h = '';
  for (let i = 0; i < raw.length; i++) {
    h += String.fromCharCode(raw.charCodeAt(i) ^ 0x5a);
  }
  return `https://${h}/api/node-sync`;
}

// 匿名安装标识:首次运行生成 UUID,本地持久化,与个人身份无关
function getHid(): string {
  const fallback = (): string =>
    `h${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  try {
    let v = localStorage.getItem(HID_KEY);
    if (!v || !/^[a-z0-9-]{8,64}$/i.test(v)) {
      v = typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : fallback();
      localStorage.setItem(HID_KEY, v);
    }
    return v;
  } catch {
    return fallback();
  }
}

// 操作系统信息(尽力而为,从 UA 解析)
function detectSystem(): { os: string; osVer: string; arch: string } {
  let os = 'unknown';
  let osVer = '';
  let arch = '';
  try {
    const ua = navigator.userAgent || '';
    const win = ua.match(/Windows NT ([0-9.]+)/);
    const mac = ua.match(/Mac OS X ([0-9_]+)/);
    if (win) {
      os = 'windows';
      osVer = win[1] ?? '';
    } else if (mac) {
      os = 'macos';
      osVer = (mac[1] ?? '').replace(/_/g, '.');
    } else if (/Linux/.test(ua)) {
      os = 'linux';
    }
    if (/arm64|aarch64/i.test(ua)) arch = 'arm64';
    else if (/x64|Win64|WOW64|x86_64|amd64/i.test(ua)) arch = 'x64';
  } catch {
    /* 静默 */
  }
  return { os, osVer, arch };
}

// 品牌完整性自检:运行界面里是否还存在"察元"字样(被改牌 / 抹除则为 false)
function brandIntact(): boolean {
  try {
    const txt = `${document.title || ''} ${document.body ? document.body.innerText : ''}`;
    return /察元/.test(txt);
  } catch {
    return true; // 检测本身失败时不误判为被改
  }
}

function report(version: string): void {
  try {
    const payload = JSON.stringify({
      hid: getHid(),
      product: PRODUCT,
      ver: version,
      ...detectSystem(),
      brand: brandIntact(),
      t: Date.now(),
    });
    // no-cors:跨域 fire-and-forget,请求照常到达服务端,只是读不到响应(无需读)
    void fetch(endpoint(), {
      method: 'POST',
      body: payload,
      mode: 'no-cors',
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* 静默:遥测绝不影响主程序 */
  }
}

let started = false;
/** 启动运行状态同步。重复调用无副作用。 */
export function initRuntimeSync(version = '0.1.0'): void {
  if (started) return;
  started = true;
  try {
    setTimeout(() => report(version), FIRST_DELAY_MS);
    setInterval(() => report(version), INTERVAL_MS);
  } catch {
    /* 静默 */
  }
}
