import React, { Suspense, lazy, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/useAuth';
import LogoMark from '../components/LogoMark';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Alert } from '../components/Alert';
import {
  Mail, Lock, ArrowRight, ShieldCheck, User
} from 'lucide-react';

const ThreeBackground = lazy(() => import('../components/ThreeBackground'));

export const RegisterPage: React.FC = () => {
  const navigate = useNavigate();
  const { register, error, clearError, isLoading } = useAuth();

  const [formData, setFormData] = useState({ username: '', email: '', password: '', confirmPassword: '', fullName: '' });
  const [localError, setLocalError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [passwordStrength, setPasswordStrength] = useState(0);

  const calculatePasswordStrength = (pass: string) => {
    let s = 0;
    if (pass.length >= 6) s += 25;
    if (pass.length >= 10) s += 25;
    if (/[a-z]/.test(pass) && /[A-Z]/.test(pass)) s += 25;
    if (/\d/.test(pass)) s += 15;
    if (/[^a-zA-Z0-9]/.test(pass)) s += 10;
    return Math.min(s, 100);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
    if (name === 'password') setPasswordStrength(calculatePasswordStrength(value));
    setLocalError(null);
    clearError();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    setSuccessMessage(null);
    if (!formData.username.trim()) { setLocalError('Username is required'); return; }
    if (formData.username.length < 3) { setLocalError('Username must be at least 3 characters'); return; }
    if (!formData.email.trim()) { setLocalError('Email is required'); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) { setLocalError('Please enter a valid email'); return; }
    if (!formData.password) { setLocalError('Password is required'); return; }
    if (formData.password.length < 6) { setLocalError('Password must be at least 6 characters'); return; }
    if (formData.password !== formData.confirmPassword) { setLocalError('Passwords do not match'); return; }
    try {
      await register(formData.username, formData.email, formData.password, formData.fullName || undefined);
      setSuccessMessage('Account created. Redirecting to sign in…');
      setTimeout(() => navigate('/login'), 2000);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Registration failed');
    }
  };

  const displayError = localError || error;

  return (
    <div className="min-h-screen relative overflow-hidden flex items-center justify-center px-4 py-10 bg-[#050810]">
      <Suspense fallback={null}>
        <ThreeBackground />
      </Suspense>
      <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" style={{ zIndex: 1 }} />

      <div className="w-full max-w-lg relative z-10 animate-fade-in">
        <div className="text-center mb-8">
          <div className="inline-flex flex-col items-center gap-4">
            <div className="w-16 h-16 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center backdrop-blur-xl">
              <LogoMark size={32} />
            </div>
            <div>
              <h1 className="text-2xl font-black text-white tracking-tighter uppercase italic">Create Registry</h1>
              <p className="text-gray-500 text-[10px] font-black uppercase tracking-widest mt-1">Authorized Operator Setup</p>
            </div>
          </div>
        </div>

        <Card className="p-8 md:p-10 bg-white/[0.02] border-white/5 shadow-2xl backdrop-blur-2xl rounded-[2.5rem]">
          {displayError && (
            <div className="mb-6 animate-fade-in">
              <Alert type="error" message={displayError} onClose={() => { setLocalError(null); clearError(); }} />
            </div>
          )}
          {successMessage && (
            <div className="mb-6 animate-fade-in">
              <Alert type="success" message={successMessage} />
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div>
                <label htmlFor="fullName" className="block text-[9px] font-black text-gray-600 uppercase tracking-widest mb-2 ml-1">Identity Name</label>
                <div className="relative group">
                  <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors"><User size={14} /></span>
                  <input id="fullName" name="fullName" type="text" value={formData.fullName} onChange={handleChange}
                    placeholder="Full Name" className="w-full bg-white/5 border border-white/5 rounded-2xl pl-10 pr-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
                </div>
              </div>
              <div>
                <label htmlFor="username" className="block text-[9px] font-black text-gray-600 uppercase tracking-widest mb-2 ml-1">Operator ID</label>
                <div className="relative group">
                  <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors"><User size={14} /></span>
                  <input id="username" name="username" type="text" value={formData.username} onChange={handleChange}
                    placeholder="jdoe_admin" className="w-full bg-white/5 border border-white/5 rounded-2xl pl-10 pr-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
                </div>
              </div>
            </div>

            <div>
              <label htmlFor="email" className="block text-[9px] font-black text-gray-600 uppercase tracking-widest mb-2 ml-1">Network Email</label>
              <div className="relative group">
                <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors"><Mail size={16} /></span>
                <input id="email" name="email" type="email" value={formData.email} onChange={handleChange}
                  placeholder="admin@enterprise.ai" className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div>
                <label htmlFor="password" className="block text-[9px] font-black text-gray-600 uppercase tracking-widest mb-2 ml-1">Access Pass</label>
                <div className="relative group">
                  <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors"><Lock size={16} /></span>
                  <input id="password" name="password" type="password" value={formData.password} onChange={handleChange}
                    placeholder="••••••••" className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
                </div>
              </div>
              <div>
                <label htmlFor="confirmPassword" className="block text-[9px] font-black text-gray-600 uppercase tracking-widest mb-2 ml-1">Verify Pass</label>
                <div className="relative group">
                  <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors"><ShieldCheck size={16} /></span>
                  <input id="confirmPassword" name="confirmPassword" type="password" value={formData.confirmPassword} onChange={handleChange}
                    placeholder="••••••••" className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
                </div>
              </div>
            </div>

            {formData.password.length > 0 && (
              <div className="px-1">
                <div className="flex gap-1 mb-2">
                  {[1, 2, 3, 4].map(bar => (
                    <div key={bar} className={`flex-1 h-0.5 rounded-full transition-all duration-500 ${bar <= Math.ceil(passwordStrength / 25) ? 'bg-cyan-500 shadow-[0_0_10px_rgba(34,211,238,0.5)]' : 'bg-white/5'}`} />
                  ))}
                </div>
                <p className="text-[9px] font-black text-gray-600 uppercase tracking-widest text-right">Pass Complexity Pass</p>
              </div>
            )}

            <Button
              type="submit"
              loading={isLoading}
              className="w-full py-7 mt-2 rounded-2xl text-xs font-black uppercase tracking-[0.2em] bg-white text-black hover:bg-gray-200 transition-all shadow-[0_0_30px_rgba(255,255,255,0.1)]"
            >
              Verify & Register <ArrowRight size={16} className="ml-2" />
            </Button>
          </form>

          <div className="mt-8 pt-8 border-t border-white/5 flex flex-col items-center gap-4">
            <Link
              to="/login"
              className="text-xs font-bold text-gray-400 hover:text-white transition-colors"
            >
              Return to Operator Sign-in
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
};
