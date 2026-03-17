import { useState } from "react";
import {
  Copy, RefreshCw, AlertTriangle, Check, Eye, EyeOff,
  Shield, Activity, Calendar, Zap, Lock, Terminal
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import { toast } from "sonner"; // Assuming sonner is used, or replace with your toast lib

export function ApiKeysView() {
  const [copied, setCopied] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);

  // Mock data simulation
  const userStr = localStorage.getItem("user");
  const user = userStr ? JSON.parse(userStr) : null;
  const apiKey = user?.api_key || "sk_live_51M0...93xS"; // Fallback for preview

  // Logic to only show first 4 and last 4 chars when masked
  const maskedKey = `${apiKey.slice(0, 7)}••••••••••••••••••••••${apiKey.slice(-4)}`;

  const handleCopy = () => {
    navigator.clipboard.writeText(apiKey);
    setCopied(true);
    toast.success("API Key copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRegenerate = () => {
    setIsRegenerating(true);
    // Simulate API call
    setTimeout(() => {
      setIsRegenerating(false);
      toast.success("API Key regenerated successfully");
    }, 2000);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-10 animate-in fade-in slide-in-from-bottom-4 duration-500">

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-foreground tracking-tight">
            API Access
          </h1>
          <p className="text-muted-foreground mt-1">
            Manage authentication tokens for your applications.
          </p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 text-sm font-medium">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          Operational
        </div>
      </div>

      {/* Main Key Card */}
      <div className="bg-card border border-border/50 rounded-3xl shadow-xl shadow-black/5 overflow-hidden">

        {/* Card Header */}
        <div className="px-6 py-5 border-b border-border/50 flex items-center justify-between bg-muted/30">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-background border border-border rounded-xl shadow-sm">
              <KeyIcon className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h3 className="font-semibold text-foreground">Production Secret Key</h3>
              <p className="text-xs text-muted-foreground">Standard RAG Pipeline Scope</p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleRegenerate}
              disabled={isRegenerating}
              className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-background rounded-lg border border-transparent hover:border-border transition-all disabled:opacity-50"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", isRegenerating && "animate-spin")} />
              {isRegenerating ? "Rolling..." : "Roll Key"}
            </button>
          </div>
        </div>

        {/* Key Display Area */}
        <div className="p-6 space-y-6">
          <div className="relative group">
            <div
              className={cn(
                "relative flex items-center justify-between px-4 py-4 rounded-xl border-2 transition-all duration-300",
                showKey
                  ? "bg-secondary/20 border-primary/20"
                  : "bg-secondary/50 border-transparent hover:border-border"
              )}
            >
              <div className="flex-1 font-mono text-sm truncate mr-4">
                <AnimatePresence mode="wait">
                  {showKey ? (
                    <motion.span
                      key="real"
                      initial={{ opacity: 0, filter: "blur(4px)" }}
                      animate={{ opacity: 1, filter: "blur(0px)" }}
                      exit={{ opacity: 0, filter: "blur(4px)" }}
                      className="text-foreground select-all"
                    >
                      {apiKey}
                    </motion.span>
                  ) : (
                    <motion.span
                      key="masked"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="text-muted-foreground select-none tracking-widest"
                    >
                      {maskedKey}
                    </motion.span>
                  )}
                </AnimatePresence>
              </div>

              <div className="flex items-center gap-1">
                <button
                  onClick={() => setShowKey(!showKey)}
                  className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-background transition-colors"
                  title={showKey ? "Hide key" : "Reveal key"}
                >
                  {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
                <div className="w-px h-4 bg-border mx-1" />
                <button
                  onClick={handleCopy}
                  className="p-2 rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors relative"
                  title="Copy to clipboard"
                >
                  {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {/* Security Blur Overlay (Optional visual flair) */}
            {!showKey && (
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-background/10 to-transparent pointer-events-none" />
            )}
          </div>

          {/* Security Alert */}
          <div className="bg-amber-500/5 border border-amber-500/10 rounded-xl p-4 flex gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />
            <div className="space-y-1">
              <h4 className="text-sm font-medium text-amber-700 dark:text-amber-500">Security Notice</h4>
              <p className="text-xs text-amber-700/80 dark:text-amber-500/70 leading-relaxed">
                This key grants full access to your vector database. Do not commit it to public repositories or share it in client-side code.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          icon={Zap}
          label="Rate Limit"
          value="1k"
          sub="requests / min"
          color="text-blue-500"
          bg="bg-blue-500/10"
        />
        <StatCard
          icon={Activity}
          label="Last Used"
          value="2m"
          sub="ago"
          color="text-emerald-500"
          bg="bg-emerald-500/10"
        />
        <StatCard
          icon={Calendar}
          label="Created"
          value="Jan 15"
          sub="2024"
          color="text-purple-500"
          bg="bg-purple-500/10"
        />
      </div>

      {/* Developer Quick Snippet */}
      <div className="space-y-4 pt-4">
        <h3 className="text-lg font-semibold text-foreground flex items-center gap-2">
          <Terminal className="w-5 h-5" /> Quick Integration
        </h3>
        <div className="bg-[#1e1e1e] rounded-xl p-4 overflow-x-auto shadow-inner border border-white/10 group relative">
          <pre className="text-sm font-mono text-gray-300">
            <span className="text-purple-400">curl</span> https://api.rag-platform.com/v1/query \<br />
            {"  "}-H <span className="text-green-400">"Authorization: Bearer {apiKey.slice(0, 12)}..."</span> \<br />
            {"  "}-d <span className="text-yellow-400">'{`{"query": "Hello world"}'`}'</span>
          </pre>
          <button
            onClick={() => {
              navigator.clipboard.writeText(`curl https://api.rag-platform.com/v1/query -H "Authorization: Bearer ${apiKey}" -d '{"query": "Hello world"}'`);
              toast.success("Snippet copied");
            }}
            className="absolute top-4 right-4 p-2 bg-white/10 rounded-lg text-white hover:bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Copy className="w-3 h-3" />
          </button>
        </div>
      </div>

    </div>
  );
}

// Helper Component for Stats
function StatCard({ icon: Icon, label, value, sub, color, bg }: any) {
  return (
    <div className="bg-card border border-border/50 p-4 rounded-2xl flex items-center gap-4 hover:border-border transition-colors shadow-sm">
      <div className={cn("w-12 h-12 rounded-xl flex items-center justify-center shrink-0", bg, color)}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{label}</p>
        <div className="flex items-baseline gap-1">
          <span className="text-xl font-bold text-foreground">{value}</span>
          <span className="text-xs text-muted-foreground">{sub}</span>
        </div>
      </div>
    </div>
  );
}

function KeyIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="7.5" cy="15.5" r="5.5" />
      <path d="m21 2-9.6 9.6" />
      <path d="m15.5 7.5 3 3L22 7l-3-3" />
    </svg>
  );
}