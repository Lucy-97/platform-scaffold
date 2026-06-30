// src/api.ts
//
// Admin 后台统一 API 客户端：基址 + token 注入 + 401 处理 + {code,msg,data} 解析。
// 基址来自 Vite 环境变量 VITE_API_BASE_URL（指向 gateway）。

const BASE = ((import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_BASE_URL) || "";
const TOKEN_KEY = "admin_token";

export const setAdminToken = (t: string | null): void => {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
};

export const getAdminToken = (): string | null => localStorage.getItem(TOKEN_KEY);

export interface ApiResult<T = unknown> {
  code: string;
  msg: string;
  data: T;
}

/** 发起一次后台 API 请求，返回标准响应体。401 时清 token 并跳转登录页。 */
export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };
  const tok = getAdminToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;

  const resp = await fetch(`${BASE}${path}`, { ...options, headers, credentials: "include" });

  if (resp.status === 401) {
    setAdminToken(null);
    if (typeof window !== "undefined") window.location.href = "/login";
  }

  return resp
    .json()
    .catch(() => ({ code: String(resp.status), msg: resp.statusText, data: null as unknown as T }));
}
