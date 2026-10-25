import {CompletionNotice,type Completion} from './FinanceCompletion';
import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {t} from './i18n';

export type CashStatement={balance:Record<string,string>;entries:{id:string;order_id:string;number:string;amount:string;currency:string;delivered_at:string}[]};
export function CashAmounts({amounts}:{amounts:Record<string,string>}){return <>{Object.entries(amounts).length?Object.entries(amounts).map(([currency,amount])=><strong key={currency}>{amount} {currency}{' '}</strong>):<strong>0.00 EGP</strong>}</>;}

export function FinanceCash({courier,name,statement,onChange}:{courier:string;name:string;statement:CashStatement;onChange:()=>void}){
 const [completed,setCompleted]=useState<Completion[]>([]);
 const [currency,setCurrency]=useState('EGP');const [amount,setAmount]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [snapshot,setSnapshot]=useState<{currency:string;amount:string;ids:string[];key:string}|null>(null);const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{setCurrency(Object.keys(statement.balance)[0]??'EGP');setAmount('');setSnapshot(null);setError('');},[courier]);
 const entries=statement.entries.filter(row=>row.currency===currency);
 async function confirm(){if(!snapshot||busy)return;setBusy(true);setError('');try{const result=await api<{completions:Completion[]}>(`/api/v1/departments/finance/couriers/${courier}/complete/`,{currency:snapshot.currency,amount:snapshot.amount,entry_ids:snapshot.ids,command_key:snapshot.key});setCompleted(result.completions);setSnapshot(null);setAmount('');dialog.current?.close();onChange();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 if(completed.length)return <CompletionNotice rows={completed} close={()=>setCompleted([])}/>;
 return <section className="panel"><h2>{t('Cash awaiting handover')} · {name}</h2><p>{t('All delivered website COD orders still unsettled, across all dates. Market orders are excluded.')}</p><CashAmounts amounts={statement.balance}/>
 {!statement.entries.length?<p>{t('No cash awaiting handover.')}</p>:<><label>{t('Currency')}<select value={currency} onChange={e=>{setCurrency(e.target.value);setAmount('');}}>{Object.keys(statement.balance).map(value=><option key={value}>{value}</option>)}</select></label>
 <ul>{entries.map(row=><li key={row.id}>{row.number} · {row.amount} {row.currency}</li>)}</ul>
 <form className="form" onSubmit={e=>{e.preventDefault();setError('');setSnapshot({currency,amount,ids:entries.map(row=>row.id),key:crypto.randomUUID()});dialog.current?.showModal();}}>
 <label>{t('Cash received by finance')} ({currency})<input required type="number" min="0" step="0.01" inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></label><button disabled={busy||!entries.length}>{t('Complete cash handover')}</button></form></>}
 <dialog ref={dialog} className="courier-dialog" onCancel={e=>{if(busy)e.preventDefault();}}><h2>{t('Confirm cash handover')}</h2><p>{name} · {snapshot?.amount} {snapshot?.currency}</p><p>{t('Confirm only after receiving the full cash amount. This clears the listed debt and queues silent Shopify fulfillment for these delivered website orders.')}</p>{error&&<p role="alert" className="error">{error}</p>}<div className="toolbar"><button disabled={busy} onClick={()=>void confirm()}>{t(busy?'Saving…':'Confirm')}</button><button className="secondary" disabled={busy} onClick={()=>{dialog.current?.close();setSnapshot(null);}}>{t('Go back')}</button></div></dialog>
 </section>;
}
