import {useEffect,useState} from 'react';
import {all,api,type Customer} from './api';
import {t} from './i18n';
export function BranchMaps({refresh,onChange}:{refresh:number;onChange:()=>void}) {
 const [customers,setCustomers]=useState<Customer[]>([]),[branch,setBranch]=useState(''),[url,setUrl]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [customer,setCustomer]=useState(''),[code,setCode]=useState(''),[address,setAddress]=useState(''),[newUrl,setNewUrl]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{void all<Customer>('/api/v1/customers/').then(setCustomers).catch(e=>setError(e.message));},[refresh]);
 const addresses=customers.flatMap(c=>c.addresses.map(a=>({...a,label:`${c.name} · ${a.branch_code}`})));
 return <section><h1>{t('Branch locations')}</h1>
 {error&&<p className="error" role="alert">{error}</p>}{message&&<p role="status">{t(message)}</p>}
 <form className="panel form" onSubmit={e=>{e.preventDefault();setBusy(true);setError('');void api(`/api/v1/addresses/${branch}/`,{maps_url:url},'PATCH').then(()=>{setMessage('Location saved');onChange();}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}>
 <label>{t('Delivery branch')}<select required value={branch} onChange={e=>{setBranch(e.target.value);setUrl(addresses.find(a=>a.id===e.target.value)?.maps_url||'');}}><option value="">—</option>{addresses.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select></label>
 <label>{t('Google Maps link')}<input type="url" value={url} placeholder="https://maps.app.goo.gl/…" onChange={e=>setUrl(e.target.value)}/></label><button disabled={busy||!branch}>{t('Save location')}</button></form>
 <form className="panel form" onSubmit={e=>{e.preventDefault();setBusy(true);setError('');setMessage('');void api('/api/v1/addresses/',{customer,branch_code:code,full_address:address,maps_url:newUrl}).then(()=>{setCode('');setAddress('');setNewUrl('');setMessage('Branch added');onChange();}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}>
 <h2>{t('Add a DS branch')}</h2><p>{t('New stores can be added at any time. Select the correct market first.')}</p>
 <label>{t('Customer')}<select required value={customer} onChange={e=>setCustomer(e.target.value)}><option value="">—</option>{customers.filter(c=>c.company).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
 <label>{t('Branch code')}<input required value={code} onChange={e=>setCode(e.target.value)} placeholder="DS76"/></label>
 <label>{t('Full address')}<textarea required value={address} onChange={e=>setAddress(e.target.value)}/></label>
 <label>{t('Google Maps link')}<input type="url" value={newUrl} onChange={e=>setNewUrl(e.target.value)} placeholder="https://maps.app.goo.gl/…"/></label>
 <button disabled={busy||!customer}>{t('Add branch')}</button></form></section>;
}
