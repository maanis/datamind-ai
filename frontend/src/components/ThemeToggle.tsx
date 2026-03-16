import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <button className="w-9 h-9 rounded-full bg-secondary flex items-center justify-center">
        <Sun className="w-4 h-4 text-muted-foreground" />
      </button>
    );
  }

  return (
    <button
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      className="relative w-9 h-9 rounded-full bg-secondary hover:bg-accent transition-colors duration-200 flex items-center justify-center group"
      aria-label="Toggle theme"
    >
      <Sun className={`w-4 h-4 absolute transition-all duration-300 ${
        theme === "dark" 
          ? "opacity-0 rotate-90 scale-0" 
          : "opacity-100 rotate-0 scale-100 text-foreground"
      }`} />
      <Moon className={`w-4 h-4 absolute transition-all duration-300 ${
        theme === "dark" 
          ? "opacity-100 rotate-0 scale-100 text-foreground" 
          : "opacity-0 -rotate-90 scale-0"
      }`} />
    </button>
  );
}
