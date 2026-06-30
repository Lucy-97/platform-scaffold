// src/lib/tokenManager.ts
//
// 用闭包在内存中保存 accessToken（模块单例，全应用共享）。
// 浏览器刷新后内存丢失，由 apiClient 的 refresh 流程自动重新获取。
// refreshToken / 过期时间存 localStorage（见 apiClient）。

let accessToken: string | null = null;

/** 设置内存中的 accessToken。 */
export const setToken = (token: string | null): void => {
  accessToken = token;
};

/** 获取内存中的 accessToken。 */
export const getToken = (): string | null => accessToken;

/** 清除内存中的 accessToken（登出时调用）。 */
export const clearToken = (): void => {
  accessToken = null;
};
