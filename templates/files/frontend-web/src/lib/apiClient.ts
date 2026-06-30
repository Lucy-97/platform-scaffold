// src/lib/apiClient.ts
//
// 统一 API 客户端，自动处理：
//   - Bearer token 注入
//   - 403（access token 过期）→ 单飞刷新 + 失败请求排队重试
//   - 401（未登录）→ 跳转 SSO（带 3s 冷却，避免重定向风暴）
//   - 402（余额不足）→ 派发全局事件，由 UI 弹窗
// 响应体约定 { code, msg, data }，与后端 response 包一致。

import { getToken, setToken, clearToken } from './tokenManager';
import { redirectToSSOLogin } from './sso';
import { apiUrl } from './apiUrl';

export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthenticationError';
  }
}

interface QueueItem {
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}

export interface ApiClientOptions extends Omit<RequestInit, 'headers'> {
  handle401?: boolean;
  headers?: Record<string, string>;
}

let isRefreshing = false;
let failedQueue: QueueItem[] = [];

let lastSSORedirectAt = 0;
const SSO_REDIRECT_COOLDOWN = 3000; // 3 秒冷却

const triggerSSORedirect = (handle401: boolean): void => {
  if (!handle401) return;
  if (typeof window === 'undefined') return;
  const now = Date.now();
  if (now - lastSSORedirectAt > SSO_REDIRECT_COOLDOWN) {
    lastSSORedirectAt = now;
    redirectToSSOLogin();
  }
};

const clearAuthState = (): void => {
  clearToken();
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('accessTokenExpiration');
  }
};

const processQueue = (error: unknown, token: string | null = null): void => {
  failedQueue.forEach((prom) => {
    if (error) prom.reject(error);
    else prom.resolve(token!);
  });
  failedQueue = [];
};

/**
 * 封装的 API 请求客户端。
 * @param url 请求 URL（建议用 apiUrl() 拼接）
 * @param options fetch 选项，额外支持 handle401（默认 true）
 */
const apiClient = async (url: string, options: ApiClientOptions = {}): Promise<Response> => {
  const { handle401 = true, ...fetchOptions } = options;

  if (!fetchOptions.headers) fetchOptions.headers = {};

  const token = getToken();
  if (token) fetchOptions.headers['Authorization'] = `Bearer ${token}`;

  if (
    fetchOptions.body &&
    !(fetchOptions.body instanceof FormData) &&
    !fetchOptions.headers['Content-Type']
  ) {
    fetchOptions.headers['Content-Type'] = 'application/json';
  }

  fetchOptions.credentials = 'include';

  const response = await fetch(url, fetchOptions);

  // 401 未登录 → 跳 SSO
  if (response.status === 401) {
    if (handle401) {
      triggerSSORedirect(true);
      return new Response(JSON.stringify({ code: '401', msg: 'Unauthorized', data: null }), {
        status: 401,
        statusText: 'Unauthorized',
      });
    }
    return response;
  }

  // 402 余额不足 → 全局事件
  if (response.status === 402) {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('showInsufficientBalanceModal'));
    }
    return response;
  }

  // 403 token 过期 → 刷新并重试
  if (response.status === 403) {
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({
          resolve: (newToken: string) => {
            fetchOptions.headers!['Authorization'] = `Bearer ${newToken}`;
            resolve(fetch(url, fetchOptions));
          },
          reject,
        });
      });
    }

    isRefreshing = true;
    try {
      const deviceId =
        (typeof localStorage !== 'undefined' && localStorage.getItem('deviceId')) || 'web';
      const refreshResponse = await fetch(apiUrl('/api/auth/refresh'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Device-ID': deviceId },
        body: JSON.stringify({}),
        credentials: 'include',
      });

      const refreshData = await refreshResponse.json().catch(() => null);

      if (!refreshResponse.ok || refreshData?.code !== '200' || !refreshData?.data?.accessToken) {
        const authError = new AuthenticationError(refreshData?.msg || 'Failed to refresh token.');
        processQueue(authError, null);
        clearAuthState();
        triggerSSORedirect(handle401);
        return new Response(JSON.stringify(refreshData || { code: '401', msg: authError.message, data: null }), {
          status: 401,
          statusText: 'Unauthorized',
        });
      }

      const newAccessToken = refreshData.data.accessToken as string;
      setToken(newAccessToken);
      const accessExpiration = Date.now() + refreshData.data.accessTokenExpiration * 1000;
      localStorage.setItem('accessTokenExpiration', String(accessExpiration));

      processQueue(null, newAccessToken);

      fetchOptions.headers['Authorization'] = `Bearer ${newAccessToken}`;
      return fetch(url, fetchOptions);
    } catch (error) {
      processQueue(error, null);
      clearAuthState();
      triggerSSORedirect(handle401);
      return new Response(null, { status: 401, statusText: 'Unauthorized' });
    } finally {
      isRefreshing = false;
    }
  }

  return response;
};

export default apiClient;
