// src/lib/apiUrl.ts
//
// 拼接 API 完整地址。基址来自 NEXT_PUBLIC_API_BASE_URL（指向 gateway）。

export const apiUrl = (path: string): string => {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || '';
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${normalizedPath}`;
};
