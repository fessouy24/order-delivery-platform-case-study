import { useEffect, useState } from 'react';
import { t } from './i18n';

const paths: Record<string, string> = {
  'phone-x': 'M6 3H3v4c0 8 6 14 14 14h4v-3l-5-2-2 2c-4-2-6-4-8-8l2-2z M16 3l6 6 M22 3l-6 6',
  back: 'M19 12H5 M12 5l-7 7 7 7',
  Overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  Orders: 'M8 4H5v17h14V4h-3 M8 2h8v4H8z M8 11h8 M8 16h5',
  'Fleet report': 'M2 5h12v12H2z M14 9h4l4 4v4h-8 M5 17a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M18 17a2 2 0 1 0 0 4a2 2 0 1 0 0-4',
  Warehouse: 'M3 8l9-5 9 5v12H3z M8 20v-8h8v8 M3 8h18',
  Sales: 'M3 3h2l3 12h10l3-8H6 M9 20h.01 M18 20h.01',
  Catalog: 'M3 3h8l10 10-8 8L3 11z M7 7h.01',
  Administration: 'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  check: 'M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20 M7 12l3 3 7-7', cancel: 'M8 2h8l6 6v8l-6 6H8l-6-6V8z M8 8l8 8 M16 8l-8 8',
  calendar: 'M3 5h18v17H3z M7 2v6 M17 2v6 M3 11h18 M7 15h3 M14 15h3',
  photo: 'M3 6h4l2-3h6l2 3h4v15H3z M12 9a4 4 0 1 0 0 8a4 4 0 1 0 0-8',
  clock: 'M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20 M12 6v6l4 2',
  person: 'M12 3a4 4 0 1 0 0 8a4 4 0 1 0 0-8 M4 21v-3a8 5 0 0 1 16 0v3',
  box: 'M3 7l9-5 9 5v10l-9 5-9-5z M3 7l9 5 9-5 M12 12v10',
};
export function Icon({ name }: { name: string }) {
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] ?? paths.Administration}/></svg>;
}

export function ProductPhoto({ src, name }: { src?: string; name: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return src && !failed ? <a className="photo-link" href={src} target="_blank" rel="noreferrer" aria-label={`${t('View photo')}: ${name}`}><img className="product-photo" src={src} alt={name} loading="lazy" onError={() => setFailed(true)}/></a> :
    <span className="product-photo photo-missing" aria-label={t('No photo')} title={t('No photo')}><Icon name="photo"/><small>{t('No photo')}</small></span>;
}

const states: Record<string, [string, string]> = {
  SCHEDULED: ['calendar', 'Scheduled'], PREPARING: ['box', 'Preparing'], READY: ['check', 'Ready'],
  ASSIGNED: ['person', 'Assigned'], OUT_FOR_DELIVERY: ['Fleet report', 'Out for delivery'],
  DELIVERED: ['check', 'Delivered'], CANCELLED: ['cancel', 'Cancelled'],
};
export function StatusBadge({ state }: { state: string }) {
  const [icon, label] = states[state] ?? ['•', state];
  return <span className={`status-badge status-${state.toLowerCase()}`}><Icon name={icon}/>{t(label)}</span>;
}
export function OrderSteps({ state }: { state: string }) {
  const steps = ['SCHEDULED', 'PREPARING', 'READY', 'ASSIGNED', 'OUT_FOR_DELIVERY', 'DELIVERED'];
  if (state === 'CANCELLED') return <StatusBadge state={state}/>;
  const current = steps.indexOf(state);
  return <ol className="order-steps" aria-label={t('Order progress')}>{steps.map((step, index) => <li key={step} className={index <= current ? 'done' : ''} aria-current={index === current ? 'step' : undefined}><span aria-hidden="true"><Icon name={index < current ? 'check' : states[step][0]}/></span><small>{t(states[step][1])}</small></li>)}</ol>;
}
export function quantity(value: string) { return value.includes('.') ? value.replace(/\.?0+$/, '') : value; }
