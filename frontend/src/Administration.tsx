import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, api, can, type User, type Customer, type Item, type Location } from './api';
type Area = {
    id: string;
    name: string;
    region: string;
};
type Alias = {
    id: string;
    area: string;
    original_text: string;
};
type Staff = {
    id: string;
    username: string;
    first_name: string;
    last_name: string;
    email: string;
    is_active: boolean;
    roles: string[];
};
type Policy = {
    id: string;
    weekdays: number[];
    holidays: string[];
    extra_workdays: string[];
    shopify_due_tag: string;
    communication_template: string;
};
type Mapping = {
    id: string;
    entity_type: string;
    entity_id: string;
    external_id: string;
};
type Integrations = {
    shopify_configured: boolean;
    odoo_configured: boolean;
    jobs: {
        id: string;
        order_id: string;
        order__number: string;
        status: string;
        attempts: number;
        error: string;
        external_number: string;
    }[];
    inbox_errors: {
        id: string;
        topic: string;
        error: string;
    }[];
};
type Audit = {
    id: string;
    actor__username: string | null;
    action: string;
    entity_type: string;
    created_at: string;
};
const roleNames = ['Admin', 'Management', 'Sales', 'Supply Chain', 'Warehouse', 'Courier', 'Finance', 'Production'];
const emptyStaff = { username: '', first_name: '', last_name: '', email: '', is_active: true, roles: [] as string[], password: '' };
const defaultMessage = 'Hello {customer}, your OPSDEMO order {order} is scheduled for delivery today.';
export function Administration({ user, refresh, onChange }: {
    user: User;
    refresh: number;
    onChange: () => void;
}) {
    const admin = can(user, 'Admin');
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [busy, setBusy] = useState(false);
    const [areas, setAreas] = useState<Area[]>([]);
    const [aliases, setAliases] = useState<Alias[]>([]);
    const [areaName, setAreaName] = useState('');
    const [region, setRegion] = useState('');
    const [aliasArea, setAliasArea] = useState('');
    const [aliasText, setAliasText] = useState('');
    const [staff, setStaff] = useState<Staff[]>([]);
    const [editing, setEditing] = useState('');
    const [account, setAccount] = useState(emptyStaff);
    const [policy, setPolicy] = useState<Policy | null>(null);
    const [weekdays, setWeekdays] = useState([5, 6, 0, 1, 2, 3]);
    const [holidays, setHolidays] = useState('');
    const [extraDays, setExtraDays] = useState('');
    const [tag, setTag] = useState('delivery-today');
    const [message, setMessage] = useState(defaultMessage);
    const [locations, setLocations] = useState<Location[]>([]);
    const [locationName, setLocationName] = useState('');
    const [integrations, setIntegrations] = useState<Integrations | null>(null);
    const [mappings, setMappings] = useState<Mapping[]>([]);
    const [customers, setCustomers] = useState<Customer[]>([]);
    const [items, setItems] = useState<Item[]>([]);
    const [kind, setKind] = useState('customer');
    const [entity, setEntity] = useState('');
    const [externalId, setExternalId] = useState('');
    const [audit, setAudit] = useState<Audit[]>([]);
    useEffect(() => {
        const requests: Promise<unknown>[] = [all<Area>('/api/v1/areas/').then(setAreas), all<Alias>('/api/v1/area-aliases/').then(setAliases), api<Integrations>('/api/v1/integrations/').then(setIntegrations), all<Mapping>('/api/v1/mappings/').then(setMappings), all<Policy>('/api/v1/policies/').then(rows => { const value = rows[0]; setPolicy(value ?? null); if (value) {
                setWeekdays(value.weekdays);
                setHolidays(value.holidays.join('\n'));
                setExtraDays(value.extra_workdays.join('\n'));
                setTag(value.shopify_due_tag);
                setMessage(value.communication_template);
            } })];
        if (admin)
            requests.push(all<Staff>('/api/v1/users/').then(setStaff), all<Location>('/api/v1/locations/').then(setLocations), all<Customer>('/api/v1/customers/').then(setCustomers), all<Item>('/api/v1/items/').then(setItems), api<Audit[]>('/api/v1/audit/').then(setAudit));
        void Promise.all(requests).catch(error => setError(error.message));
    }, [admin, refresh]);
    async function save(action: () => Promise<unknown>) { setBusy(true); setError(''); setNotice(''); try {
        await action();
        setNotice('Saved.');
        onChange();
    }
    catch (error) {
        setError((error as Error).message);
    }
    finally {
        setBusy(false);
    } }
    const entities = kind === 'customer' ? customers.map(row => ({ id: row.id, name: row.name })) : (kind === 'item' || kind === 'item_uom') ? items.map(row => ({ id: row.id, name: `${row.sku} · ${row.name}` })) : customers.flatMap(row => row.addresses.map(address => ({ id: address.id, name: `${row.name} · ${address.branch_code}` })));
    const dates = (text: string) => text.split(/[\s,]+/).filter(Boolean);
    return <section><h1>{admin ? 'Administration' : 'Operations settings'}</h1><p>{t("Maintain delivery areas and monitor external synchronization.")}</p>{error && <p className="error" role="alert">{t(error)}</p>}{notice && <p className="success" role="status">{notice}</p>}
    <div className="two-columns"><form className="panel form" onSubmit={event => { event.preventDefault(); void save(async () => { await api('/api/v1/areas/', { name: areaName, region }); setAreaName(''); }); }}><h2>{t("Delivery areas")}</h2><label>{t("Area name")}<input required value={areaName} onChange={event => setAreaName(event.target.value)}/></label><label>{t("Region")}<input value={region} onChange={event => setRegion(event.target.value)}/></label><button disabled={busy}>{t("Add area")}</button>{areas.map(area => <p key={area.id}>{area.name} · {area.region}</p>)}</form>
      <form className="panel form" onSubmit={event => { event.preventDefault(); void save(async () => { await api('/api/v1/area-aliases/', { area: aliasArea, original_text: aliasText }); setAliasText(''); }); }}><h2>{t("Area spellings & aliases")}</h2><label>{t("Standard area")}<select required value={aliasArea} onChange={event => setAliasArea(event.target.value)}><option value="">{t("Select area")}</option>{areas.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{t("English or Arabic spelling")}<input required dir="auto" value={aliasText} onChange={event => setAliasText(event.target.value)}/></label><button disabled={busy}>{t("Add alias")}</button><details><summary>{t("Existing mappings (")}{aliases.length}{t(")")}</summary>{aliases.map(alias => <p key={alias.id}>{alias.original_text} → {areas.find(area => area.id === alias.area)?.name}</p>)}</details></form></div>
    {admin && <><div className="two-columns"><form className="panel form" onSubmit={event => { event.preventDefault(); void save(async () => { const { password, ...rest } = account; await api(`/api/v1/users/${editing ? editing + '/' : ''}`, { ...rest, ...(password ? { password } : {}) }, editing ? 'PATCH' : 'POST'); setEditing(''); setAccount(emptyStaff); }); }}><h2>{t(editing ? 'Edit staff account' : 'Create staff account')}</h2><label>{t("Username")}<input required value={account.username} onChange={event => setAccount({ ...account, username: event.target.value })}/></label><div className="field-row"><label>{t("First name")}<input value={account.first_name} onChange={event => setAccount({ ...account, first_name: event.target.value })}/></label><label>{t("Last name")}<input value={account.last_name} onChange={event => setAccount({ ...account, last_name: event.target.value })}/></label></div><label>{t("Email")}<input type="email" value={account.email} onChange={event => setAccount({ ...account, email: event.target.value })}/></label><label>{t(editing ? 'New password (leave blank to retain)' : 'Initial password')}<input type="password" autoComplete="new-password" required={!editing} minLength={12} value={account.password} onChange={event => setAccount({ ...account, password: event.target.value })}/></label><fieldset><legend>{t("Roles")}</legend>{roleNames.map(role => <label className="checkbox" key={role}><input type="checkbox" checked={account.roles.includes(role)} onChange={event => setAccount({ ...account, roles: event.target.checked ? [...account.roles, role] : account.roles.filter(value => value !== role) })}/>{t(role)}</label>)}</fieldset><label className="checkbox"><input type="checkbox" checked={account.is_active} onChange={event => setAccount({ ...account, is_active: event.target.checked })}/>{t("Active account")}</label><button disabled={busy}>{t("Save account")}</button>{editing && <button type="button" className="secondary" onClick={() => { setEditing(''); setAccount(emptyStaff); }}>{t("Cancel editing")}</button>}</form>
      <section className="panel"><h2>{t("Staff directory")}</h2>{staff.map(row => <div className="summary-row" key={row.id}><p>{row.username}<small>{row.roles.join(' · ')} · {row.is_active ? 'Active' : 'Inactive'}</small></p><button className="secondary" onClick={() => { setEditing(row.id); setAccount({ ...row, password: '' }); }}>{t("Edit")}</button></div>)}</section></div>
      <div className="two-columns"><form className="panel form" onSubmit={event => { event.preventDefault(); void save(() => api(`/api/v1/policies/${policy ? policy.id + '/' : ''}`, { name: 'default', weekdays, holidays: dates(holidays), extra_workdays: dates(extraDays), shopify_due_tag: tag, communication_template: message }, policy ? 'PATCH' : 'POST')); }}><h2>{t("Work calendar & communication")}</h2><fieldset><legend>{t("Working days")}</legend>{['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, index) => <label className="checkbox" key={day}><input type="checkbox" checked={weekdays.includes(index)} onChange={event => setWeekdays(event.target.checked ? [...weekdays, index] : weekdays.filter(day => day !== index))}/>{t(day)}</label>)}</fieldset><label>{t("Holidays (YYYY-MM-DD, one per line)")}<textarea value={holidays} onChange={event => setHolidays(event.target.value)}/></label><label>{t("Extra working dates")}<textarea value={extraDays} onChange={event => setExtraDays(event.target.value)}/></label><label>{t("Shopify tag for today's deliveries")}<input required value={tag} onChange={event => setTag(event.target.value)}/></label><label>{t("Approved WhatsApp message")}<textarea required value={message} onChange={event => setMessage(event.target.value)}/></label><small>{t("Available placeholders:")}{' '}{'{customer}'}{' '}{t("and")}{' '}{'{order}'}{t(".")}</small><button disabled={busy}>{t("Save policy")}</button></form>
        <form className="panel form" onSubmit={event => { event.preventDefault(); void save(async () => { await api('/api/v1/locations/', { name: locationName, active: true }); setLocationName(''); }); }}><h2>{t("Warehouses")}</h2><label>{t("Name")}<input required value={locationName} onChange={event => setLocationName(event.target.value)}/></label><button disabled={busy}>{t("Add warehouse")}</button>{locations.map(row => <p key={row.id}>{row.name}</p>)}</form></div>
      <form className="panel form" onSubmit={event => { event.preventDefault(); void save(() => api('/api/v1/mappings/', { provider: 'ODOO', entity_type: kind, entity_id: entity, external_id: externalId })); }}><h2>{t("Odoo record mappings")}</h2><div className="field-row"><label>{t("Entity type")}<select value={kind} onChange={event => { setKind(event.target.value); setEntity(''); }}><option value="customer">{t("Customer / partner")}</option><option value="address">{t("Delivery branch / shipping partner")}</option><option value="item">{t("Product variant")}</option><option value="item_uom">{t("Product unit in Odoo")}</option></select></label><label>{t("Internal record")}<select required value={entity} onChange={event => setEntity(event.target.value)}><option value="">{t("Select record")}</option>{entities.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{t("Odoo record ID")}<input required inputMode="numeric" pattern="[1-9][0-9]*" value={externalId} onChange={event => setExternalId(event.target.value)}/></label></div><button disabled={busy}>{t("Save mapping")}</button><details><summary>{t("Existing mappings (")}{mappings.length}{t(")")}</summary>{mappings.map(row => <p key={row.id}>{row.entity_type} · {row.entity_id} → {row.external_id}</p>)}</details></form></>}
    <section className="panel table-wrap"><h2>{t("Integrations")}</h2><p>{t("Shopify:")}{' '}{integrations?.shopify_configured ? 'Configuration present' : 'Configuration needed'}{' '}{t("\u00B7 Odoo:")}{' '}{integrations?.odoo_configured ? 'Configuration present' : 'Configuration needed'}</p><table><thead><tr><th>{t("Order")}</th><th>{t("Status")}</th><th>{t("Odoo quotation")}</th><th>{t("Attention")}</th><th>{t("Action")}</th></tr></thead><tbody>{integrations?.jobs.map(job => <tr key={job.id}><td>{job.order__number}</td><td>{job.status} · {job.attempts}{' '}{t("attempts")}</td><td>{job.external_number || 'Pending'}</td><td>{job.error}</td><td>{!['SYNCED', 'CLAIMED', 'CREATING', 'CANCELLED'].includes(job.status) && <button disabled={busy} className="secondary" onClick={() => { void save(() => api(`/api/v1/integrations/odoo/${job.order_id}/retry/`, {})); }}>{t("Retry / reconcile")}</button>}</td></tr>)}</tbody></table>{integrations?.inbox_errors.map(row => <div className="error" key={row.id}><p>{row.topic}: {row.error}</p><button disabled={busy} className="secondary" onClick={() => { void save(() => api(`/api/v1/integrations/shopify/${row.id}/retry/`, {})); }}>{t("Retry import after correction")}</button></div>)}</section>
    {admin && <section className="panel table-wrap"><h2>{t("Recent audit trail")}</h2><table><thead><tr><th>{t("Time")}</th><th>{t("Actor")}</th><th>{t("Action")}</th><th>{t("Entity")}</th></tr></thead><tbody>{audit.map(row => <tr key={row.id}><td>{new Date(row.created_at).toLocaleString()}</td><td>{row.actor__username ?? 'System'}</td><td>{row.action}</td><td>{row.entity_type}</td></tr>)}</tbody></table></section>}
  </section>;
}
