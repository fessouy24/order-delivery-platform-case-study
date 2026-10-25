import { useEffect, useState } from 'react';
import { api, can, type Order, type User } from './api';
import { ProductPhoto, StatusBadge, quantity } from './Visuals';
import { t, getLanguage } from './i18n';

type Row = { id: string; number?: string; month: string; values: (string | number | null)[] };
type Table = { headers: string[]; colors: Record<string, [string, string]>; dataset: string; source_name: string; count: number; page: number; pages: number; months: string[]; statuses: string[]; couriers: string[]; summary: Record<string, number>; results: Row[] };
export function FleetReport({ user, refresh }: { user: User; refresh: number }) {
 const initial = new URLSearchParams(window.location.search);
 const historyAllowed = can(user, 'Management', 'Supply Chain');
 const [dataset, setDataset] = useState(initial.get('dataset') === 'history' && historyAllowed ? 'history' : 'live');
 const [start, setStart] = useState(initial.get('start') || ''), [end, setEnd] = useState(initial.get('end') || '');
 const [query, setQuery] = useState(initial.get('q') || ''), [month, setMonth] = useState(initial.get('month') || '');
 const [status, setStatus] = useState(initial.get('status') || ''), [courier, setCourier] = useState(initial.get('report_courier') || '');
 const [page, setPage] = useState(1), [data, setData] = useState<Table | null>(null);
 const [error, setError] = useState(''), [selected, setSelected] = useState<Order | null>(null);
 const [exporting, setExporting] = useState(false), [copied, setCopied] = useState(false);
 const filters = new URLSearchParams({ dataset, start, end, q: query, month, status, report_courier: courier });
 const valid = !start || !end || start <= end;
 useEffect(() => { setPage(1); setSelected(null); setCopied(false); }, [dataset, start, end, query, month, status, courier]);
 useEffect(() => {
  if (!valid) { setData(null); setError('Choose a valid date range.'); return; }
  let active = true;
  const load = () => api<Table>(`/api/v1/reports/orders/table/?${new URLSearchParams({ dataset, start, end, q: query, month, status, report_courier: courier, page: String(page) })}`)
   .then(result => { if (active) { setData(result); setError(''); } }).catch(e => { if (active) { setData(null); setError(e.message); } });
  const debounce = window.setTimeout(() => { void load(); }, 250);
  const timer = dataset === 'live' ? window.setInterval(() => { void load(); }, 15000) : undefined;
  return () => { active = false; window.clearTimeout(debounce); window.clearInterval(timer); };
 }, [dataset, start, end, query, month, status, courier, page, refresh, valid]);
 useEffect(() => {
  if (!selected) return;
  const back = (event: Event) => { event.preventDefault(); setSelected(null); window.scrollTo({ top: 0 }); };
  window.addEventListener('opsdemo-back', back); return () => window.removeEventListener('opsdemo-back', back);
 }, [selected]);
 function clear() { setStart(''); setEnd(''); setQuery(''); setMonth(''); setStatus(''); setCourier(''); setPage(1); }
 async function exportReport() {
  setExporting(true); setError('');
  try {
   const response = await fetch(`/api/v1/reports/orders/table/?${filters}&export=xlsx`, { credentials: 'same-origin' });
   if (!response.ok) { const message = await response.json(); throw new Error(Object.values(message).flat().join(' ')); }
   const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
   link.href = url; link.download = dataset === 'history' ? 'opsdemo-original-excel-history.xlsx' : 'opsdemo-order-details.xlsx'; link.click();
   window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { setError((e as Error).message); } finally { setExporting(false); }
 }
 function statusLabel(label: string) { return t(label === 'Pending' && getLanguage() === 'ar' ? 'Order pending' : label); }
 function value(v: string | number | null, column: number) { return v === null || v === '' ? '—' : column === 4 ? String(v).replace('T', ' ').slice(0, 16) : String(v); }
 return <section className="orders-excel"><div className="section-header"><div><h1>{t('Orders Excel')}</h1><p>{t('Your monthly Excel layout, inside the system.')}</p></div><div className="toolbar">
  <button className="secondary" onClick={() => { void navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}?view=fleet&${filters}`).then(() => setCopied(true)).catch(e => setError(e.message)); }}>{t(copied ? 'Link copied' : 'Copy report link')}</button>
  <button disabled={exporting || !valid || !data} onClick={() => { void exportReport(); }}>{t('Export spreadsheet')}</button></div></div>
  <div className="toolbar" role="group" aria-label={t('Order records')}><button className={dataset === 'live' ? '' : 'secondary'} aria-pressed={dataset === 'live'} onClick={() => { setDataset('live'); clear(); setData(null); }}>{t('Live system orders')}</button>
   {historyAllowed && <button className={dataset === 'history' ? '' : 'secondary'} aria-pressed={dataset === 'history'} onClick={() => { setDataset('history'); clear(); setData(null); }}>{t('Original Excel history')}</button>}</div>
  <p>{t(dataset === 'history' ? 'Original workbook records are read-only history. They do not create deliveries or send updates to Shopify or Odoo.' : 'All system order types use your Excel columns. Statuses update automatically as staff work.')}</p>
  {dataset === 'history' && data?.source_name && <p className="source-name">{t('Source workbook')}: {data.source_name}</p>}
  <div className="panel report-filters"><label>{t('Search')}<input placeholder={t('Order, customer, phone or notes')} value={query} onChange={e => setQuery(e.target.value)}/></label>
   <label>{t('Month')}<select value={month} onChange={e => setMonth(e.target.value)}><option value="">{t('All months')}</option>{data?.months.map(m => <option key={m} value={m}>{t(m)}</option>)}</select></label>
   <label>{t('Status')}<select value={status} onChange={e => setStatus(e.target.value)}><option value="">{t('All statuses')}</option>{data?.statuses.map(s => <option key={s} value={s}>{statusLabel(s || 'Blank')}</option>)}</select></label>
   <label>{t('Courier')}<select value={courier} onChange={e => setCourier(e.target.value)}><option value="">{t('All couriers')}</option>{data?.couriers.map(c => <option key={c} value={c}>{c}</option>)}</select></label>
   <label>{t('Delivery date from')}<input type="date" value={start} max={end} onChange={e => setStart(e.target.value)}/></label><label>{t('Delivery date through')}<input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)}/></label>
   <button className="secondary" onClick={clear}>{t('Clear filters')}</button></div>
  {error && <p role="alert" className="error">{t(error)}</p>}
  <p className="report-status"><strong>{data?.count ?? '…'}</strong>{' '}{t('matching orders')}{dataset === 'live' && <> · {t('Updates automatically every 15 seconds')}</>}</p>
  {data && <div className="excel-status-summary">{Object.entries(data.summary).map(([s, count]) => <span key={s} style={data.colors[s] ? { backgroundColor: `#${data.colors[s][0]}`, color: `#${data.colors[s][1]}` } : {}}>{statusLabel(s || 'Blank')}: {count}</span>)}</div>}
  <div className="table-wrap panel excel-orders-table"><table aria-label={t('Orders Excel')}><thead><tr>{data?.headers.map(header => <th key={header} scope="col">{t(header)}</th>)}</tr></thead><tbody>
   {data?.results.map(row => <tr key={row.id}>{row.values.map((v, column) => <td key={column} style={column === 5 && data.colors[String(v)] ? { backgroundColor: `#${data.colors[String(v)][0]}`, color: `#${data.colors[String(v)][1]}`, fontWeight: 700 } : {}}>
    {dataset === 'live' && column === 2 ? <button className="table-link" title={row.number} onClick={() => { void api<Order>(`/api/v1/orders/${row.id}/`).then(setSelected).catch(e => setError(e.message)); }}>{value(v, column)}</button> : column === 5 ? statusLabel(value(v, column)) : value(v, column)}
   </td>)}</tr>)}
  </tbody></table>{data?.count === 0 && <div className="empty">{t('No orders match this view.')}</div>}</div>
  <div className="toolbar"><button className="secondary" disabled={!data || page <= 1} onClick={() => setPage(p => p - 1)}>{t('Previous')}</button><span>{t('Page')} {page} {t('of')} {data?.pages ?? 1}</span><button className="secondary" disabled={!data || page >= data.pages} onClick={() => setPage(p => p + 1)}>{t('Next')}</button></div>
  {selected && <aside className="panel report-detail"><div className="section-header"><h2>{selected.number}</h2><button className="secondary" onClick={() => { setSelected(null); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>{t('Back to report')}</button></div><StatusBadge state={selected.state}/><p>{selected.customer_name} · {selected.full_address}</p><div className="product-cards">{selected.lines.map(line => <div className="product-card" key={line.id}><ProductPhoto src={line.photo_url} name={line.name}/><strong>{line.name}</strong><small>{line.sku}</small><b>× {quantity(line.quantity)}</b></div>)}</div></aside>}
 </section>;
}
