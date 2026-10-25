import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, api, today, type Customer, type Item, type Location, type Order } from './api';
export function Sales({ refresh, onChange, occasional = false }: {
    refresh: number; occasional?: boolean;
    onChange: () => void;
}) {
    const [channel, setChannel] = useState('PHONE');
    const [reference, setReference] = useState('');
    const [customers, setCustomers] = useState<Customer[]>([]);
    const [items, setItems] = useState<Item[]>([]);
    const [locations, setLocations] = useState<Location[]>([]);
    const [customer, setCustomer] = useState('');
    const [address, setAddress] = useState('');
    const [location, setLocation] = useState('');
    const [date, setDate] = useState(today());
    const [notes, setNotes] = useState('');
    const [payment, setPayment] = useState('COD');
    const [currency, setCurrency] = useState('EGP');
    const [paymentStatus, setPaymentStatus] = useState('UNPAID');
    const [collection, setCollection] = useState('');
    const [earliest, setEarliest] = useState('');
    const [latest, setLatest] = useState('');
    const [lines, setLines] = useState<{item:string;quantity:string;unit_price?:string}[]>([{ item: '', quantity: '1' }]);
    const [key, setKey] = useState(crypto.randomUUID());
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    const [busy, setBusy] = useState(false);
    const [customerName, setCustomerName] = useState('');
    const [phone, setPhone] = useState('');
    const [branch, setBranch] = useState('');
    const [fullAddress, setFullAddress] = useState('');
    const [originalArea, setOriginalArea] = useState('');
    useEffect(() => {
        Promise.all([all<Customer>('/api/v1/customers/'), all<Item>('/api/v1/items/'), all<Location>('/api/v1/locations/')])
            .then(([customers, items, locations]) => { setCustomers(customers); setItems(items.filter(item => item.active && item.kind === 'FINISHED_GOOD')); setLocations(locations); })
            .catch(error => setError(error.message));
    }, [refresh]);
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        setError('');
        setSuccess('');
        setBusy(true);
        try {
            const order = await api<Order>(occasional ? '/api/v1/occasional/' : '/api/v1/orders/', { ...(occasional ? { channel, reference } : {}), command_key: key, customer, address, location, delivery_date: date,
                notes, currency, payment_method: payment, payment_status: paymentStatus, ...(collection ? { expected_collection: collection } : {}), earliest_time: earliest || null,
                latest_time: latest || null, lines });
            setSuccess(occasional ? t('Saved pending delivery reservation.') : `Created ${order.number}. Scheduled for ${order.delivery_date}.`);
            setKey(crypto.randomUUID());
            onChange();
        }
        catch (error) {
            setError((error as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    const current = customers.find(row => row.id === customer);
    return <section><h1>{t(occasional ? "Occasional orders" : "Sales order entry")}</h1><p>{t("A DS is a delivery branch of the selected company.")}</p>
    {error && <p className="error" role="alert">{t(error)}</p>}{success && <p className="success" role="status">{success}</p>}
    <div className="two-columns"><form className="panel form" onSubmit={submit}>
      <h2>{t(occasional ? "Phone or partner order" : "Create a B2B order")}</h2>
      {occasional && <><label>{t("Order source")}<select value={channel} onChange={event => setChannel(event.target.value)}><option value="PHONE">{t("Phone")}</option><option value="PARTNER">{t("Partner company")}</option><option value="OTHER">{t("Other")}</option></select></label><label>{t("Reference or contact person")}<input value={reference} onChange={event => setReference(event.target.value)}/></label><p>{t("Saved pending delivery reservation.")}</p></>}
      <label>{t("Company or customer")}<select required value={customer} onChange={event => { setCustomer(event.target.value); setAddress(''); }}><option value="">{t("Select customer")}</option>{customers.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <label>{t("DS or delivery branch")}<select required value={address} onChange={event => setAddress(event.target.value)}><option value="">{t("Select branch")}</option>{current?.addresses.map(row => <option key={row.id} value={row.id}>{row.branch_code} · {row.full_address}</option>)}</select></label>
      <label>{t("Preparation warehouse")}<select required value={location} onChange={event => setLocation(event.target.value)}><option value="">{t("Select warehouse")}</option>{locations.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      {lines.map((line, index) => <div className="field-row" key={index}><label>{t("Product")}<select required value={line.item} onChange={event => setLines(rows => rows.map((row, n) => n === index ? { ...row, item: event.target.value } : row))}><option value="">{t("Select product")}</option>{items.map(item => <option key={item.id} value={item.id}>{item.sku} · {item.name}{item.fresh ? ' · Fresh production' : ''}</option>)}</select></label>
        <label>{t("Quantity")}<input required type="number" min="0.000001" step="0.000001" value={line.quantity} onChange={event => setLines(rows => rows.map((row, n) => n === index ? { ...row, quantity: event.target.value } : row))}/></label>
        {occasional && <label>{t('Agreed unit price')}<input type="number" min="0" step="0.01" value={line.unit_price ?? ''} placeholder={t('Default product price')} onChange={event => setLines(rows => rows.map((row,n) => n === index ? {...row,unit_price:event.target.value || undefined} : row))}/></label>}
        {lines.length > 1 && <button type="button" className="secondary" onClick={() => setLines(rows => rows.filter((_, n) => n !== index))}>{t("Remove line")}</button>}</div>)}
      <button className="secondary" type="button" onClick={() => setLines(rows => [...rows, { item: '', quantity: '1' }])}>{t("Add another product")}</button>
      {!occasional && <div className="field-row"><label>{t("Requested delivery date")}<input type="date" required value={date} onChange={event => setDate(event.target.value)}/></label><label>{t("After")}<input type="time" value={earliest} onChange={event => setEarliest(event.target.value)}/></label><label>{t("Before")}<input type="time" value={latest} onChange={event => setLatest(event.target.value)}/></label></div>}
      <div className="field-row"><label>{t("Payment method")}<select value={payment} onChange={event => setPayment(event.target.value)}><option value="COD">{t("Cash on delivery")}</option><option value="BANK_TRANSFER">{t("Bank transfer")}</option><option value="ONLINE">{t("Online payment")}</option></select></label><label>{t("Currency")}<input required minLength={3} maxLength={3} value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())}/></label></div>
      <label>{t("Delivery notes")}<textarea value={notes} onChange={event => setNotes(event.target.value)}/></label>
      <div className="field-row"><label>{t("Payment status")}<select value={paymentStatus} onChange={event => { setPaymentStatus(event.target.value); setCollection(event.target.value === 'PAID' ? '0' : ''); }}><option value="UNPAID">{t("Unpaid")}</option><option value="PAID">{t("Fully paid")}</option><option value="PARTIAL">{t("Partially paid")}</option></select></label><label>{t("Remaining amount to collect")}<input type="number" required={paymentStatus === 'PARTIAL'} min="0" step="0.01" value={collection} onChange={event => setCollection(event.target.value)} placeholder={t("Defaults to total for unpaid orders")}/></label></div>
      <p className="muted">{t("The company calendar and fresh-production lead time determine the final scheduled date.")}</p>
      <button disabled={busy}>{busy ? t('Creating…') : t(occasional ? 'Save pending order' : 'Create order')}</button>
    </form>
    <div className="form">
      <form className="panel form" onSubmit={event => { event.preventDefault(); void api('/api/v1/customers/', { name: customerName, phone, company: true }).then(() => { setCustomerName(''); onChange(); }).catch(error => setError(error.message)); }}><h2>{t("Add a company")}</h2><label>{t("Company name")}<input required value={customerName} onChange={event => setCustomerName(event.target.value)}/></label><label>{t("Phone in international format")}<input type="tel" placeholder={t("+20\u2026")} value={phone} onChange={event => setPhone(event.target.value)}/></label><button>{t("Add company")}</button></form>
      <form className="panel form" onSubmit={event => { event.preventDefault(); void api('/api/v1/addresses/', { customer, branch_code: branch, full_address: fullAddress, original_area: originalArea }).then(() => { setBranch(''); setFullAddress(''); onChange(); }).catch(error => setError(error.message)); }}><h2>{t("Add a DS branch")}</h2><p>{t("For")}{' '}{current?.name ?? 'the company selected in the order form'}{t(".")}</p><label>{t("Branch code")}<input required value={branch} onChange={event => setBranch(event.target.value)} placeholder={t("DS 30")}/></label><label>{t("Full address")}<textarea required value={fullAddress} onChange={event => setFullAddress(event.target.value)}/></label><label>{t("Area as entered")}<input value={originalArea} onChange={event => setOriginalArea(event.target.value)}/></label><button disabled={!customer}>{t("Add branch")}</button></form>
    </div></div>
  </section>;
}
