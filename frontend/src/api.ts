import { t } from './i18n';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return t(value);
  if (Array.isArray(value)) return value.map(errorText).join(' ');
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, text]) => `${key === 'detail' ? '' : key.replaceAll('_', ' ') + ': '}${errorText(text)}`).join(' ');
  }
  return 'The request could not be completed.';
}

export async function api<T>(path: string, data?: unknown, method?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const verb = method ?? (data === undefined ? 'GET' : 'POST');
  if (verb !== 'GET') {
    const csrf = await api<{ csrf_token: string }>('/api/v1/auth/csrf/');
    headers['X-CSRFToken'] = csrf.csrf_token;
    headers['Content-Type'] = 'application/json';
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, { method: verb, headers, credentials: 'same-origin', cache: 'no-store',
      body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal });
    const result: unknown = await response.json().catch(() => ({ detail: 'The service is unavailable. Please try again.' }));
    if (!response.ok) throw new ApiError(errorText(result), response.status);
    return result as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('The connection did not complete. Refresh to check whether the action was saved before retrying.', 0);
  } finally { window.clearTimeout(timer); }
}

export async function all<T>(path: string): Promise<T[]> {
  const rows: T[] = [];
  let next: string | null = path;
  for (let page = 0; next && page < 50; page++) {
    const response: T[] | { results: T[]; next: string | null } = await api(next);
    if (Array.isArray(response)) return response;
    rows.push(...response.results);
    next = response.next ? new URL(response.next, window.location.origin).pathname + new URL(response.next, window.location.origin).search : null;
  }
  if (next) throw new ApiError('This list is too large. Narrow the date or operational filter.', 400);
  return rows;
}

export function today() {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Africa/Cairo', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-');
}

export type User = { id: string; username: string; name: string; roles: string[]; mfa_verified: boolean };
export function can(user: User, ...roles: string[]) { return user.roles.includes('Admin') || roles.some(role => user.roles.includes(role)); }
export type Item = { id: string; sku: string; name: string; photo_url?: string; kind: string; unit: string; price: string; manufactured: boolean; fresh: boolean; lead_workdays: number; active: boolean };
export type Location = { id: string; name: string };
export type Address = { maps_url?:string; id: string; customer: string; branch_code: string; full_address: string; original_area: string; phone: string };
export type Customer = { id: string; name: string; phone: string; company: boolean; addresses: Address[] };
export type OrderLine = { id: string; item: string; name: string; sku: string; photo_url?: string; quantity: string; sent_quantity?: string | null; received_quantity?: string | null; unit_price?: string; recipe: string | null; production_complete: boolean; fresh: boolean };
export type Order = { id: string; number: string; no_answer_count?: number; courier_label?: string; courier_label_ar?: string; odoo_number?: string; website_number?: string; fulfillment_status?:string; cash_settled?:boolean; source: string; market_order?: boolean; customer_name: string; customer_phone: string; full_address: string; navigation_url?:string; area_name: string | null;
  original_area: string; branch_code: string; delivery_date: string; earliest_time: string | null; latest_time: string | null; notes: string; state: string;
  warehouse_ready: boolean; allocated: boolean; stock_issued: boolean; review_required: boolean; courier: string | null; courier_name: string | null; version: number;
  payment_method?: string; payment_status?: string; expected_collection?: string; collected?: string; total?: string; currency: string; lines: OrderLine[]; sync_status: string; sync_error?: string };
