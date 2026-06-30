// src/lib/sso.ts
//
// OAuth2 SSO 统一登录工具（业务无关骨架）。
// SSO 提供方通过环境变量配置，不与具体 IdP 绑定：
//   NEXT_PUBLIC_SSO_BASE_URL   授权服务器基址（如 https://sso.example.com）
//   NEXT_PUBLIC_SSO_CLIENT_ID  OAuth client id
// 后端交换端点：POST {API}/api/auth/sso/exchange （需在 backend-api 实现）。

import { apiUrl } from './apiUrl';
import { setToken } from './tokenManager';

export const SSO_STATE_KEY = 'sso_probe_state';
export const SSO_SILENT_ATTEMPTED_KEY = 'sso_silent_attempted';

type SSOPrompt = 'login' | 'none';

interface RedirectToSSOLoginOptions {
  prompt?: SSOPrompt;
}

/** 构造 SSO 回调 URI（必须与后端注册的 redirect_uri 一致）。 */
export function getSSORedirectUri(): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  return `${origin}/sso/callback`;
}

/**
 * 用 SSO 授权码换取本地 Token。
 * 调用后端 POST /api/auth/sso/exchange，成功后自动 setToken。
 * @returns true 表示交换成功
 */
export async function exchangeSSOCode(code: string, redirectUri: string): Promise<boolean> {
  try {
    const deviceId =
      (typeof localStorage !== 'undefined' && localStorage.getItem('deviceId')) || 'web';

    const res = await fetch(apiUrl('/api/auth/sso/exchange'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Device-ID': deviceId },
      body: JSON.stringify({ code, deviceId, redirectUri }),
      credentials: 'include',
    });

    if (!res.ok) return false;

    const data = await res.json();
    const accessToken = data?.data?.accessToken;
    if (accessToken) {
      setToken(accessToken);
      const exp = data?.data?.accessTokenExpiration;
      if (exp) {
        localStorage.setItem('accessTokenExpiration', String(Date.now() + exp * 1000));
      }
      return true;
    }
    return false;
  } catch {
    console.warn('[SSO] exchangeSSOCode failed');
    return false;
  }
}

/**
 * 跳转到 SSO 登录页，登录成功后回调当前页面。
 * @param returnUrl 登录成功后回跳的地址，默认当前页面
 */
export function redirectToSSOLogin(
  returnUrl?: string,
  options: RedirectToSSOLoginOptions = {},
): boolean {
  if (typeof window === 'undefined') return false;

  const ssoBase = process.env.NEXT_PUBLIC_SSO_BASE_URL;
  const clientId = process.env.NEXT_PUBLIC_SSO_CLIENT_ID;
  const prompt = options.prompt || 'login';

  if (!ssoBase || !clientId) {
    console.warn('[SSO] NEXT_PUBLIC_SSO_BASE_URL 或 NEXT_PUBLIC_SSO_CLIENT_ID 未配置，已跳过 SSO 跳转');
    return false;
  }

  const returnTo = returnUrl || window.location.href;
  sessionStorage.setItem('sso_return_url', returnTo);

  const redirectUri = getSSORedirectUri();
  const state =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : String(Date.now());

  const ssoUrl = new URL(`${ssoBase}/oauth/authorize`);
  ssoUrl.searchParams.set('client_id', clientId);
  ssoUrl.searchParams.set('redirect_uri', redirectUri);
  ssoUrl.searchParams.set('response_type', 'code');
  ssoUrl.searchParams.set('prompt', prompt);
  ssoUrl.searchParams.set('state', state);

  if (prompt === 'login') {
    sessionStorage.removeItem(SSO_SILENT_ATTEMPTED_KEY);
  }
  sessionStorage.setItem(SSO_STATE_KEY, state);

  window.location.href = ssoUrl.toString();
  return true;
}
