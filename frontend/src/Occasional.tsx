import {useEffect,useState} from 'react';
import {api} from './api';
import {t} from './i18n';
import {Sales} from './Sales';
export function Occasional({refresh,onChange}:{refresh:number;onChange:()=>void}) {
  const [rows,setRows]=useState<{id:string;customer_name:string;channel:string;reference:string}[]>([]),[couriers,setCouriers]=useState<{id:string;username:string}[]>([]),[selected,setSelected]=useState(''),[date,setDate]=useState(''),[courier,setCourier]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{void Promise.all([api<typeof rows>('/api/v1/occasional/'),api<typeof couriers>('/api/v1/couriers/')]).then(([r,c])=>{setRows(r);setCouriers(c);}).catch(e=>setError(e.message));},[refresh]);
  return <><section className="panel"><h1>{t('Pending occasional orders')}</h1>{error&&<p className="error" role="alert">{error}</p>}{rows.map(row=><article key={row.id}><strong>{row.customer_name}</strong><p>{t(row.channel)} · {row.reference}</p><button onClick={()=>{setSelected(row.id);setDate('');setCourier('');}}>{t('Schedule delivery')}</button></article>)}{!rows.length&&<p>{t('No pending orders.')}</p>}
    {selected&&<form className="form" onSubmit={e=>{e.preventDefault();setBusy(true);void api(`/api/v1/occasional/${selected}/schedule/`,{delivery_date:date,courier}).then(()=>{setSelected('');onChange();}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}><label>{t('Delivery day')}<input required type="date" value={date} onChange={e=>setDate(e.target.value)}/></label><label>{t('Courier')}<select required value={courier} onChange={e=>setCourier(e.target.value)}><option value="">—</option>{couriers.map(c=><option key={c.id} value={c.id}>{c.username}</option>)}</select></label><button disabled={busy}>{t('Schedule delivery')}</button><button type="button" onClick={()=>setSelected('')}>{t('Cancel')}</button></form>}
  </section><Sales refresh={refresh} onChange={onChange} occasional/></>;
}
