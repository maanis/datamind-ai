import { useNavigate } from "react-router-dom";
import {
  MessageSquare, Key, ArrowUpRight, Sparkles, Database,
  Cpu, Search, Plus, Folder, Activity, Zap, Layers
} from "lucide-react";
import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import { useWorkspaces, useCreateWorkspace } from "@/hooks/useWorkspaces";

interface Workspace {
  workspaceId: string;
  workspaceName: string;
}

export function DashboardView() {
  const navigate = useNavigate();
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [showCreateInput, setShowCreateInput] = useState(false);

  // Use TanStack Query hooks
  const { data: workspaces = [], isLoading: workspacesLoading, error: workspacesError } = useWorkspaces();
  const createWorkspaceMutation = useCreateWorkspace();

  // Show error toast if workspaces fetch fails
  useEffect(() => {
    if (workspacesError) {
      toast.error("Failed to fetch workspaces");
    }
  }, [workspacesError]);

  const createWorkspace = async () => {
    if (!newWorkspaceName.trim()) {
      toast.error("Please name your workspace");
      return;
    }

    try {
      await createWorkspaceMutation.mutateAsync({
        workspaceName: newWorkspaceName.trim()
      });

      setNewWorkspaceName("");
      setShowCreateInput(false);
      toast.success("Workspace created");
    } catch (error) {
      toast.error("Failed to create workspace");
    }
  };

  const actions = [
    {
      title: "Playground",
      desc: "Chat with your data",
      icon: MessageSquare,
      path: "/playground",
      color: "text-emerald-500",
      bg: "bg-emerald-500/5",
      border: "hover:border-emerald-500/20"
    },
    {
      title: "Knowledge Base",
      desc: "Manage documents",
      icon: Database,
      path: "/ingest",
      color: "text-blue-500",
      bg: "bg-blue-500/5",
      border: "hover:border-blue-500/20"
    },
    {
      title: "API Access",
      desc: "Integration keys",
      icon: Key,
      path: "/api-keys",
      color: "text-amber-500",
      bg: "bg-amber-500/5",
      border: "hover:border-amber-500/20"
    }
  ];

  return (
    <div className="h-screen bg-background text-foreground  overflow-hidden">
      <div className="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">

        {/* LEFT COLUMN: Main Content */}
        <main className="space-y-6">

          {/* Hero Header */}
          <header className="space-y-2">
            <div className="inline-flex items-center gap-2 px-2 py-1 rounded-full bg-secondary/50 border border-border/50 text-xs font-medium text-muted-foreground">
              <Sparkles className="w-3 h-3 text-primary" />
              <span>RAG Engine v2.0</span>
            </div>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-foreground">
              Good morning, <br />
              <span className="text-muted-foreground">Ready to build?</span>
            </h1>
            <p className="text-sm text-muted-foreground max-w-md">
              Manage your retrieval pipelines and chat with your knowledge base in real-time.
            </p>
          </header>

          {/* Stats Row */}
          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 rounded-xl bg-card border border-border/50 shadow-sm flex flex-col gap-1">
              <span className="text-muted-foreground text-xs font-medium uppercase tracking-wider">Active Pipelines</span>
              <span className="text-xl font-bold text-foreground">
                {workspacesLoading ? '...' : workspaces.length}
              </span>
            </div>
            <div className="p-3 rounded-xl bg-card border border-border/50 shadow-sm flex flex-col gap-1">
              <span className="text-muted-foreground text-xs font-medium uppercase tracking-wider">Total Documents</span>
              <span className="text-xl font-bold text-foreground">128</span>
            </div>
            <div className="p-3 rounded-xl bg-card border border-border/50 shadow-sm flex flex-col gap-1">
              <span className="text-muted-foreground text-xs font-medium uppercase tracking-wider">Queries Today</span>
              <span className="text-xl font-bold text-foreground">1.2k</span>
            </div>
          </div>

          {/* Workspaces Grid */}
          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold tracking-tight flex items-center gap-2">
                <Layers className="w-4 h-4 text-muted-foreground" />
                Workspaces
              </h2>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* Create New Card */}
              <div
                className={cn(
                  "relative group overflow-hidden rounded-2xl border-2 border-dashed border-border/60 hover:border-primary/50 transition-colors p-4 flex flex-col justify-between min-h-[140px] cursor-pointer bg-muted/5 hover:bg-muted/10",
                  showCreateInput && "border-primary ring-1 ring-primary/20 bg-background"
                )}
                onClick={() => !showCreateInput && setShowCreateInput(true)}
              >
                {!showCreateInput ? (
                  <>
                    <div className="w-10 h-10 rounded-xl bg-background border border-border shadow-sm flex items-center justify-center group-hover:scale-110 transition-transform duration-300">
                      <Plus className="w-5 h-5 text-muted-foreground group-hover:text-primary" />
                    </div>
                    <div>
                      <h3 className="font-medium text-foreground">Create New</h3>
                      <p className="text-xs text-muted-foreground">Start a new project</p>
                    </div>
                  </>
                ) : (
                  <div className="h-full flex flex-col justify-center animate-in fade-in zoom-in-95">
                    <label className="text-xs font-medium text-primary mb-1">Workspace Name</label>
                    <input
                      autoFocus
                      type="text"
                      value={newWorkspaceName}
                      onChange={(e) => setNewWorkspaceName(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && createWorkspace()}
                      placeholder="e.g. Finance Bot"
                      className="w-full bg-transparent text-lg font-semibold placeholder:text-muted-foreground/30 focus:outline-none mb-3"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={(e) => { e.stopPropagation(); createWorkspace(); }}
                        disabled={createWorkspaceMutation.isPending}
                        className="px-3 py-1 bg-primary text-primary-foreground text-sm font-medium rounded-full hover:opacity-90 disabled:opacity-50"
                      >
                        {createWorkspaceMutation.isPending ? 'Creating...' : 'Create'}
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setShowCreateInput(false); }}
                        className="px-3 py-1 text-muted-foreground text-sm font-medium hover:text-foreground"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Loading Skeletons */}
              {workspacesLoading && Array.from({ length: 2 }).map((_, i) => (
                <div
                  key={`skeleton-${i}`}
                  className="rounded-2xl bg-card border border-border/50 p-4 flex flex-col justify-between min-h-[140px] animate-pulse"
                >
                  <div className="flex justify-between items-start">
                    <div className="w-8 h-8 rounded-xl bg-muted"></div>
                    <div className="w-6 h-6 rounded-full bg-muted"></div>
                  </div>
                  <div>
                    <div className="h-4 bg-muted rounded mb-1"></div>
                    <div className="h-3 bg-muted rounded w-2/3"></div>
                  </div>
                </div>
              ))}

              {/* Existing Workspace Cards */}
              {!workspacesLoading && workspaces.map((ws) => (
                <div
                  key={ws.workspaceId}
                  className="group relative overflow-hidden rounded-2xl bg-card border border-border/50 p-4 flex flex-col justify-between min-h-[140px] hover:shadow-lg hover:shadow-primary/5 hover:border-primary/20 transition-all duration-300"
                >
                  <div className="flex justify-between items-start">
                    <div className="w-8 h-8 rounded-xl bg-secondary/50 flex items-center justify-center text-foreground font-semibold text-sm">
                      {(ws.workspaceName || 'W').charAt(0).toUpperCase()}
                    </div>
                    <div className="opacity-0 group-hover:opacity-100 transition-opacity -translate-y-2 group-hover:translate-y-0 duration-300">
                      <div className="p-1.5 rounded-full bg-background border border-border shadow-sm">
                        <ArrowUpRight className="w-3 h-3 text-foreground" />
                      </div>
                    </div>
                  </div>

                  <div>
                    <h3 className="text-sm font-semibold text-foreground mb-1 group-hover:text-primary transition-colors">
                      {ws.workspaceName || 'Unnamed Workspace'}
                    </h3>
                    <p className="text-xs text-muted-foreground font-mono">
                      ID: {(ws.workspaceId || 'unknown').slice(0, 6)}...
                    </p>
                  </div>

                  {/* Decorative background gradient */}
                  <div className="absolute -bottom-8 -right-8 w-24 h-24 bg-primary/5 blur-2xl rounded-full group-hover:bg-primary/10 transition-colors pointer-events-none" />
                </div>
              ))}
            </div>
          </section>
        </main>

        {/* RIGHT COLUMN: Sidebar / Quick Actions */}
        <aside className="space-y-4 lg:pt-4">

          {/* Quick Actions List */}
          <div className="bg-card/50 backdrop-blur-sm rounded-2xl p-2 border border-border/50">
            {actions.map((action, i) => {
              const Icon = action.icon;
              return (
                <button
                  key={i}
                  onClick={() => navigate(action.path)}
                  className={cn(
                    "w-full flex items-center gap-3 p-3 rounded-xl transition-all duration-200 group hover:bg-background border border-transparent",
                    action.border
                  )}
                >
                  <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-colors", action.bg, action.color)}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="text-left flex-1">
                    <h4 className="font-medium text-foreground text-sm">{action.title}</h4>
                    <p className="text-xs text-muted-foreground">{action.desc}</p>
                  </div>
                  <ArrowUpRight className="w-3 h-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              );
            })}
          </div>

          {/* System Status / Mini Pipeline */}
          <div className="rounded-2xl border border-border/50 bg-card p-4 space-y-3">
            <div className="flex items-center gap-2 mb-1">
              <Activity className="w-4 h-4 text-primary" />
              <h3 className="font-semibold text-xs">System Health</h3>
            </div>

            <div className="space-y-3">
              <div className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-muted-foreground">Vector Database</span>
                  <span className="text-emerald-500 font-medium">Operational</span>
                </div>
                <div className="h-1 w-full bg-secondary rounded-full overflow-hidden">
                  <div className="h-full w-full bg-emerald-500 rounded-full" />
                </div>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-muted-foreground">API Latency</span>
                  <span className="text-foreground font-medium">45ms</span>
                </div>
                <div className="h-1 w-full bg-secondary rounded-full overflow-hidden">
                  <div className="h-full w-[85%] bg-primary rounded-full" />
                </div>
              </div>
            </div>

            <div className="pt-3 border-t border-border/50">
              <button className="w-full py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-secondary rounded-lg transition-colors">
                View System Logs
              </button>
            </div>
          </div>

          <div className="rounded-xl bg-gradient-to-br from-indigo-500/10 via-purple-500/10 to-transparent p-4 border border-indigo-500/10">
            <Zap className="w-4 h-4 text-indigo-500 mb-1" />
            <h4 className="font-semibold text-xs mb-1">Upgrade Plan</h4>
            <p className="text-xs text-muted-foreground mb-2">Unlock unlimited vectors and team collaboration.</p>
            <button className="text-xs font-medium bg-background text-foreground px-2 py-1 rounded-lg border border-border shadow-sm hover:shadow-md transition-all">
              View Plans
            </button>
          </div>

        </aside>
      </div>
    </div>
  );
}