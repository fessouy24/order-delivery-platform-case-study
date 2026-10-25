import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, api, can, today, type Order, type User } from './api';
import { Amend } from './Amend';
import { MarketQuantities } from './MarketQuantities';
import { ProductPhoto, StatusBadge, OrderSteps, quantity } from './Visuals';
type Courier = {
    id: string;
    username: string;
};
type History = {
    action: string;
    time: string;
    actor: string;
    before?: object;
    after?: object;
};
export function Orders({ user, refresh, onChange }: {
    user: User;
    refresh: number;
    onChange: () => void;
}) {
    const [windowName, setWindow] = useState('today');
    const [rows, setRows] = useState<Order[]>([]);
    const [selected, setSelected] = useState<Order | null>(null);
    const [couriers, setCouriers] = useState<Courier[]>([]);
    const [courier, setCourier] = useState('');
    const [area, setArea] = useState('');
    const [numberSearch, setNumberSearch] = useState('');
    const [courierFilter, setCourierFilter] = useState('');
    const [checked, setChecked] = useState<string[]>([]);
    const [reason, setReason] = useState('');
    const [newDate, setNewDate] = useState('');
    const [earliest, setEarliest] = useState('');
    const [latest, setLatest] = useState('');
    const [collected, setCollected] = useState('0');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const [history, setHistory] = useState<History[]>([]);
    const [messageLink, setMessageLink] = useState('');
    const [returnDisposition, setReturnDisposition] = useState('RESTOCK');
    const [handover, setHandover] = useState('');
    const [areas, setAreas] = useState<{
        id: string;
        name: string;
    }[]>([]);
    const [routeArea, setRouteArea] = useState('');
    const [phone, setPhone] = useState('');
    const [address, setAddress] = useState('');
    const [reportStart, setReportStart] = useState(today());
    const [reportEnd, setReportEnd] = useState(today());
    const [approvedAmount, setApprovedAmount] = useState('0');
    const [approvedStatus, setApprovedStatus] = useState('PAID');
    useEffect(() => {
        if (!selected) return;
        const back = (event: Event) => { event.preventDefault(); setSelected(null); window.scrollTo({top:0}); };
        window.addEventListener('opsdemo-back', back);
        return () => window.removeEventListener('opsdemo-back', back);
    }, [selected]);
    useEffect(() => {
        let mounted = true;
        setLoading(true);
        setError('');
        const load = () => all<Order>(`/api/v1/orders/${windowName === 'all' ? '' : '?window=' + windowName}`)
            .then(data => { if (mounted) {
            setRows(data);
            setSelected(current => current && data.some(order => order.id === current.id) ? current : null);
        } })
            .catch(error => { if (mounted)
            setError(error.message); }).finally(() => { if (mounted)
            setLoading(false); });
        void load();
        const timer = window.setInterval(() => { void load(); }, 15000);
        return () => { mounted = false; window.clearInterval(timer); };
    }, [windowName, refresh]);
    useEffect(() => {
        if (can(user, 'Supply Chain', 'Management'))
            api<Courier[]>('/api/v1/couriers/').then(setCouriers).catch(error => setError(error.message));
        if (can(user, 'Supply Chain'))
            all<{
                id: string;
                name: string;
            }>('/api/v1/areas/').then(setAreas).catch(error => setError(error.message));
    }, [user]);
    async function command(action: string, data: object = {}) {
        if (!selected)
            return;
        setBusy(true);
        setError('');
        try {
            const updated = await api<Order>(`/api/v1/orders/${selected.id}/${action}/`, { version: selected.version, ...data });
            setSelected(updated);
            setHistory(await api<History[]>(`/api/v1/orders/${updated.id}/history/`));
            onChange();
        }
        catch (error) {
            setError((error as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    async function viewHistory(order: Order) {
        setSelected(order);
        setCollected(order.expected_collection ?? '0');
        setHistory([]);
        setMessageLink('');
        setReason('');
        setNewDate('');
        setRouteArea('');
        setPhone(order.customer_phone);
        setAddress(order.full_address);
        setApprovedAmount(order.expected_collection ?? '0');
        setApprovedStatus(order.payment_status === 'PAID' ? 'PAID' : ['PARTIAL', 'PARTIALLY_PAID'].includes(order.payment_status ?? '') ? 'PARTIAL' : 'UNPAID');
        try {
            setHistory(await api<History[]>(`/api/v1/orders/${order.id}/history/`));
        }
        catch (error) {
            setError((error as Error).message);
        }
    }
    const filtered = rows.filter(order => (!numberSearch.trim() || [order.number,order.website_number,order.odoo_number,order.notes].some(value => value?.toLowerCase().includes(numberSearch.trim().toLowerCase()))) && (!area || (order.area_name ?? 'Needs mapping') === area) && (!courierFilter || (courierFilter === 'unassigned' ? !order.courier : order.courier === courierFilter)));
    const active = selected && !['CANCELLED', 'DELIVERED'].includes(selected.state);
    return <section>
    <div className="section-header"><div><h1>{t(can(user, 'Courier') && !can(user, 'Supply Chain') ? 'My deliveries' : 'Orders and dispatch')}</h1><p>{t("Orders are grouped by their current delivery schedule.")}</p></div>
      <div className="toolbar"><input aria-label={t("Search order number")} placeholder={t("Search order number")} value={numberSearch} onChange={event => setNumberSearch(event.target.value)}/><select aria-label={t("Order window")} value={windowName} onChange={event => setWindow(event.target.value)}>
        <option value="today">{t("Today")}</option><option value="upcoming">{t("Upcoming")}</option><option value="postponed">{t("Postponed")}</option><option value="urgent">{t("Time restricted today")}</option><option value="exceptions">{t("Exceptions")}</option><option value="all">{t("Full history")}</option></select>
        <select aria-label={t("Area")} value={area} onChange={event => setArea(event.target.value)}><option value="">{t("All areas")}</option>{[...new Set(rows.map(order => order.area_name ?? 'Needs mapping'))].map(name => <option key={name}>{name}</option>)}</select>
        {couriers.length > 0 && <select aria-label={t("Courier filter")} value={courierFilter} onChange={event => setCourierFilter(event.target.value)}><option value="">{t("All couriers")}</option><option value="unassigned">{t("Unassigned")}</option>{couriers.map(courier => <option key={courier.id} value={courier.id}>{courier.username}</option>)}</select>}
      </div>
    </div>
    {error && <p className="error" role="alert">{t(error)}</p>}
    {loading ? <p>{t("Loading orders\u2026")}</p> : <p>{filtered.length}{' '}{t(filtered.length === 1 ? 'order in this view' : 'orders in this view')}</p>}
    {can(user, 'Management', 'Sales', 'Supply Chain', 'Finance') && <details className="panel"><summary>{t("Download an order report")}</summary><div className="toolbar"><label>{t("From")}<input type="date" required value={reportStart} onChange={event => setReportStart(event.target.value)}/></label><label>{t("Through")}<input type="date" required value={reportEnd} onChange={event => setReportEnd(event.target.value)}/></label><a className="button secondary" href={`/api/v1/reports/orders/?start=${reportStart}&end=${reportEnd}`}>{t("Download CSV")}</a></div><small>{t("The report includes only orders and financial fields authorized for your role.")}</small></details>}
    {can(user, 'Supply Chain') && <div className="panel toolbar"><button className="secondary" onClick={() => setChecked(filtered.filter(order => order.warehouse_ready && !order.stock_issued && !['CANCELLED', 'DELIVERED'].includes(order.state)).map(order => order.id))}>{t("Select ready orders in this view")}</button><button className="secondary" onClick={() => setChecked([])}>{t("Clear selection")}</button><select aria-label={t("Batch courier")} value={courier} onChange={event => setCourier(event.target.value)}><option value="">{t("Assign to courier")}</option>{couriers.map(row => <option key={row.id} value={row.id}>{row.username}</option>)}</select><button disabled={busy || !checked.length || !courier} onClick={() => { setBusy(true); setError(''); void api('/api/v1/orders/assign-batch/', { courier, orders: rows.filter(order => checked.includes(order.id)).map(order => ({ id: order.id, version: order.version })) }).then(() => { setChecked([]); onChange(); }).catch(error => setError(error.message)).finally(() => setBusy(false)); }}>{t("Assign")}{' '}{checked.length}{' '}{t("selected")}</button></div>}
    <div className={`order-grid${selected ? ' has-detail' : ''}`}><div className="order-list">
      {filtered.map(order => <div key={order.id}>{can(user, 'Supply Chain') && order.warehouse_ready && !order.stock_issued && !['CANCELLED', 'DELIVERED'].includes(order.state) && <label className="checkbox"><input type="checkbox" checked={checked.includes(order.id)} onChange={event => setChecked(event.target.checked ? [...checked, order.id] : checked.filter(id => id !== order.id))}/>{t("Select")}{' '}{order.number}</label>}<button className={`order-row ${selected?.id === order.id ? 'selected' : ''}`} style={{ width: '100%' }} onClick={() => { void viewHistory(order); }}>
        <span><strong>{order.number}{order.odoo_number ? ` · ${order.odoo_number}` : ''}</strong><span>{order.customer_name} · {order.branch_code}</span><span>{order.area_name ?? 'Area needs mapping'} · {order.delivery_date}</span></span>
        <span className="order-flags"><StatusBadge state={order.state}/><span>{order.courier_name ?? t('Unassigned')}</span>
          {!order.allocated && !order.stock_issued && <span className="warning">{t("Stock shortage")}</span>}{order.earliest_time && <span className="warning">{t("After")}{' '}{order.earliest_time.slice(0, 5)}</span>}</span>
      </button></div>)}
      {!loading && !filtered.length && <div className="empty">{t("No orders match this view.")}</div>}
    </div>
    {selected && <article className="panel order-detail">
      <button className="secondary" onClick={() => { setSelected(null); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>{t('Back to orders')}</button>
      <h2>{selected.number}</h2><p><strong>{selected.customer_name}</strong></p><p>{selected.full_address}</p>
      <OrderSteps state={selected.state}/>
      <p>{t("Original area:")}{' '}{selected.original_area || t('Not supplied')}</p>
      <p>{selected.delivery_date}{selected.earliest_time && ` · After ${selected.earliest_time.slice(0, 5)}`}{selected.latest_time && ` · Before ${selected.latest_time.slice(0, 5)}`}</p>
      <p>{selected.notes || t('No customer notes')}</p><div className="product-cards">{selected.lines.map(line => <article className="product-card" key={line.id}><ProductPhoto src={line.photo_url} name={line.name}/><div><strong>{line.name}</strong><small>{line.sku}</small></div><b>× {quantity(line.quantity)}</b></article>)}</div>
      {selected.expected_collection !== undefined && <p className="collection">{t("Collect")}{' '}{selected.expected_collection} {selected.currency} · {selected.payment_method} · {selected.payment_status}</p>}
      {selected.total !== undefined && <p>{t("Order total")}{' '}{selected.total} {selected.currency}</p>}
      <p>{t("Odoo:")}{' '}{selected.sync_status}{selected.sync_error && ` · ${selected.sync_error}`}</p>
      {selected.review_required && <p className="error">{t("This order is held for upstream order or payment review. Production and delivery confirmation are blocked until reconciliation.")}</p>}
      <div className="toolbar"><a className="button secondary" href={`tel:${selected.customer_phone.replace(/[^+\d]/g, '')}`}>{t("Call customer")}</a>
        {can(user, 'Warehouse', 'Supply Chain', 'Courier', 'Management') && <a className="button secondary" href={`/api/v1/documents/${selected.id}/dispatch/`} target="_blank" rel="noreferrer">{t("Print dispatch sheet")}</a>}
        {selected.source !== 'SHOPIFY' && <a className="button secondary" href={selected.navigation_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(selected.full_address)}`} target="_blank" rel="noreferrer">{t("Open map")}</a>}
        {can(user, 'Courier', 'Supply Chain') && <button className="secondary" onClick={() => { void api<{
            url: string;
        }>(`/api/v1/orders/${selected.id}/whatsapp/`, {}).then(data => setMessageLink(data.url)).catch(error => setError(error.message)); }}>{t("Prepare WhatsApp message")}</button>}
        {messageLink && <a className="button" href={messageLink} target="_blank" rel="noreferrer">{t("Open WhatsApp")}</a>}
      </div>
      {selected.market_order && selected.state !== 'CANCELLED' && <MarketQuantities key={selected.id+':'+selected.version} order={selected} user={user} busy={busy} save={command}/>}
      {active && <div className="actions form">
        {can(user, 'Warehouse') && <div className="toolbar"><button disabled={busy} onClick={() => { void command('reserve'); }}>{t("Recalculate allocation")}</button><button disabled={busy || selected.warehouse_ready} onClick={() => { void command('ready'); }}>{t("Confirm warehouse readiness")}</button></div>}
        {can(user, 'Supply Chain') && <div><label>{t("Courier")}<select value={courier} onChange={event => setCourier(event.target.value)}><option value="">{t("Select courier")}</option>{couriers.map(row => <option key={row.id} value={row.id}>{row.username}</option>)}</select></label>
          {selected.stock_issued && <label>{t("Custody handover reason")}<input value={handover} onChange={event => setHandover(event.target.value)} placeholder={t("Record the actual stock handover before reassigning")}/></label>}
          <div className="toolbar"><button disabled={busy || !courier || !selected.warehouse_ready} onClick={() => { void command('assign', { courier, handover_reason: handover }); }}>{t("Assign courier")}</button><button disabled={busy || !selected.courier || selected.delivery_date !== today()} onClick={() => { void command('dispatch'); }}>{t("Record dispatch")}</button></div></div>}
        {can(user, 'Courier', 'Supply Chain') && <>
          {selected.state === 'OUT_FOR_DELIVERY' && <div><label>{t("Collected amount")}<input inputMode="decimal" type="number" min="0" step="0.01" value={collected} onChange={event => setCollected(event.target.value)}/></label>
            <button disabled={busy} onClick={() => { void command('deliver', { collected }); }}>{t("Mark delivered")}</button></div>}
          <label>{t("Reason for postponement or cancellation")}<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500}/></label>
          <div className="field-row"><label>{t("New delivery date")}<input type="date" value={newDate} onChange={event => setNewDate(event.target.value)}/></label><label>{t("After")}<input type="time" value={earliest} onChange={event => setEarliest(event.target.value)}/></label><label>{t("Before")}<input type="time" value={latest} onChange={event => setLatest(event.target.value)}/></label></div>
          <div className="toolbar"><button disabled={busy || !reason || (!newDate && !earliest && !latest)} onClick={() => { void command('postpone', { reason, ...(newDate ? { delivery_date: newDate } : {}), earliest_time: earliest || null, latest_time: latest || null }); }}>{t("Postpone")}</button>
            <button className="danger" disabled={busy || !reason} onClick={() => { void command('cancel', { reason }); }}>{t("Cancel order")}</button></div>
        </>}
      </div>}
      {can(user, 'Finance') && active && <details className="panel"><summary>{t("Review and approve remaining collection")}</summary><form className="form" onSubmit={event => { event.preventDefault(); void command('collection-review', { expected_collection: approvedAmount, payment_status: approvedStatus, reason }); }}><label>{t("Approved remaining amount")}<input type="number" required min="0" step="0.01" value={approvedAmount} onChange={event => setApprovedAmount(event.target.value)}/></label><label>{t("Confirmed payment status")}<select value={approvedStatus} onChange={event => setApprovedStatus(event.target.value)}><option value="PAID">{t("Paid")}</option><option value="PARTIAL">{t("Partially paid")}</option><option value="UNPAID">{t("Unpaid")}</option></select></label><label>{t("Finance verification reason")}<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Approve collection")}</button><small>{t("This resolves payment review only. Product, quantity or commercial-total changes require separate reconciliation.")}</small></form></details>}
      {can(user, 'Supply Chain') && active && <details className="panel"><summary>{t("Correct routing or contact details")}</summary><form className="form" onSubmit={event => { event.preventDefault(); void command('routing', { ...(routeArea ? { area: routeArea } : {}), customer_phone: phone, full_address: address, reason }); }}><label>{t("Standard delivery area")}<select value={routeArea} onChange={event => setRouteArea(event.target.value)}><option value="">{t("Keep current area")}</option>{areas.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{t("Customer phone")}<input required type="tel" value={phone} onChange={event => setPhone(event.target.value)}/></label><label>{t("Delivery address")}<textarea required value={address} onChange={event => setAddress(event.target.value)}/></label><label>{t("Correction reason")}<input required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Save correction")}</button><small>{t("The original area entry remains in the record.")}</small></form></details>}
      {can(user, 'Warehouse') && active && selected.lines.some(line => line.fresh && line.production_complete) && <form className="form panel" onSubmit={event => { event.preventDefault(); void command('freshness-review', { delivery_date: newDate, reason }); }}><h3>{t("Freshness review for postponement")}</h3><label>{t("Inspected new delivery date")}<input type="date" required value={newDate} onChange={event => setNewDate(event.target.value)}/></label><label>{t("Inspection result and approval reason")}<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Record freshness approval")}</button><small>{t("Approval applies to this date and current order version. Supply Chain or the courier then records the postponement.")}</small></form>}
      {can(user, 'Warehouse') && selected.state === 'CANCELLED' && selected.stock_issued && <form className="form panel" onSubmit={event => { event.preventDefault(); void command('receive-return', { disposition: returnDisposition, reason }); }}><h3>{t("Receive issued stock back")}</h3><label>{t("Physical inspection outcome")}<select value={returnDisposition} onChange={event => setReturnDisposition(event.target.value)}><option value="RESTOCK">{t("Received, inspected and suitable for restocking")}</option><option value="DISCARD">{t("Received and discarded")}</option></select></label><label>{t("Physical receipt and quality inspection reason")}<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Confirm physical return")}</button><small>{t("Confirm only after receiving every issued product. Partial returns require a separate stock investigation.")}</small></form>}
      <h3>{t("Order history")}</h3><ol className="history">{history.map((event, index) => <li key={index}><strong>{t(event.action.replaceAll('.', ' ').replaceAll('_', ' '))}</strong><span>{event.actor} · {new Date(event.time).toLocaleString(undefined, { timeZone: 'Africa/Cairo' })}</span></li>)}</ol>
      {can(user, 'Sales') && active && selected.source === 'B2B' && !selected.stock_issued && !selected.lines.some(line => line.production_complete) && ['PENDING', 'ERROR'].includes(selected.sync_status) && <Amend order={selected} busy={busy} save={data => command('amend', data)}/>}
    </article>}
    </div>
  </section>;
}
