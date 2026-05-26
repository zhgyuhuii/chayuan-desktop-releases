"""纯 Python / SVG 生成的图形验证码。

为什么不用 PIL / Pillow：
- 配置面板希望尽可能少增加依赖；
- 4 位字符的"扰乱文字 + 干扰线"级别的验证码，用 SVG 在 DOM 里渲染就足够挡住
  脚本化爆破（OCR 识别成本 >> 随机 8 位路径 + 用户名/密码多道防线）。

接口：
- `generate_captcha()` -> (code: str, svg_html: str)
  code 是正确答案（小写字母数字 4 位，排除易混字符）；svg_html 可直接喂给 `ui.html`。
- `verify_captcha(user_input, expected)` -> bool   大小写不敏感。
"""
from __future__ import annotations

import random
import secrets
from html import escape

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 去掉 0/o/1/l/i/5/s 等易混字符
_CAPTCHA_LEN = 4

_FONT_FAMILY = (
    "'Courier New', 'Menlo', 'Consolas', monospace"
)
_COLOR_PALETTE = [
    "#2563eb",
    "#db2777",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#b91c1c",
]


def generate_code(length: int = _CAPTCHA_LEN) -> str:
    """生成只含小写字母+数字、长度为 length 的随机码。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _render_svg(code: str, width: int = 140, height: int = 48) -> str:
    """把 code 渲染成带轻度扰乱的 SVG。"""
    rng = random.Random(secrets.token_bytes(8))

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'style="background:#f5f5f7;border-radius:6px;user-select:none;" '
        f'aria-label="验证码">'
    )

    for _ in range(4):
        x1, x2 = rng.randint(0, width), rng.randint(0, width)
        y1, y2 = rng.randint(0, height), rng.randint(0, height)
        color = rng.choice(_COLOR_PALETTE)
        opacity = rng.uniform(0.25, 0.55)
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="1" stroke-opacity="{opacity:.2f}" />'
        )

    for _ in range(18):
        cx = rng.randint(2, width - 2)
        cy = rng.randint(2, height - 2)
        r = rng.randint(1, 2)
        color = rng.choice(_COLOR_PALETTE)
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" fill-opacity="0.35" />'
        )

    n = len(code)
    slot = width / (n + 1)
    for i, ch in enumerate(code, start=1):
        x = slot * i
        y = height / 2 + rng.randint(4, 8)
        rotate = rng.randint(-25, 25)
        size = rng.randint(24, 30)
        color = rng.choice(_COLOR_PALETTE)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" '
            f'font-family="{_FONT_FAMILY}" font-size="{size}" font-weight="700" '
            f'fill="{color}" transform="rotate({rotate} {x:.1f} {y:.1f})">'
            f'{escape(ch)}'
            f'</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def generate_captcha() -> tuple[str, str]:
    """返回 (正确答案, SVG html)。答案全为小写，校验时请做 `.lower()` 后对比。"""
    code = generate_code()
    return code, _render_svg(code)


def verify_captcha(user_input: str, expected: str) -> bool:
    """大小写不敏感 + 两侧去空白 的校验。"""
    if not expected:
        return False
    return (user_input or "").strip().lower() == expected.strip().lower()
