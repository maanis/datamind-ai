import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Database,
  Key,
  BarChart3,
  MessageSquare,
  Settings,
  Plus,
  Layers,
  LogOut,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const navItems: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "ingest", label: "Ingest Data", icon: Database },
  { id: "api-keys", label: "API Keys", icon: Key },
  { id: "usage", label: "API Usage", icon: BarChart3 },
  { id: "playground", label: "Playground", icon: MessageSquare },
];

interface SidebarProps { }

export function Sidebar({ }: SidebarProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [usagePercent] = useState(67);

  const handleLogout = () => {
    // Clear all authentication data from localStorage
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    // Redirect to login page
    navigate("/login");
  };

  return (
    <aside className=" w-64 dark:bg-neutral-900 rounded-2xl floating-panel p-4 flex flex-col">
      {/* Logo */}
      <div className="flex items-center gap-3 px-3 py-2 mb-6">
        <div className="w-8 h-8 rounded-lg bg-foreground flex items-center justify-center">
          <Layers className="w-4 h-4 text-background" />
        </div>
        <span className="font-semibold text-foreground tracking-tight">
          RAG Builder
        </span>
      </div>

      {/* New Project Button */}
      {/* <button className="flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-xl bg-foreground text-background font-medium text-sm transition-all duration-200 hover:opacity-90 active:animate-press mb-5">
        <Plus className="w-4 h-4" />
        New Project
      </button> */}

      {/* Navigation */}
      <nav className="flex-1 space-y-1">
        {navItems.map((item) => {
          const isActive = location.pathname === `/${item.id}`;
          const Icon = item.icon;

          return (
            <Link
              key={item.id}
              to={`/${item.id}`}
              className={cn(
                "flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200",
                isActive
                  ? "bg-nav-active text-nav-active-foreground pressed-in"
                  : "text-sidebar-foreground hover:bg-sidebar-accent/50 hover:text-foreground"
              )}
            >
              <Icon className="w-[17px] h-[17px]" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Bottom Section */}
      <div className="space-y-2 pt-4 border-t border-sidebar-border">
        {/* Usage Indicator */}
        <div className="px-3 py-2.5 rounded-xl bg-secondary/40">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-muted-foreground">API Requests</span>
            <span className="font-medium text-foreground">{usagePercent}%</span>
          </div>
          <div className="h-1 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-foreground rounded-full transition-all duration-500"
              style={{ width: `${usagePercent}%` }}
            />
          </div>
          <p className="text-[10px] text-muted-foreground mt-1.5">
            6,700 / 10,000 requests
          </p>
        </div>

        {/* Settings */}
        <button className="flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm text-sidebar-foreground hover:bg-sidebar-accent/50 hover:text-foreground transition-all duration-200">
          <Settings className="w-[17px] h-[17px]" />
          Settings
        </button>

        {/* Logout */}
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm text-sidebar-foreground hover:bg-sidebar-accent/50 hover:text-foreground transition-all duration-200"
        >
          <LogOut className="w-[17px] h-[17px]" />
          Logout
        </button>
      </div>
    </aside>
  );
};
