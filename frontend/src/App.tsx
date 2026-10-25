import { t, getLanguage, setLanguage as saveLanguage, type Language } from './i18n';
import { useEffect, useState } from 'react';
import { api, ApiError, can, today, type User } from './api';
import { Auth } from './Auth';
import { Orders } from './Orders';
import { Sales } from './Sales';
import { Warehouse } from './Warehouse';
import { Catalog } from './Catalog';
import { Administration } from './Administration';
import { FleetReport } from './FleetReport';
import { MarketPending } from './MarketPending';
import { Occasional } from './Occasional';
import { BranchMaps } from './BranchMaps';
import { Icon } from './Visuals';
import { Courier } from './Courier';
import { DepartmentApp } from './DepartmentApp';
import { ProductionPreview } from './ProductionPreview';
import { CourierLocations } from './CourierLocations';
type Alert = {
    id: string;
    message: string;
    severity: string;
    read: boolean;
};
type Dashboard = {
    today: number;
    upcoming: number;
    unassigned: number;
    overdue: number;
    delivered: number;
    finance?: {
        by_currency: {
            currency: string;
            expected_today: string;
            collected_today: string;
        }[];
    };
    by_area: {
        'area__name': string | null;
        count: number;
    }[];
};
export function App() {
    const [language, changeLanguage] = useState<Language>(getLanguage);
    useEffect(() => { document.documentElement.lang = language; document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr'; }, [language]);
    const languageControl = <div className="language-switch" aria-label={t('Language')}>{(['en', 'ar'] as Language[]).map(value => <button key={value} className="secondary" aria-pressed={value === language} onClick={() => { saveLanguage(value); changeLanguage(value); }}>{value === 'ar' ? 'العربية' : 'English'}</button>)}</div>;
    const [user, setUser] = useState<User | null>(null);
    const [checking, setChecking] = useState(true);
    const [error, setError] = useState('');
    const [page, setPage] = useState(new URLSearchParams(window.location.search).get('view') === 'fleet' ? 'Fleet report' : 'Overview');
    const [refresh, setRefresh] = useState(0);
    const [pageTrail, setPageTrail] = useState<string[]>([]);
    const [dashboard, setDashboard] = useState<Dashboard | null>(null);
    const [alerts, setAlerts] = useState<Alert[]>([]);
    useEffect(() => {
        api<User>('/api/v1/auth/me/').then(setUser).catch(error => {
            if (!(error instanceof ApiError) || ![401, 403].includes(error.status))
                setError(error.message);
        }).finally(() => setChecking(false));
    }, []);
    useEffect(() => {
        if (!user)
            return;
        if(user.roles.length===1&&['Warehouse','Finance','Production'].includes(user.roles[0]))return;
        let active = true;
        const load = () => Promise.all([api<Dashboard>('/api/v1/dashboard/'), api<Alert[]>('/api/v1/alerts/')])
            .then(([summary, alerts]) => { if (active) {
            setDashboard(summary);
            setAlerts(alerts);
        } })
            .catch(error => { if (active)
            setError(error.message); });
        void load();
        const timer = window.setInterval(() => { void load(); }, 60000);
        return () => { active = false; window.clearInterval(timer); };
    }, [user, refresh]);
    const changed = () => setRefresh(value => value + 1);
    const pages = ['Overview', 'Orders', ...(user && can(user, 'Sales') ? ['Sales'] : []),
        ...(user && can(user, 'Supply Chain') ? ['Pending market orders', 'Occasional orders', 'Branch locations'] : []),
        ...(user && can(user, 'Management', 'Supply Chain', 'Sales', 'Finance') ? ['Fleet report'] : []),
        ...(user && can(user, 'Management', 'Supply Chain') ? ['Courier locations'] : []),
        ...(user && can(user, 'Warehouse', 'Supply Chain', 'Management') ? ['Warehouse'] : []),
        ...(user && can(user, 'Warehouse') ? ['Catalog'] : []),
        ...(user && can(user, 'Supply Chain') && !can(user, 'Admin') ? ['Operations settings'] : []),
        ...(user && can(user, 'Admin') ? ['Administration'] : [])];
    function navigate(next: string) {
        if (next === page || !pages.includes(next)) return;
        setPageTrail(trail => [...trail, page]);
        setPage(next); setError('');
        window.history.replaceState(null, '', next === 'Fleet report' ? '?view=fleet' : window.location.pathname);
        window.scrollTo({ top: 0 });
    }
    function goBack() {
        const detailBack = new Event('opsdemo-back', { cancelable: true });
        window.dispatchEvent(detailBack);
        if (detailBack.defaultPrevented) return;
        const previous = pageTrail.at(-1) || 'Overview';
        setPageTrail(trail => trail.slice(0, -1));
        setPage(pages.includes(previous) ? previous : 'Overview'); setError('');
        window.history.replaceState(null, '', previous === 'Fleet report' ? '?view=fleet' : window.location.pathname);
        window.scrollTo({ top: 0 });
    }
    useEffect(() => {
        const nativeBack = (event: Event) => {
            if (user && (page !== 'Overview' || pageTrail.length)) { event.preventDefault(); goBack(); }
        };
        window.addEventListener('opsdemo-native-back', nativeBack);
        return () => window.removeEventListener('opsdemo-native-back', nativeBack);
    }, [user, page, pageTrail]);
    if (checking)
        return <main className="signin"><p role="status">{t("Connecting to OPSDEMO\u2026")}</p></main>;
    if (!user)
        return <><div className="auth-language">{languageControl}</div><Auth onLogin={user => { setUser(user); setError(''); }}/>{error && <p className="error" role="alert">{t(error)}</p>}</>;
    if(user.roles.length===1&&['Warehouse','Finance'].includes(user.roles[0]))return <DepartmentApp user={user} languageControl={languageControl} onSignOut={()=>{void api('/api/v1/auth/logout/',{}).then(()=>{setUser(null);setPage('Overview');}).catch(error=>setError(error.message));}}/>;
    if(user.roles.length===1&&user.roles[0]==='Production')return <ProductionPreview languageControl={languageControl} onSignOut={()=>{void api('/api/v1/auth/logout/',{}).then(()=>setUser(null)).catch(error=>setError(error.message));}}/>;
    if (user.roles.includes('Courier') && !can(user, 'Admin', 'Management', 'Supply Chain', 'Warehouse', 'Finance', 'Sales'))
        return <div className="courier-app"><header className="topbar"><img className="header-logo" src="/logo.png" alt="OPSDEMO Coffee Roasters"/><div className="topbar-actions">{languageControl}<button className="secondary" onClick={() => { void api('/api/v1/auth/logout/', {}).then(() => { setUser(null); setPage('Overview'); setAlerts([]); setDashboard(null); window.history.replaceState(null, '', window.location.pathname); }).catch(error => setError(error.message)); }}>{t('Sign out')}</button></div></header><main>{error && <p className="error" role="alert">{t(error)}</p>}<Courier/></main></div>;
    return <div className="app"><aside className="sidebar"><a className="brand" href="/"><img className="sidebar-logo" src="/logo.png" alt="OPSDEMO Coffee Roasters"/><span>{t("Operations workspace")}</span></a>
    <nav aria-label={t("Workspaces")}>{pages.map(name => <button key={name} className={page === name ? 'selected' : ''} onClick={() => navigate(name)}><Icon name={name}/><span>{t(name === 'Fleet report' ? 'Orders Excel' : name)}</span></button>)}</nav>
    <div className="profile"><strong>{user.name || user.username}</strong><small>{user.roles.map(t).join(' · ') || 'No role assigned'}</small></div>
  </aside><div className="workspace"><header className="topbar"><div><div className="navigation-controls"><button className="secondary back-button" disabled={page === 'Overview' && !pageTrail.length} onClick={goBack}><Icon name="back"/>{t('Back')}</button><button className="secondary" disabled={page === 'Overview'} onClick={() => navigate('Overview')}><Icon name="Overview"/>{t('Home')}</button></div><span className="eyebrow">{t("OPSDEMO /")}{' '}{t(page === 'Fleet report' ? 'Orders Excel' : page)}</span><p>{today()}{' '}{t("\u00B7 Cairo")}</p></div><div className="topbar-actions"><button className="secondary" onClick={changed}>{t("Refresh")}</button>{languageControl}<button className="secondary signout" onClick={() => { void api('/api/v1/auth/logout/', {}).then(() => { setUser(null); setDashboard(null); setAlerts([]); setPage('Overview'); setPageTrail([]); window.history.replaceState(null, '', window.location.pathname); }).catch(error => setError(error.message)); }}>{t("Sign out")}</button></div></header>
    <main>{error && <p className="error" role="alert">{error}</p>}
      {page === 'Overview' && <section><h1>{t("Your operations, at a glance.")}</h1><p className="intro">{t("Follow preparation, delivery and the exceptions that need attention.")}</p>
        <div className="metrics">{dashboard && [['Today', dashboard.today], ['Upcoming', dashboard.upcoming], ['Unassigned today', dashboard.unassigned], ['Overdue', dashboard.overdue], ['Delivered today', dashboard.delivered]].map(([label, value]) => <article className="panel" key={label}><span>{t(String(label))}</span><strong>{value}</strong></article>)}</div>
        {dashboard?.finance && <div className="panel"><h2>{t("Authorized collections")}</h2>{dashboard.finance.by_currency.map(row => <p key={row.currency}>{row.currency}{' '}{t("\u00B7 Expected today:")}{' '}{row.expected_today}{' '}{t("\u00B7 Collected today:")}{' '}{row.collected_today}</p>)}</div>}
        <div className="two-columns"><section className="panel"><h2>{t("Attention needed")}</h2>{alerts.length === 0 && <p>{t("No active alerts.")}</p>}{alerts.map(alert => <article className="alert-row" key={alert.id}><span className="badge">{t(alert.severity)}</span><p>{t(alert.message)}</p>{!alert.read && <button className="secondary" onClick={() => { void api(`/api/v1/alerts/${alert.id}/read/`, {}).then(changed).catch(error => setError(error.message)); }}>{t("Mark read")}</button>}</article>)}</section>
          <section className="panel"><h2>{t("Today by area")}</h2>{dashboard?.by_area.map(row => <p className="summary-row" key={row.area__name ?? 'unmapped'}><span>{row.area__name ?? t('Needs mapping')}</span><strong>{row.count}</strong></p>)}<button onClick={() => navigate('Orders')}>{t("Open orders")}</button></section></div></section>}
      {page === 'Orders' && <Orders user={user} refresh={refresh} onChange={changed}/>}
      {page === 'Sales' && <Sales refresh={refresh} onChange={changed}/>}
      {page === 'Branch locations' && can(user,'Management','Supply Chain') && <BranchMaps refresh={refresh} onChange={changed}/>}
      {page === 'Occasional orders' && can(user, 'Management', 'Supply Chain') && <Occasional refresh={refresh} onChange={changed}/>}
      {page === 'Pending market orders' && can(user, 'Supply Chain') && <MarketPending refresh={refresh} onChange={changed} onScheduled={() => navigate('Orders')}/>}
      {page === 'Warehouse' && can(user, 'Warehouse', 'Supply Chain', 'Management') && <Warehouse user={user} refresh={refresh} onChange={changed} onAddProduct={() => navigate('Catalog')}/>}
      {page === 'Catalog' && can(user, 'Warehouse') && <Catalog refresh={refresh} onChange={changed}/>}
      {(page === 'Administration' || page === 'Operations settings') && <Administration user={user} refresh={refresh} onChange={changed}/>}
      {page === 'Fleet report' && can(user, 'Management', 'Supply Chain', 'Sales', 'Finance') && <FleetReport user={user} refresh={refresh}/>}
      {page === 'Courier locations' && can(user, 'Management','Supply Chain') && <CourierLocations/>}
    </main><footer>{t("OPSDEMO \u00B7 Shared order and preparation records")}</footer></div></div>;
}
