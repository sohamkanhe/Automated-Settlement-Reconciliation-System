import React, { useState } from 'react';
import { X, Database, Sparkles, Sliders, CheckCircle2 } from 'lucide-react';
import { generateTestData } from '../services/api';

export default function GeneratorModal({ onClose, onGenerated }) {
  const [numTxns, setNumTxns] = useState(1200);
  const [seed, setSeed] = useState(42);
  const [month, setMonth] = useState(10);
  const [year, setYear] = useState(2026);
  const [isGenerating, setIsGenerating] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setIsGenerating(true);
      await generateTestData({
        num_transactions: Number(numTxns),
        seed: Number(seed),
        month: Number(month),
        year: Number(year)
      });
      if (onGenerated) onGenerated();
      onClose();
    } catch (err) {
      alert('Error generating test data: ' + err.message);
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl">
        
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/10 text-emerald-400 flex items-center justify-center">
              <Database className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">Test Data Generator & Seeder</h3>
              <p className="text-xs text-slate-400">Configure synthetic payment volume and planted gap parameters.</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 text-xs">
          <div>
            <label className="block text-slate-300 font-medium mb-1.5">
              Transaction Volume ({numTxns.toLocaleString()} transactions)
            </label>
            <input
              type="range"
              min="300"
              max="3000"
              step="100"
              value={numTxns}
              onChange={(e) => setNumTxns(e.target.value)}
              className="w-full accent-emerald-500 cursor-pointer"
            />
            <div className="flex justify-between text-[11px] text-slate-500 mt-1">
              <span>300 (Fast)</span>
              <span>1,200 (Recommended)</span>
              <span>3,000 (Stress Test)</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Random Seed (Reproducibility)</label>
              <input
                type="number"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-brand-500 font-mono"
              />
            </div>
            <div>
              <label className="block text-slate-300 font-medium mb-1">Reconciliation Period</label>
              <select
                value={`${year}-${month}`}
                onChange={(e) => {
                  const [y, m] = e.target.value.split('-');
                  setYear(y);
                  setMonth(m);
                }}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-brand-500"
              >
                <option value="2026-10">October 2026 (Month-End Close)</option>
              </select>
            </div>
          </div>

          {/* Planted Gaps Reminder */}
          <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
            <span className="text-[11px] font-semibold text-brand-400 uppercase tracking-wider block">
              Auto-Planted Gaps Included:
            </span>
            <ul className="space-y-1.5 text-slate-300 text-[11px]">
              <li className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-brand-400 shrink-0" />
                <span>12 late-month transactions settling next month (Nov 2-3)</span>
              </li>
              <li className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-brand-400 shrink-0" />
                <span>Up to 350 transactions with sub-cent fee rounding drift</span>
              </li>
              <li className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-brand-400 shrink-0" />
                <span>1 bank duplicate payout + 1 platform duplicate checkout retry</span>
              </li>
              <li className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-brand-400 shrink-0" />
                <span>3 orphan bank refunds with missing platform sales history</span>
              </li>
            </ul>
          </div>

          <div className="pt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isGenerating}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-emerald-400 hover:bg-emerald-300 active:bg-emerald-500 rounded-lg transition-colors flex items-center gap-1.5 shadow-lg shadow-emerald-500/20 disabled:opacity-50"
            >
              <Sparkles className={`w-3.5 h-3.5 ${isGenerating ? 'animate-spin' : ''}`} />
              <span>{isGenerating ? 'Generating & Seeding...' : 'Generate & Reconcile'}</span>
            </button>
          </div>
        </form>

      </div>
    </div>
  );
}
