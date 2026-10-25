import { t } from './i18n';
import { useState } from 'react';
import { api, type User } from './api';
type Challenge = {
    mfa_required: true;
    enrollment_required: boolean;
};
export function Auth({ onLogin }: {
    onLogin: (user: User) => void;
}) {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [remember, setRemember] = useState(false);
    const [token, setToken] = useState('');
    const [challenge, setChallenge] = useState<Challenge | null>(null);
    const [setup, setSetup] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        setBusy(true);
        setError('');
        try {
            if (challenge) {
                onLogin(await api<User>('/api/v1/auth/mfa/verify/', { token }));
            }
            else {
                const result = await api<User | Challenge>('/api/v1/auth/login/', { username, password, remember_me: remember });
                setPassword('');
                if ('mfa_required' in result) {
                    setChallenge(result);
                    if (result.enrollment_required) {
                        const result = await api<{
                            configuration_url: string;
                        }>('/api/v1/auth/mfa/enroll/', {});
                        if (result.configuration_url.startsWith('otpauth://'))
                            setSetup(result.configuration_url);
                    }
                }
                else
                    onLogin(result);
            }
        }
        catch (error) {
            setError((error as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    return <main className="auth">
    <img className="signin-logo" src="/logo.png" alt="OPSDEMO Coffee Roasters"/><p className="eyebrow">{t("OPSDEMO operations")}</p><h1>{t("Sign in to your workspace.")}</h1>
    <p>{t("Use the account assigned by your administrator.")}</p>
    <form onSubmit={submit} className="panel form">
      {error && <p className="error" role="alert">{t(error)}</p>}
      {!challenge ? <>
        <label>{t("Username")}<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required maxLength={150}/></label>
        <label>{t("Password")}<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required/></label>
        <label className="remember-signin"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)}/><span>{t("Keep me signed in")}<small>{t("Stay signed in for 30 days on this device.")}</small></span></label>
      </> : <>
        <h2>{t(challenge.enrollment_required ? 'Set up your authenticator' : 'Verify your sign-in')}</h2>
        {setup && <div className="setup-secret"><p>{t("Add a time-based account in your authenticator using this setup key. Keep it private.")}</p>
          <code>{new URL(setup).searchParams.get('secret')}</code><p><a href={setup}>{t("Open an authenticator app")}</a></p></div>}
        <label>{t("Six-digit code")}<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={token} onChange={event => setToken(event.target.value)} required maxLength={6}/></label>
        <p>{t("Enrollment and sign-in challenges expire after five minutes.")}</p>
      </>}
      <button disabled={busy}>{t(busy ? 'Checking…' : challenge ? 'Verify code' : 'Sign in')}</button>
      {challenge && <button className="secondary" type="button" onClick={() => { setChallenge(null); setSetup(''); setToken(''); }}>{t("Start again")}</button>}
    </form>
  </main>;
}
