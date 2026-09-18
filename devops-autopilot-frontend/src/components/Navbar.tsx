import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/useAuth';
import LogoMark from './LogoMark';
import {
  ChevronDown, LogOut, Activity, LayoutGrid
} from 'lucide-react';

export const Navbar: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [showUserMenu, setShowUserMenu] = useState(false);

  const handleLogout = () => { logout(); navigate('/login'); };
  const initial = user?.username?.charAt(0).toUpperCase() ?? '?';

  return (
    <nav className="sticky top-0 z-50 bg-[#050810]/80 backdrop-blur-2xl border-b border-white/5 h-20 flex items-center">
      <div className="max-w-[1600px] w-full mx-auto px-6">
        <div className="flex justify-between items-center">

          {/* Logo Group */}
          <Link to="/dashboard" className="flex items-center gap-4 group">
            <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center transition-all group-hover:bg-white/10 group-hover:border-white/20">
              <LogoMark size={24} />
            </div>
            <div className="hidden sm:block">
              <p className="text-sm font-black text-white uppercase italic tracking-tighter">AutoPilot</p>
              <p className="text-[9px] text-gray-600 font-black uppercase tracking-[0.2em]">Deployment Suite</p>
            </div>
          </Link>

          {/* Navigation Links */}
          <div className="hidden md:flex items-center gap-8">
             <Link to="/dashboard" className="text-[10px] font-black uppercase tracking-[0.2em] text-gray-500 hover:text-white transition-colors flex items-center gap-2">
                <LayoutGrid size={14} /> Dashboard
             </Link>
             <div className="h-4 w-[1px] bg-white/5"></div>
             <div className="flex items-center gap-2 px-3 py-1.5 bg-emerald-500/5 border border-emerald-500/10 rounded-full">
                <Activity size={12} className="text-emerald-500 animate-pulse" />
                <span className="text-[10px] font-black uppercase tracking-widest text-emerald-500">System Online</span>
             </div>
          </div>

          {/* User Section */}
          <div className="flex items-center gap-4">
            {user && (
              <div className="relative">
                <button
                  onClick={() => setShowUserMenu(v => !v)}
                  className={`flex items-center gap-3 pl-2 pr-4 py-2 rounded-2xl transition-all border ${
                    showUserMenu ? 'bg-white/10 border-white/20' : 'bg-white/5 border-white/5 hover:bg-white/10'
                  }`}
                >
                  <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-white to-gray-400 flex items-center justify-center text-xs font-black text-black">
                    {initial}
                  </div>
                  <div className="hidden md:block text-left">
                    <p className="text-xs font-black text-white leading-none mb-1 uppercase tracking-tight">{user.username}</p>
                    <p className="text-[9px] text-gray-500 font-medium leading-none truncate max-w-[100px]">{user.email}</p>
                  </div>
                  <ChevronDown size={14} className={`text-gray-600 transition-transform ${showUserMenu ? 'rotate-180' : ''}`} />
                </button>

                {showUserMenu && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setShowUserMenu(false)} />
                    <div className="absolute right-0 top-full mt-3 w-64 z-20 animate-fade-in">
                      <div className="rounded-[2rem] overflow-hidden bg-[#0d1117]/95 backdrop-blur-3xl border border-white/10 shadow-2xl p-2">
                        <div className="px-5 py-4 border-b border-white/5">
                           <p className="text-[10px] font-black uppercase tracking-widest text-gray-600 mb-2">Authenticated As</p>
                           <p className="text-sm font-black text-white">{user.username}</p>
                           <p className="text-xs text-gray-500">{user.email}</p>
                        </div>

                        <div className="py-2">
                          <button
                            onClick={() => { setShowUserMenu(false); handleLogout(); }}
                            className="w-full flex items-center gap-3 px-5 py-3 text-xs font-black uppercase tracking-widest text-rose-500 hover:bg-rose-500/10 transition-all rounded-xl"
                          >
                            <LogOut size={15} /> Terminate
                          </button>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
};