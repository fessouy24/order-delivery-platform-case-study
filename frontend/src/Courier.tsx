import {CashAmounts,type CashStatement} from './CourierCash';
import { useEffect, useRef, useState } from 'react';
import { all, api, today, type Order } from './api';
import { t, getLanguage } from './i18n';
import { ProductPhoto, StatusBadge, Icon, quantity } from './Visuals';

function courierLabel(order: Order) { return (getLanguage() === 'ar' ? order.courier_label_ar : order.courier_label) || order.customer_name; }

type Action = 'cancel' | 'deliver' | 'postpone' | 'no-answer';
export function Courier() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [selected, setSelected] = useState<Order | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [date, setDate] = useState('');
  const [reason, setReason] = useState('');
  const [amount, setAmount] = useState('0');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [cash,setCash]=useState<CashStatement>({balance:{},entries:[]});
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    let active = true;
    const load = () => Promise.all([all<Order>('/api/v1/orders/'),api<CashStatement>('/api/v1/courier/cash/')]).then(([rows,cash]) => {
      if (active) { setOrders(rows);setCash(cash); setError(''); }
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    void load();
    const timer = window.setInterval(() => { void load(); }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (action) dialog.current?.showModal();
    else dialog.current?.close();
  }, [action]);
  const activeOrders = orders.filter(order => !['DELIVERED', 'CANCELLED'].includes(order.state));
  const closedOrders = orders.filter(order => ['DELIVERED', 'CANCELLED'].includes(order.state));
  function open(order: Order) {
    setSelected(order); setError(''); setNotice('');
    window.scrollTo({ top: 0 });
  }
  function confirm(value: Action) {
    setReason(''); setDate(''); setError('');
    setAmount(selected?.expected_collection ?? '0'); setAction(value);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !action || busy) return;
    setBusy(true); setError('');
    try {
      const payload = { version: selected.version, ...(action === 'deliver' ? { collected: amount } : { reason }), ...(action === 'postpone' ? { delivery_date: date } : {}) };
      await api<Order>(`/api/v1/orders/${selected.id}/${action}/`, payload);
      const label = action === 'deliver' ? 'Delivery confirmed.' : action === 'cancel' ? 'Cancellation confirmed.' : action === 'no-answer' ? 'Unanswered delivery recorded. Admin notified.' : 'New delivery date saved.';
      setAction(null); setSelected(null); setNotice(label);
      setOrders(await all<Order>('/api/v1/orders/'));setCash(await api<CashStatement>('/api/v1/courier/cash/'));
      window.scrollTo({ top: 0 });
    } catch (e) {
      setError((e as Error).message);
      // Refresh the version after a conflict; keep the confirmation open for review.
      try { const fresh = await api<Order>(`/api/v1/orders/${selected.id}/`); setSelected(fresh); } catch { /* The original error remains visible. */ }
    } finally { setBusy(false); }
  }
  return <section className="courier-screen">
    {notice && <p className="success" role="status">{t(notice)}</p>}
    {error && !action && <p className="error" role="alert">{t(error)}</p>}
    {!selected ? <><div className="toolbar"><h1>{t('My deliveries')}</h1><span aria-label={t('Cash awaiting handover')} className="cash-counter">{t('Cash awaiting handover')}: <CashAmounts amounts={cash.balance}/></span></div>{loading && <p role="status">{t('Loading orders…')}</p>}
      {!loading && activeOrders.length === 0 && <p className="empty">{t('No assigned deliveries.')}</p>}
      <div className="courier-list">{activeOrders.map(order => <button className="order-row" key={order.id} onClick={() => open(order)}><span><strong>{courierLabel(order)}</strong></span></button>)}</div>
      {closedOrders.length > 0 && <details className="panel courier-history"><summary>{t('Completed and cancelled orders')}</summary>{closedOrders.map(order => <button className="order-row" key={order.id} onClick={() => open(order)}><span><strong>{courierLabel(order)}</strong></span></button>)}</details>}
    </> : <>
      <button className="secondary" onClick={() => { setSelected(null); setError(''); }}>{t('Back to my deliveries')}</button>
      <h1>{courierLabel(selected)}</h1><p>{selected.customer_name}</p><StatusBadge state={selected.state}/>
      <dl className="panel courier-address"><dt>{t('Phone number')}</dt><dd>{selected.customer_phone ? <a href={`tel:${selected.customer_phone.replace(/[^+\d]/g, '')}`}>{selected.customer_phone}</a> : t('Not supplied')}</dd><dt>{t('Address')}</dt><dd>{selected.full_address}</dd><dt>{t('Area')}</dt><dd>{selected.area_name || selected.original_area || t('Needs mapping')}</dd><dt>{t('Delivery day')}</dt><dd>{selected.delivery_date}</dd></dl>
      {selected.source !== 'SHOPIFY' && <a className="button secondary" href={selected.navigation_url} target="_blank" rel="noreferrer">{t('Open map')}</a>}
      {selected.source==='SHOPIFY' && selected.delivery_date===today() && !['DELIVERED','CANCELLED'].includes(selected.state) && <button className="secondary" onClick={()=>{const tab=window.open('about:blank','_blank');void api<{url:string}>(`/api/v1/orders/${selected.id}/whatsapp/`,{}).then(data=>{if(tab)tab.location.href=data.url;else window.location.href=data.url;}).catch(error=>{tab?.close();setError(error.message);});}}>{t('Send WhatsApp message')}</button>}
      <h2>{t('Ordered items')}</h2><div className="product-cards">{selected.lines.filter(line=>!selected.stock_issued || line.sent_quantity==null || Number(line.sent_quantity)>0).map(line => <article className="product-card" key={line.id}><ProductPhoto src={line.photo_url} name={line.name}/><strong>{line.name}</strong><b>× {quantity(selected.stock_issued ? (line.sent_quantity ?? line.quantity) : line.quantity)}</b></article>)}</div>
      {selected.expected_collection !== undefined && <p className="collection">{t('Collect')}{' '}<strong>{selected.expected_collection} {selected.currency}</strong></p>}
      {selected.review_required && <p className="error">{t('Contact your coordinator before updating this order.')}</p>}
      {!['DELIVERED','CANCELLED'].includes(selected.state) && <div className="courier-outcomes"><button className="courier-cancel" disabled={busy} onClick={() => confirm('cancel')}><Icon name="cancel"/>{t('Cancelled')}</button><button className="courier-deliver" disabled={busy || selected.review_required || selected.state !== 'OUT_FOR_DELIVERY'} onClick={() => confirm('deliver')}><Icon name="check"/>{t('Delivered')}</button><button className="courier-postpone" disabled={busy} onClick={() => confirm('postpone')}><Icon name="calendar"/>{t('Postponed')}</button>{selected.source === 'SHOPIFY' && <button className="courier-no-answer" disabled={busy || selected.state !== 'OUT_FOR_DELIVERY'} onClick={() => confirm('no-answer')}><Icon name="phone-x"/>{t('Didn’t answer')}</button>}</div>}
      {!['DELIVERED','CANCELLED','OUT_FOR_DELIVERY'].includes(selected.state) && <p>{t('Delivery confirmation becomes available after the coordinator records dispatch.')}</p>}
    </>}
    <dialog ref={dialog} className="courier-dialog" onCancel={event => { if (busy) event.preventDefault(); else setAction(null); }} onClose={() => { if (!busy) setAction(null); }}>
      <form className="form" onSubmit={event => { void submit(event); }}><h2>{t(action === 'no-answer' ? 'Confirm unanswered delivery' : action === 'deliver' ? 'Confirm delivery' : action === 'cancel' ? 'Confirm cancellation' : 'Postpone delivery')}</h2><p>{selected ? courierLabel(selected) : ''}</p>
        {action === 'no-answer' && <p>{t((selected?.no_answer_count ?? 0) > 0 ? 'This is the second unanswered delivery attempt. Confirming will cancel this order and notify Admin.' : 'Admin will be notified and this order will move to Upcoming on the next company working day.')}</p>}
        {action === 'deliver' && <label>{t('Collected amount')} ({selected?.currency})<input autoFocus required inputMode="decimal" type="number" min="0" step="0.01" value={amount} onChange={e => setAmount(e.target.value)}/></label>}
        {action === 'postpone' && <label>{t('New delivery date')}<input autoFocus required type="date" min={today()} value={date} onChange={e => setDate(e.target.value)}/></label>}
        {action !== 'deliver' && action !== 'no-answer' && <label>{t('Reason')}<select autoFocus={action === 'cancel'} required value={reason} onChange={e => setReason(e.target.value)}><option value="">{t('Select reason')}</option>{(action === 'cancel' ? ['Customer cancelled','Customer refused order','Incorrect order','Other — contact coordinator'] : ['Customer requested another day','Customer unavailable','Delivery could not be completed']).map(value => <option key={value} value={value}>{t(value)}</option>)}</select></label>}
        {error && <p className="error" role="alert">{t(error)}</p>}
        <button className={action === 'deliver' ? 'courier-deliver' : action === 'cancel' ? 'courier-cancel' : action === 'no-answer' ? 'courier-no-answer' : 'courier-postpone'} disabled={busy}>{t(busy ? 'Saving…' : 'Confirm')}</button><button type="button" className="secondary" disabled={busy} onClick={() => setAction(null)}>{t('Go back')}</button>
      </form>
    </dialog>
  </section>;
}
