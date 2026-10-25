import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, api, can, today, type Item, type Location, type User } from './api';
import { ProductPhoto, quantity } from './Visuals';
type Material = {
    photo_url?: string;
    item_id: string;
    sku: string;
    name: string;
    unit: string;
    location: string;
    required: string;
    on_hand: string;
    reserved: string;
    available: string;
    remaining: string;
    shortage: string;
    orders: {
        order_id: string;
        order: string;
        customer: string;
        required: string;
        products?: {
            name: string;
            quantity: string;
            required: string;
        }[];
    }[];
};
type Batch = {
    id: string;
    product: string;
    sku: string;
    fresh: boolean;
    due_date: string;
    location: string;
    quantity: string;
    allocations: {
        line: string;
        order: string;
        quantity: string;
        completed: boolean;
        review_required: boolean;
    }[];
};
type Balance = {
    photo_url?: string;
    id: string;
    name: string;
    sku: string;
    location: string;
    on_hand: string;
    reserved: string;
    available: string;
    unit: string;
};
export function Warehouse({ user, refresh, onChange, onAddProduct }: {
    user: User;
    refresh: number;
    onChange: () => void;
    onAddProduct: () => void;
}) {
    const [date, setDate] = useState(today());
    const [materials, setMaterials] = useState<Material[]>([]);
    const [batches, setBatches] = useState<Batch[]>([]);
    const [balances, setBalances] = useState<Balance[]>([]);
    const [items, setItems] = useState<Item[]>([]);
    const [locations, setLocations] = useState<Location[]>([]);
    const [item, setItem] = useState('');
    const [location, setLocation] = useState('');
    const [delta, setDelta] = useState('');
    const [reason, setReason] = useState('');
    const [key, setKey] = useState(crypto.randomUUID());
    const [lots, setLots] = useState<Record<string, string>>({});
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState('');
    const [addingWarehouse, setAddingWarehouse] = useState(false);
    const [warehouseName, setWarehouseName] = useState('');
    useEffect(() => {
        Promise.all([api<{
                materials: Material[];
            }>(`/api/v1/warehouse/?date=${date}`), api<Batch[]>('/api/v1/production/'), all<Balance>('/api/v1/inventory/'), all<Item>('/api/v1/items/'), all<Location>('/api/v1/locations/')])
            .then(([requirements, production, stock, items, locations]) => { setMaterials(requirements.materials); setBatches(production); setBalances(stock); setItems(items); setLocations(locations); })
            .catch(error => setError(error.message));
    }, [date, refresh]);
    async function perform(action: () => Promise<unknown>) { setBusy(true); setError(''); setNotice(''); try {
        await action();
        setNotice('Saved. The requirements and stock balances have been refreshed.');
        onChange();
    }
    catch (error) {
        setError((error as Error).message);
    }
    finally {
        setBusy(false);
    } }
    return <section><h1>{t("Warehouse & production")}</h1><p>{t("Shared materials are combined across the selected delivery day, with reservations accounted for.")}</p>
    {can(user, 'Warehouse') && <div className="toolbar"><button onClick={onAddProduct}>＋ {t('Add product')}</button><button className="secondary" onClick={() => setAddingWarehouse(value => !value)}>＋ {t('Add new warehouse')}</button><button className="secondary" onClick={() => document.getElementById('receive-stock')?.scrollIntoView({ behavior: 'smooth' })}>{t('Receive stock')}</button></div>}
    {addingWarehouse && can(user, 'Warehouse') && <form className="panel form" onSubmit={event => { event.preventDefault(); void perform(async () => { const created = await api<Location>('/api/v1/locations/', { name: warehouseName, active: true }); setLocation(created.id); setWarehouseName(''); setAddingWarehouse(false); }); }}><h2>{t('Add new warehouse')}</h2><label>{t('Warehouse name')}<input required maxLength={160} value={warehouseName} onChange={event => setWarehouseName(event.target.value)}/></label><button disabled={busy}>{t('Save warehouse')}</button></form>}
    {error && <p className="error" role="alert">{t(error)}</p>}{notice && <p className="success" role="status">{t(notice)}</p>}
    <div className="toolbar preparation-toolbar"><label>{t("Delivery day")}<input type="date" required value={date} onChange={event => setDate(event.target.value)}/></label><a className="button secondary" target="_blank" rel="noreferrer" href={`/api/v1/documents/preparation/?date=${date}`}>{t("Print preparation for this date")}</a></div>
    <div className="table-wrap panel"><table><caption>{t("Combined preparation requirements")}</caption><thead><tr>{['Material / product', 'Warehouse', 'Required', 'On hand', 'Reserved', 'Available', 'Remaining', 'Shortage'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead><tbody>{materials.map(row => <tr key={row.item_id + row.location}><td><div className="product-cell"><ProductPhoto src={row.photo_url} name={row.name}/><div><strong>{row.name}</strong><small>{row.sku} · {row.unit}</small></div></div><details><summary>{t("Contributing orders")}</summary>{row.orders.map(order => <div key={order.order_id}><p>{order.order} · {order.customer} · {order.required} {row.unit}</p>{order.products?.map((product, index) => <small key={index}>{product.name} × {product.quantity} → {product.required} {row.unit}</small>)}</div>)}</details></td><td>{row.location}</td><td>{quantity(row.required)}</td><td>{quantity(row.on_hand)}</td><td>{quantity(row.reserved)}</td><td>{quantity(row.available)}</td><td>{quantity(row.remaining)}</td><td className={Number(row.shortage) > 0 ? 'shortage' : ''}>{quantity(row.shortage)}</td></tr>)}</tbody></table>{materials.length === 0 && <p>{t("No unissued requirements for this date.")}</p>}</div>
    <h2>{t("Production batches")}</h2><div className="cards">{batches.map(batch => <article className="panel" key={batch.id}><span className="badge">{batch.fresh ? 'Fresh production' : 'Production'}</span><div className="product-cell"><ProductPhoto src={items.find(item => item.sku === batch.sku)?.photo_url} name={batch.product}/><h3>{batch.product}</h3></div><p>{batch.due_date} · {batch.location} · {quantity(batch.quantity)}{' '}{t("units")}</p>{batch.allocations.map(entry => <div className="allocation" key={entry.line}><p>{entry.order} · {quantity(entry.quantity)} {entry.completed ? t('Complete') : t('Awaiting production')}</p>{!entry.completed && can(user, 'Warehouse') && <form className="field-row" onSubmit={event => { event.preventDefault(); void perform(() => api(`/api/v1/production/${entry.line}/complete/`, { lot_reference: lots[entry.line] })); }}><label>{t("Lot / batch reference")}<input required value={lots[entry.line] ?? ''} onChange={event => setLots(values => ({ ...values, [entry.line]: event.target.value }))}/></label><button disabled={busy || entry.review_required}>{t("Confirm produced")}</button></form>}</div>)}</article>)}</div>
    <div className="two-columns">{can(user, 'Warehouse') && <form id="receive-stock" className="panel form" onSubmit={event => { event.preventDefault(); void perform(async () => { await api('/api/v1/inventory/movement/', { item, location, delta, reason, command_key: key }); setKey(crypto.randomUUID()); setDelta(''); setReason(''); }); }}><h2>{t("Receive or adjust stock")}</h2><label>{t("Item")}<select required value={item} onChange={event => setItem(event.target.value)}><option value="">{t("Select item")}</option>{items.map(row => <option key={row.id} value={row.id}>{row.sku} · {row.name}</option>)}</select></label><label>{t("Warehouse")}<select required value={location} onChange={event => setLocation(event.target.value)}><option value="">{t("Select warehouse")}</option>{locations.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{t("Change in item's base unit")}<input required type="number" step="0.000001" value={delta} onChange={event => setDelta(event.target.value)} placeholder={t("Positive receipt, negative adjustment")}/></label><label>{t("Reason")}<input required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Save stock movement")}</button></form>}
      <section className="panel table-wrap"><h2>{t("Current stock")}</h2><table><thead><tr><th>{t("Item")}</th><th>{t("Warehouse")}</th><th>{t("On hand")}</th><th>{t("Available")}</th></tr></thead><tbody>{balances.map(row => <tr key={row.id}><td><div className="product-cell"><ProductPhoto src={row.photo_url} name={row.name}/><span>{row.name}<small>{row.sku} · {row.unit}</small></span></div></td><td>{locations.find(location => location.id === row.location)?.name}</td><td>{quantity(row.on_hand)}</td><td>{quantity(row.available)}</td></tr>)}</tbody></table></section></div>
  </section>;
}
