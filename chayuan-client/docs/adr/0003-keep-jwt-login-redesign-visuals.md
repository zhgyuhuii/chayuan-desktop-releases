# ADR-0003 登录:保留 JWT,仅做视觉重塑

- 状态:已采纳(2026-04-25)
- 关联里程碑:M4

## 背景

参考图的"登录页"是联想式手机号 + 验证码 + 国家码 + 协议复选 + 蓝色 CTA。
后端 `chayuan-server` 当前认证是 **JWT 用户名/密码**(`auth_routes.py`),没有手机号/验证码端点。

## 决策

**不引入手机号登录**。保留 `useAuthStore` 与 `/auth/login` 现状;仅按参考图改造 `LoginPage` 的**视觉与交互**。

## 实现要点

- 形态:**Modal-in-place**(像参考图那样浮在主页之上);未登录用户访问受限路由 → 弹 Modal,而不是整页跳 `/login`。
- 字段:用户名 + 密码;协议复选 + 链接(隐私声明 / 注册协议)。
- 视觉:蓝色 CTA "登录"(替代设计图的"下一步");灰底输入框、与参考图同密度间距;保留头像 / Lenovo 纵列样式位但替换为察元品牌字。
- 错误:参考图未给出错误态;沿用现有 `BizError` toast;字段级错误用 `aria-describedby` 提示。
- 游客:保留 `?guest=1` 旁路。

## 理由

- 后端当前没有 SMS provider 集成;手机号登录需要新建表 / 限速 / 防刷,工作量明显大于本次 UI 重构目标。
- 用户量级目前以企业内部 + admin 为主,JWT 用户名密码已满足。
- Modal-in-place 形态既能匹配参考图,又免去 `/login` 独立页对多 Tab 工作区的中断。

## 后果

- `LoginPage` 改名 `LoginCard` + `LoginDialog` 包装。
- Router 守卫:未登录访问 `/chat /kb /marketplace /space /skill` 不再 `redirect to /login`,而是触发 `cy:open-login` CustomEvent → `Chrome` 渲染 `LoginDialog`。
- M4 留扩展点:`auth-store.signInWithPhone()` 接口先空实现,真要做 SMS 时不破当前调用方。
