import React, { Suspense, lazy, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/useAuth';
import LogoMark from '../components/LogoMark';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Alert } from '../components/Alert';
import {
  User, Lock, Eye, EyeOff, ArrowRight
} from 'lucide-react';

const ThreeBackground = lazy(() => import('../components/ThreeBackground'));

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const { login, error, clearError, isLoading } = useAuth();

  const [formData, setFormData] = useState({ username: '', password: '' });
  const [localError, setLocalError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
    setLocalError(null);
    clearError();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (!formData.username.trim()) { setLocalError('Username is required'); return; }
    if (!formData.password) { setLocalError('Password is required'); return; }
    try {
      await login(formData.username, formData.password);
      navigate('/dashboard');
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Login failed');
    }
  };

  const displayError = localError || error;

  return (
    <div className="min-h-screen relative overflow-hidden flex items-center justify-center px-4 bg-[#050810]">
      <Suspense fallback={null}>
        <ThreeBackground />
      </Suspense>
      <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" style={{ zIndex: 1 }} />

      <div className="w-full max-w-md relative z-10 animate-fade-in">
        <div className="text-center mb-10">
          <div className="inline-flex flex-col items-center gap-4">
            <div className="w-20 h-20 rounded-3xl bg-white/5 border border-white/10 flex items-center justify-center shadow-[0_0_50px_rgba(34,211,238,0.15)] backdrop-blur-xl">
              <LogoMark size={40} />
            </div>
            <div>
              <h1 className="text-3xl font-black text-white tracking-tighter uppercase italic">AutoPilot</h1>
              <p className="text-gray-500 text-xs font-black uppercase tracking-widest mt-1">Enterprise Deployment Suite</p>
            </div>
          </div>
        </div>

        <Card className="p-10 bg-white/[0.02] border-white/5 shadow-2xl backdrop-blur-2xl rounded-[2.5rem]">
          {displayError && (
            <div className="mb-6">
              <Alert type="error" message={displayError} onClose={() => { setLocalError(null); clearError(); }} />
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label htmlFor="username" className="block text-[10px] font-black text-gray-600 uppercase tracking-[0.2em] mb-3 ml-1">
                Operator ID
              </label>
              <div className="relative group">
                <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors">
                  <User size={16} />
                </span>
                <input
                  id="username"
                  name="username"
                  type="text"
                  value={formData.username}
                  onChange={handleChange}
                  placeholder="Username"
                  className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-4 py-4 text-sm text-white focus:outline-none focus:border-cyan-500/50 focus:bg-white/[0.08] transition-all"
                  disabled={isLoading}
                />
              </div>
            </div>

            <div>
              <label htmlFor="password" className="block text-[10px] font-black text-gray-600 uppercase tracking-[0.2em] mb-3 ml-1">
                Access Pass
              </label>
              <div className="relative group">
                <span className="absolute inset-y-0 left-4 flex items-center text-gray-600 group-focus-within:text-cyan-400 transition-colors">
                  <Lock size={16} />
                </span>
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  value={formData.password}
                  onChange={handleChange}
                  placeholder="••••••••"
                  className="w-full bg-white/5 border border-white/5 rounded-2xl pl-12 pr-12 py-4 text-sm text-white focus:outline-none focus:border-cyan-500/50 focus:bg-white/[0.08] transition-all"
                  disabled={isLoading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute inset-y-0 right-4 flex items-center text-gray-600 hover:text-white transition-colors"
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <Button
              type="submit"
              loading={isLoading}
              className="w-full py-7 rounded-2xl text-xs font-black uppercase tracking-[0.2em] bg-white text-black hover:bg-gray-200 transition-all shadow-[0_0_30px_rgba(255,255,255,0.1)]"
            >
              Initialize Session <ArrowRight size={16} className="ml-2" />
            </Button>
          </form>

          <div className="mt-8 pt-8 border-t border-white/5 flex flex-col items-center gap-4">
            <p className="text-gray-600 text-[10px] font-black uppercase tracking-widest">Global Authentication</p>
            <Link
              to="/register"
              className="text-xs font-bold text-gray-400 hover:text-white transition-colors"
            >
              Request new workspace access
            </Link>
          </div>
        </Card>

        <p className="text-center text-[10px] font-black text-gray-800 uppercase tracking-[0.4em] mt-10">
          GENESIS_DEVOPS_CORE_V2
        </p>
      </div>
    </div>
  );
};
