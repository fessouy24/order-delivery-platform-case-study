import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, type Item, type Order } from './api';
export function Amend({ order, busy, save }: {
    order: Order;
    busy: boolean;
    save: (data: object) => Promise<void>;
}) {
    const [items, setItems] = useState<Item[]>([]);
    const [error, setError] = useState('');
    const [lines, setLines] = useState<{
        item: string;
        quantity: string;
        unit_price: string;
    }[]>([]);
    const [reason, setReason] = useState('');
    const [notes, setNotes] = useState('');
    useEffect(() => { setLines(order.lines.map(line => ({ item: line.item, quantity: line.quantity, unit_price: line.unit_price ?? '0' }))); setNotes(order.notes); setReason(''); }, [order.id, order.version]);
    useEffect(() => { all<Item>('/api/v1/items/').then(items => setItems(items.filter(item => item.active && item.kind === 'FINISHED_GOOD'))).catch(error => setError(error.message)); }, []);
    return <details className="panel"><summary>{t("Amend products and quantities")}</summary><form className="form" onSubmit={event => { event.preventDefault(); void save({ lines, notes, reason }); }}><p>{t("Available before production, dispatch or a pending Odoo creation attempt. Original lines remain in the audit record.")}</p>{error && <p className="error" role="alert">{t(error)}</p>}
    {lines.map((line, index) => <div className="field-row" key={index}><label>{t("Product")}<select required value={line.item} onChange={event => { const item = items.find(item => item.id === event.target.value); setLines(rows => rows.map((row, n) => n === index ? { ...row, item: event.target.value, unit_price: item?.price ?? row.unit_price } : row)); }}><option value="">{t("Select product")}</option>{items.map(item => <option key={item.id} value={item.id}>{item.sku} · {item.name}</option>)}</select></label><label>{t("Quantity")}<input type="number" required min="0.000001" step="0.000001" value={line.quantity} onChange={event => setLines(rows => rows.map((row, n) => n === index ? { ...row, quantity: event.target.value } : row))}/></label><label>{t("Unit price")}<input type="number" required min="0" step="0.01" value={line.unit_price} onChange={event => setLines(rows => rows.map((row, n) => n === index ? { ...row, unit_price: event.target.value } : row))}/></label>{lines.length > 1 && <button type="button" className="secondary" onClick={() => setLines(rows => rows.filter((_, n) => n !== index))}>{t("Remove")}</button>}</div>)}
    <button type="button" className="secondary" onClick={() => setLines(rows => [...rows, { item: '', quantity: '1', unit_price: '0' }])}>{t("Add product")}</button><label>{t("Notes")}<textarea value={notes} onChange={event => setNotes(event.target.value)}/></label><label>{t("Amendment reason")}<input required value={reason} onChange={event => setReason(event.target.value)}/></label><button disabled={busy}>{t("Save amendment and recalculate stock")}</button>
  </form></details>;
}
