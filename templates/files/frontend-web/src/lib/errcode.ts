// src/lib/errcode.ts
//
// 业务错误码 → 展示文案映射，与 backend-api/internal/errcode 的 6 位码对齐。
// 后端返回 { code, msg, data }；前端按 code 翻译为本地化文案（这里给中文示例）。
// 新增后端错误码时，请同步在此登记。

export const ERR_MESSAGES: Record<string, string> = {
  // 系统层 000xxx
  '000001': '登录状态无效，请重新登录',
  '000002': '登录已过期，请重新登录',
  '000004': '请先登录',
  '000005': '没有权限访问该资源',
  '000006': '服务繁忙，请稍后重试',
  '000007': '参数缺失或不合法',
  '000099': '服务器内部错误',
  // 资源 103xxx
  '103404': '资源不存在',
  // 基础设施 105xxx
  '105001': '外部服务请求失败，请稍后重试',
  '105002': '操作太频繁，请稍后重试',
};

/** 把后端错误码翻译为展示文案；未登记的 code 回退到后端 msg 或通用提示。 */
export function errMessage(code: string, fallbackMsg?: string): string {
  return ERR_MESSAGES[code] || fallbackMsg || '操作失败，请稍后重试';
}

/** 标准响应体形状。 */
export interface ApiResult<T = unknown> {
  code: string;
  msg: string;
  data: T;
}

/** code === '200' 视为成功。 */
export function isOk<T>(r: ApiResult<T> | null | undefined): r is ApiResult<T> {
  return !!r && r.code === '200';
}
