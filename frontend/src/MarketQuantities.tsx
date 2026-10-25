import { useState } from 'react';
import { can, type Order, type User } from './api';
import { t } from './i18n';
import { ProductPhoto, quantity } from './Visuals';

export function MarketQuantities({order,user,busy,save}:{order:Order;user:User;busy:boolean;save:(action:string,data:object)=>Promise<void>}) {
  const received=order.stock_issued;
  const [values,setValues]=useState<Record<string,string>>(() => Object.fromEntries(order.lines.map(line=>[line.id,(received?line.received_quantity:line.sent_quantity) ?? (received?line.sent_quantity:line.quantity) ?? '0'])));
  const [reason,setReason]=useState('');
  const editable=received?can(user,'Finance'):can(user,'Warehouse');
  return <section className="panel"><h2>{t('Market item reconciliation')}</h2>
    <p>{t('Requested, sent and received quantities are recorded separately. Differences do not change prices or return stock automatically.')}</p>
    <form className="form" onSubmit={event=>{event.preventDefault();void save(received?'market-received':'market-sent',{reason,lines:order.lines.map(line=>({line:line.id,quantity:values[line.id]}))});}}>
    <div className="table-wrap"><table><thead><tr>{['Product','Ordered','Sent from warehouse','Received by customer',...(editable?[received?'Received quantity':'Quantity to send']:[])].map(title=><th key={title}>{t(title)}</th>)}</tr></thead>
    <tbody>{order.lines.map(line=><tr key={line.id}><td><div className="product-cell"><ProductPhoto src={line.photo_url} name={line.name}/><span>{line.name}<small>{line.sku}</small></span></div></td><td>{quantity(line.quantity)}</td><td>{line.sent_quantity==null?'—':quantity(line.sent_quantity)}</td><td>{line.received_quantity==null?'—':quantity(line.received_quantity)}</td>{editable&&<td><input aria-label={`${t(received?'Received quantity':'Quantity to send')} ${line.name}`} required type="number" min="0" max={received?(line.sent_quantity??'0'):line.quantity} step="0.000001" value={values[line.id]} onChange={event=>setValues({...values,[line.id]:event.target.value})}/></td>}</tr>)}</tbody></table></div>
    {editable&&<><label>{t('Confirmation / discrepancy reason')}<textarea required maxLength={500} value={reason} onChange={event=>setReason(event.target.value)}/></label><button disabled={busy}>{t(received?'Save received quantities':'Save warehouse quantities')}</button></>}
    </form></section>;
}
