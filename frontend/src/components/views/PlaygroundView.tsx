import { useState, useRef, useEffect } from "react";
import {
  Send, Sparkles, Database, Bot, User,
  PanelRightClose, PanelRightOpen, Search,
  Quote, Copy, RefreshCw, ChevronDown, FileText
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { motion, AnimatePresence } from "framer-motion";
import { useWorkspaces, useDocuments } from "@/hooks/useWorkspaces";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  sources?: Source[];
}

interface Source {
  id: string;
  text: string;
  score: number;
  source: string;
}

export function PlaygroundView() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "Hello! I'm ready to answer questions based on your knowledge base.",
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [activeSources, setActiveSources] = useState<Source[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const [isResponding, setIsResponding] = useState(false);
  const [selectedWorkspace, setSelectedWorkspace] = useState("");
  const [selectedDocument, setSelectedDocument] = useState("all");

  const { data: workspaces = [], isLoading: workspacesLoading, error: workspacesError } = useWorkspaces();
  const { data: documents = [], isLoading: documentsLoading } = useDocuments(selectedWorkspace);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { toast } = useToast();

  // --- Initial Data Fetching ---
  useEffect(() => {
    if (workspaces.length > 0 && !selectedWorkspace) {
      setSelectedWorkspace(workspaces[0].workspaceId);
    }
  }, [workspaces, selectedWorkspace]);

  useEffect(() => {
    // Reset selected document when workspace changes
    setSelectedDocument("all");
  }, [selectedWorkspace]);

  console.log(selectedWorkspace)

  useEffect(() => {
    scrollToBottom();
  }, [messages, isResponding]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [input]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const handleSend = async () => {
    if (!input.trim()) return;
    if (!selectedWorkspace) {
      toast({ title: "Select a workspace", variant: "destructive" });
      return;
    }

    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setIsResponding(true);
    setActiveSources([]); // Clear previous sources for new query

    try {
      const response = await fetch(
        `http://localhost:3000/api/robot/get-answer/41435046-f66f-42ec-812d-5bb0933eb36f`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: userMsg.content }),
        }
      );

      if (!response.ok) throw new Error("Failed to fetch response");

      // Handle Streaming
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let aiContent = "";

      const aiMsgId = (Date.now() + 1).toString();

      // Optimistic AI message start
      setMessages((prev) => [
        ...prev,
        { id: aiMsgId, role: "assistant", content: "", timestamp: new Date() }
      ]);

      while (true) {
        const { done, value } = await reader!.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });

        // Check for sources in the chunk (assuming your API might send them as a special event or metadata)
        // For this demo, we'll append text. If you get sources separately, parse them here.

        aiContent += chunk;
        setMessages((prev) => prev.map(m => m.id === aiMsgId ? { ...m, content: aiContent } : m));
      }

      // MOCK: Simulating sources arriving after generation for the UI demo
      // Remove this in production if your API sends real sources
      const mockSources = [
        { id: "s1", source: "Employee_Handbook.pdf", score: 0.92, text: "Employees are entitled to 20 days of paid leave annually..." },
        { id: "s2", source: "HR_Policy_2024.docx", score: 0.85, text: "Remote work is permitted on Tuesdays and Thursdays..." },
      ];
      setActiveSources(mockSources);

    } catch (error: any) {
      toast({ title: "Error", description: error.message, variant: "destructive" });
    } finally {
      setIsResponding(false);
    }
  };

  return (
    <div className="flex h-full max-w-5xl mx-auto overflow-y-hidden w-full bg-background overflow-hidden rounded-3xl border border-border shadow-2xl">

      {/* --- LEFT: Chat Interface --- */}
      <div className="flex-1 flex flex-col min-w-0 bg-background relative z-0">

        {/* Header */}
        <header className="h-16 border-b border-border/50 flex items-center justify-between px-6 bg-background/80 backdrop-blur-sm sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className="relative">
              <select
                value={selectedWorkspace}
                onChange={(e) => setSelectedWorkspace(e.target.value)}
                className="appearance-none pl-9 pr-8 py-2 bg-secondary/50 hover:bg-secondary rounded-xl text-sm font-medium transition-colors cursor-pointer outline-none focus:ring-2 focus:ring-primary/20"
              >
                <option value="" disabled>Select Workspace</option>
                {workspaces.map(w => <option key={w.workspaceId} value={w.workspaceId}>{w.workspaceName}</option>)}
                {workspaces.length === 0 && <option value="demo">Demo Workspace</option>}
              </select>
              <Database className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <ChevronDown className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
            </div>
            <div className="relative">
              <select
                value={selectedDocument}
                onChange={(e) => setSelectedDocument(e.target.value)}
                disabled={!selectedWorkspace || documentsLoading}
                className="appearance-none pl-9 pr-8 py-2 bg-secondary/50 hover:bg-secondary rounded-xl text-sm font-medium transition-colors cursor-pointer outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <option value="all">All Documents</option>
                {documents.map(d => <option key={d._id} value={d._id}>{d.fileName}</option>)}
              </select>
              <FileText className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <ChevronDown className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
            </div>
            <span className="h-4 w-px bg-border/50 mx-1" />
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Bot Active
            </div>
          </div>

          <button
            onClick={() => setShowSidebar(!showSidebar)}
            className="p-2 hover:bg-secondary rounded-lg text-muted-foreground transition-colors"
          >
            {showSidebar ? <PanelRightClose className="w-5 h-5" /> : <PanelRightOpen className="w-5 h-5" />}
          </button>
        </header>

        {/* Messages Area */}
        <div className="flex-1 overflow-y-auto p-4 md:p-6 scroll-smooth">
          <div className="max-w-3xl mx-auto space-y-8 py-4">
            {messages.map((msg, i) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                className={cn("flex gap-4", msg.role === "user" ? "flex-row-reverse" : "flex-row")}
              >
                {/* Avatar */}
                <div className={cn(
                  "w-8 h-8 rounded-full flex items-center justify-center shrink-0 shadow-sm",
                  msg.role === "assistant" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
                )}>
                  {msg.role === "assistant" ? <Bot className="w-5 h-5" /> : <User className="w-5 h-5" />}
                </div>

                {/* Bubble */}
                <div className={cn(
                  "flex flex-col max-w-[80%]",
                  msg.role === "user" ? "items-end" : "items-start"
                )}>
                  <div className={cn(
                    "px-5 py-3.5 rounded-2xl shadow-sm text-sm leading-relaxed whitespace-pre-wrap",
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground rounded-tr-sm"
                      : "bg-card border border-border rounded-tl-sm text-foreground"
                  )}>
                    {msg.content}
                    {msg.content === "" && isResponding && i === messages.length - 1 && (
                      <span className="inline-flex gap-1 items-center ml-2">
                        <span className="w-1.5 h-1.5 bg-current rounded-full animate-bounce [animation-delay:-0.3s]" />
                        <span className="w-1.5 h-1.5 bg-current rounded-full animate-bounce [animation-delay:-0.15s]" />
                        <span className="w-1.5 h-1.5 bg-current rounded-full animate-bounce" />
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] text-muted-foreground mt-2 px-1 select-none">
                    {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </div>
              </motion.div>
            ))}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        </div>

        {/* Input Area */}
        <div className="p-6 pt-2 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky bottom-0 z-20">
          <div className="max-w-3xl mx-auto relative">

            <div className="relative bg-card border border-border rounded-2xl flex items-end p-2 transition-all duration-200 
                    focus-within:border-primary 
                    focus-within:ring-2 
                    focus-within:ring-primary/10 
                    focus-within:bg-muted/20">

              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="Message..."
                rows={1}
                className="flex-1 max-h-48 min-h-[50px] bg-transparent border-none focus:outline-none resize-none py-3 px-4 text-sm leading-relaxed placeholder:text-muted-foreground/50 scrollbar-hide"
              />

              <button
                onClick={handleSend}
                disabled={!input.trim() || isResponding}
                className="mb-1.5 mr-1.5 p-2 rounded-xl bg-primary text-primary-foreground 
                   hover:opacity-90 
                   disabled:opacity-50 
                   disabled:cursor-not-allowed 
                   transition-all 
                   active:scale-95"
              >
                {isResponding ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
              </button>

            </div>

            <p className="text-[10px] text-muted-foreground text-center mt-3">
              AI can make mistakes. Please check important information.
            </p>

          </div>
        </div>

      </div>

      {/* --- RIGHT: Knowledge / Sources Panel --- */}
      <AnimatePresence initial={false}>
        {showSidebar && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 320, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="border-l border-border bg-muted/10 h-full overflow-hidden flex flex-col shadow-inner"
          >
            <div className="h-16 flex items-center px-5 border-b border-border/50 bg-muted/30">
              <h3 className="font-semibold text-sm flex items-center gap-2 text-foreground">
                <Sparkles className="w-4 h-4 text-amber-500" />
                Citations & Sources
              </h3>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {activeSources.length > 0 ? (
                activeSources.map((source, idx) => (
                  <motion.div
                    initial={{ opacity: 0, x: 20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: idx * 0.1 }}
                    key={source.id}
                    className="bg-card border border-border/50 rounded-xl p-4 shadow-sm hover:shadow-md transition-all group"
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <div className="p-1.5 bg-primary/10 rounded-md text-primary">
                          <Quote className="w-3 h-3" />
                        </div>
                        <span className="text-xs font-semibold text-foreground truncate max-w-[150px]" title={source.source}>
                          {source.source}
                        </span>
                      </div>
                      <span className={cn(
                        "text-[10px] font-medium px-2 py-0.5 rounded-full",
                        source.score > 0.8 ? "bg-emerald-500/10 text-emerald-600" : "bg-amber-500/10 text-amber-600"
                      )}>
                        {Math.round(source.score * 100)}% match
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground leading-relaxed line-clamp-4 font-mono bg-muted/30 p-2 rounded-lg border border-border/30">
                      "{source.text}"
                    </p>

                    <div className="mt-3 flex justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        className="text-[10px] flex items-center gap-1 text-primary hover:underline"
                        onClick={() => {
                          navigator.clipboard.writeText(source.text);
                          toast({ title: "Copied citation" });
                        }}
                      >
                        <Copy className="w-3 h-3" /> Copy Text
                      </button>
                    </div>
                  </motion.div>
                ))
              ) : (
                <div className="h-64 flex flex-col items-center justify-center text-center p-4">
                  <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center mb-3">
                    <Search className="w-5 h-5 text-muted-foreground" />
                  </div>
                  <p className="text-sm font-medium text-foreground">No sources yet</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Ask a question to see which documents the AI uses to generate the answer.
                  </p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}