import { ThemeProvider } from "next-themes";
import { Sidebar } from "@/components/layout/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Outlet } from "react-router-dom";

const Index = () => {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <div className="w-full h-screen overflow-hidden ">

        {/* Header with theme toggle */}
        <header className="fixed top-4 right-8 z-10">
          <ThemeToggle />
        </header>

        <main className="w-full gap-7 flex h-full p-5">
          <Sidebar />
          <div className="w-full overflow-y-auto">
            <Outlet />
          </div>
        </main>
      </div>
    </ThemeProvider>
  );
};

export default Index;
