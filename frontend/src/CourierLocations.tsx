import {useEffect,useState} from 'react';
import {api} from './api';
import {t} from './i18n';
type Row={id:string;name:string;orders:{id:string;number:string;customer_name:string}[];position:null|{latitude:number;longitude:number;accuracy:number;sampled_at:string}};
export function CourierLocations(){
 const [rows,setRows]=useState<Row[]>([]),[error,setError]=useState('');
 useEffect(()=>{let live=true;const load=()=>api<Row[]>('/api/v1/courier/locations/').then(rows=>{if(live){setRows(rows);setError('');}}).catch(e=>{if(live)setError(e.message);});void load();const timer=window.setInterval(load,30000);return()=>{live=false;window.clearInterval(timer);};},[]);
 return <section><h1>{t('Courier locations')}</h1><p>{t('Only active dispatched deliveries share location. This shows the last reported position, not proof of activity.')}</p>{error&&<p className="error" role="alert">{error}</p>}{!rows.length&&<p>{t('No couriers currently out for delivery.')}</p>}<div className="cards">{rows.map(row=><article className="panel" key={row.id}><h2>{row.name}</h2><p>{row.orders.map(order=>order.number).join(' · ')}</p>{row.position?<><p>{t('Last location update')}: {new Date(row.position.sampled_at).toLocaleString()}</p><p>{t('Accuracy')}: ±{Math.round(row.position.accuracy)} m</p><a className="button" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/search/?api=1&query=${row.position.latitude},${row.position.longitude}`}>{t('Open Google Maps')}</a></>:<p>{t('No recent location. Check courier permission, GPS and connection.')}</p>}</article>)}</div></section>;
}
