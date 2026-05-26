export const CHAYUAN_LOGO_URL = new URL('../../../../images/logo.png', import.meta.url).href;

// 关注 / 收款二维码 — 用户在 ``chayuan-client/images/pay/`` 下放原图,
// Vite 通过 import.meta.url 解析成 bundled asset URL。HomePage 二维码区使用。
export const FOLLOW_QR_URL = new URL('../../../../images/pay/follow.png', import.meta.url).href;
export const ALIPAY_QR_URL = new URL('../../../../images/pay/alipay.png', import.meta.url).href;
export const WXPAY_QR_URL = new URL('../../../../images/pay/wxpay.png', import.meta.url).href;
