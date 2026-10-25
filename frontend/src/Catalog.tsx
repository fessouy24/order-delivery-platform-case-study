import { t } from './i18n';
import { useEffect, useState } from 'react';
import { all, api, type Item, type Location } from './api';
import { ProductPhoto } from './Visuals';
type Recipe = {
    id: string;
    product: string;
    version: number;
    output_yield: string;
    active: boolean;
    components: {
        material: string;
        quantity: string;
    }[];
};
const emptyItem = { sku: '', name: '', product_name: '', variant_name: '', kind: 'FINISHED_GOOD', unit: 'unit', manufactured: false, fresh: false, lead_workdays: 0, price: '0.00', active: true };
export function Catalog({ refresh, onChange }: {
    refresh: number;
    onChange: () => void;
}) {
    const [items, setItems] = useState<Item[]>([]);
    const [recipes, setRecipes] = useState<Recipe[]>([]);
    const [item, setItem] = useState(emptyItem);
    const [editing, setEditing] = useState('');
    const [product, setProduct] = useState('');
    const [version, setVersion] = useState(1);
    const [output, setOutput] = useState('1');
    const [components, setComponents] = useState([{ material: '', quantity: '1' }]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [locations, setLocations] = useState<Location[]>([]);
    const [openingQuantity, setOpeningQuantity] = useState('0');
    const [openingLocation, setOpeningLocation] = useState('');
    const [openingReason, setOpeningReason] = useState('');
    const [stockKey, setStockKey] = useState(crypto.randomUUID());
    const [notice, setNotice] = useState('');
    useEffect(() => { void all<Location>('/api/v1/locations/').then(setLocations).catch(error => setError(error.message)); }, [refresh]);
    useEffect(() => { Promise.all([all<Item>('/api/v1/items/'), all<Recipe>('/api/v1/recipes/')]).then(([items, recipes]) => { setItems(items); setRecipes(recipes); }).catch(error => setError(error.message)); }, [refresh]);
    async function save(action: () => Promise<unknown>) { setBusy(true); setError(''); try {
        await action();
        onChange();
    }
    catch (error) {
        setError((error as Error).message);
    }
    finally {
        setBusy(false);
    } }
    async function uploadPhoto(id: string, file: File) {
        await save(async () => {
            const csrf = await api<{
                csrf_token: string;
            }>('/api/v1/auth/csrf/');
            const body = new FormData();
            body.append('photo', file);
            const response = await fetch(`/api/v1/items/${id}/photo/`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf.csrf_token }, body });
            if (!response.ok) {
                const result = await response.json();
                throw new Error(typeof result === 'object' ? Object.values(result).flat().join(' ') : String(result));
            }
        });
    }
    return <section><h1>{t("Products & recipes")}</h1><p>{t("Use one SKU and one base unit per item. Recipe versions preserve the requirements of existing orders.")}</p>{error && <p className="error" role="alert">{t(error)}</p>}
    {notice && <p className="success" role="status">{t(notice)}</p>}
    <div className="two-columns"><form className="panel form" onSubmit={event => { event.preventDefault(); setNotice(''); void save(async () => { const payload = { ...item, ...(!editing && Number(openingQuantity) > 0 ? { opening_stock: { location: openingLocation, quantity: openingQuantity, reason: openingReason, command_key: stockKey } } : {}) }; await api(`/api/v1/items/${editing ? editing + '/' : ''}`, payload, editing ? 'PATCH' : 'POST'); setNotice(editing ? 'Product saved.' : 'Product and opening stock saved.'); setEditing(''); setItem(emptyItem); setOpeningQuantity('0'); setOpeningReason(''); setStockKey(crypto.randomUUID()); }); }}>
      <h2>{t(editing ? 'Edit item' : 'Add product')}</h2><div className="field-row"><label>{t("SKU")}<input required value={item.sku} onChange={event => setItem({ ...item, sku: event.target.value })}/></label><label>{t("Display name")}<input required value={item.name} onChange={event => setItem({ ...item, name: event.target.value })}/></label></div>
      <label>{t("Type")}<select value={item.kind} onChange={event => setItem({ ...item, kind: event.target.value, manufactured: false, fresh: false })}><option value="FINISHED_GOOD">{t("Finished product")}</option><option value="RAW_MATERIAL">{t("Raw material")}</option></select></label>
      <div className="field-row"><label>{t("Product name")}<input value={item.product_name} onChange={event => setItem({ ...item, product_name: event.target.value })}/></label><label>{t("Variant name")}<input value={item.variant_name} onChange={event => setItem({ ...item, variant_name: event.target.value })}/></label></div>
      <div className="field-row"><label>{t("Base unit")}<input required value={item.unit} onChange={event => setItem({ ...item, unit: event.target.value })} placeholder={t("kg, litre, unit")}/></label><label>{t("Unit price")}<input required type="number" min="0" step="0.01" value={item.price} onChange={event => setItem({ ...item, price: event.target.value })}/></label></div>
      {item.kind === 'FINISHED_GOOD' && <><label className="checkbox"><input type="checkbox" checked={item.manufactured} onChange={event => setItem({ ...item, manufactured: event.target.checked, fresh: false })}/>{t("Manufactured from a recipe")}</label>{item.manufactured && <><label className="checkbox"><input type="checkbox" checked={item.fresh} onChange={event => setItem({ ...item, fresh: event.target.checked })}/>{t("Fresh production")}</label><label>{t("Lead time in working days")}<input type="number" required min="0" max="365" value={item.lead_workdays} onChange={event => setItem({ ...item, lead_workdays: Number(event.target.value) })}/></label></>}</>}
      {!editing && <fieldset><legend>{t('Opening stock')}</legend><label>{t('Quantity currently in warehouse')}<input required type="number" min="0" step="0.000001" value={openingQuantity} onChange={event => setOpeningQuantity(event.target.value)}/></label>{Number(openingQuantity) > 0 && <><label>{t('Warehouse')}<select required value={openingLocation} onChange={event => setOpeningLocation(event.target.value)}><option value="">{t('Select warehouse')}</option>{locations.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{t('Reason')}<input required maxLength={250} value={openingReason} onChange={event => setOpeningReason(event.target.value)}/></label></>}<small>{t('For existing products, use Receive stock on the Warehouse page.')}</small></fieldset>}
      <label className="checkbox"><input type="checkbox" checked={item.active} onChange={event => setItem({ ...item, active: event.target.checked })}/>{t("Active")}</label><div className="actions"><button disabled={busy}>{t("Save item")}</button>{editing && <button type="button" className="secondary" onClick={() => { setEditing(''); setItem(emptyItem); }}>{t("Cancel editing")}</button>}</div>
    </form><form className="panel form" onSubmit={event => { event.preventDefault(); void save(async () => { await api('/api/v1/recipes/', { product, version, output_yield: output, active: true, components }); setVersion(version + 1); }); }}>
      <h2>{t("Publish a recipe version")}</h2><label>{t("Manufactured product")}<select required value={product} onChange={event => { setProduct(event.target.value); setVersion(Math.max(0, ...recipes.filter(recipe => recipe.product === event.target.value).map(recipe => recipe.version)) + 1); }}><option value="">{t("Select product")}</option>{items.filter(item => item.manufactured).map(item => <option key={item.id} value={item.id}>{item.sku} · {item.name}</option>)}</select></label>
      <div className="field-row"><label>{t("Version")}<input type="number" required min="1" value={version} onChange={event => setVersion(Number(event.target.value))}/></label><label>{t("Output yield in product units")}<input type="number" required min="0.000001" step="0.000001" value={output} onChange={event => setOutput(event.target.value)}/></label></div>
      {components.map((component, index) => <div className="field-row" key={index}><label>{t("Raw material")}<select required value={component.material} onChange={event => setComponents(rows => rows.map((row, n) => n === index ? { ...row, material: event.target.value } : row))}><option value="">{t("Select material")}</option>{items.filter(item => item.kind === 'RAW_MATERIAL').map(item => <option key={item.id} value={item.id}>{item.sku} · {item.unit}</option>)}</select></label><label>{t("Quantity per recipe yield")}<input type="number" required min="0.000001" step="0.000001" value={component.quantity} onChange={event => setComponents(rows => rows.map((row, n) => n === index ? { ...row, quantity: event.target.value } : row))}/></label>{components.length > 1 && <button type="button" className="secondary" onClick={() => setComponents(rows => rows.filter((_, n) => n !== index))}>{t("Remove")}</button>}</div>)}
      <button type="button" className="secondary" onClick={() => setComponents(rows => [...rows, { material: '', quantity: '1' }])}>{t("Add material")}</button><button disabled={busy}>{t("Publish recipe")}</button>
      <h3>{t("Current recipe versions")}</h3>{recipes.filter(recipe => recipe.active).map(recipe => <p key={recipe.id}>{items.find(item => item.id === recipe.product)?.name}{' '}{t("\u00B7 v")}{' '}{recipe.version}{' '}{t("\u00B7 yield")}{' '}{recipe.output_yield}</p>)}
    </form></div><div className="panel table-wrap"><table><caption>{t("Item catalog")}</caption><thead><tr><th>{t("Photo")}</th><th>{t("SKU")}</th><th>{t("Name")}</th><th>{t("Unit")}</th><th>{t("Production")}</th><th>{t("Status")}</th><th>{t("Action")}</th></tr></thead><tbody>{items.map(row => <tr key={row.id}><td><ProductPhoto src={row.photo_url} name={row.name}/><label className="photo-upload">{t("Upload photo")}<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={event => { const input = event.currentTarget; const file = input.files?.[0]; if (file)
        void uploadPhoto(row.id, file).finally(() => { input.value = ''; }); }}/></label></td><td>{row.sku}</td><td>{row.name}</td><td>{row.unit}</td><td>{t(row.fresh ? 'Fresh' : row.manufactured ? 'Manufactured' : 'Stocked')}</td><td>{t(row.active ? 'Active' : 'Inactive')}</td><td><button className="secondary" onClick={() => { setEditing(row.id); setItem({ ...emptyItem, ...row }); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>{t("Edit")}</button></td></tr>)}</tbody></table></div>
  </section>;
}
