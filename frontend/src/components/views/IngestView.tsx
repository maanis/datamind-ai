import { useState, useEffect } from "react";
import {
  Upload, FileText, Check, Loader2, File, X, Code,
  FileSpreadsheet, Eye, EyeOff, Database, ChevronDown, Sparkles
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import * as XLSX from 'xlsx';
import { useWorkspaces } from "@/hooks/useWorkspaces";

type IngestMode = "text" | "json" | "excel" | "upload";
type IngestStep = "idle" | "uploaded" | "chunked" | "embedded" | "indexed";

const steps: { id: IngestStep; label: string }[] = [
  { id: "uploaded", label: "Uploaded" },
  { id: "chunked", label: "Chunked" },
  { id: "embedded", label: "Embedded" },
  { id: "indexed", label: "Indexed" },
];

export function IngestView() {
  const [mode, setMode] = useState<IngestMode>("text");
  const [currentStep, setCurrentStep] = useState<IngestStep>("idle");
  const [chunksCreated, setChunksCreated] = useState(0);
  const [rawText, setRawText] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<{ name: string; size: string; file: File } | null>(null);
  const [isIngesting, setIsIngesting] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [previewData, setPreviewData] = useState<{ headers: string[]; rows: any[][]; sheetName: string } | null>(null);
  const [workspaces, setWorkspaces] = useState<any[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState("");

  // Use TanStack Query hook
  const { data: workspacesData = [], isLoading: workspacesLoading, error: workspacesError } = useWorkspaces();

  // Show error toast if workspaces fetch fails
  useEffect(() => {
    if (workspacesError) {
      toast.error("Failed to fetch workspaces");
    }
  }, [workspacesError]);

  // Set selected workspace when data loads
  useEffect(() => {
    if (workspacesData.length > 0 && !selectedWorkspace) {
      setSelectedWorkspace(workspacesData[0].workspaceId);
    }
  }, [workspacesData, selectedWorkspace]);

  const fetchWorkspaces = async () => {
    try {
      const response = await fetch('http://localhost:3000/api/users/workspaces', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
      });
      if (response.ok) {
        const data = await response.json();
        setWorkspaces(data.workspaces);
        if (data.workspaces.length > 0 && !selectedWorkspace) {
          setSelectedWorkspace(data.workspaces[0].workspaceId);
        }
      }
    } catch (error) {
      console.error('Failed to fetch workspaces:', error);
    }
  };

  const readExcelFile = async (file: File) => {
    try {
      const data = await file.arrayBuffer();
      const workbook = XLSX.read(data, { type: 'array' });

      const sheetName = workbook.SheetNames[0];
      const worksheet = workbook.Sheets[sheetName];
      const jsonData = XLSX.utils.sheet_to_json(worksheet, { header: 1, defval: '' });

      if (jsonData.length === 0) {
        toast.error("The file appears to be empty");
        return;
      }

      const headers = (jsonData[0] as any[]).map((header, index) =>
        header?.toString().trim() || `Column ${index + 1}`
      );
      const rows = jsonData.slice(1, 11).map(row =>
        row.map(cell => {
          if (typeof cell === 'number' && cell < 1) {
            const date = new Date((cell * 24 * 60 * 60 * 1000));
            const hours = date.getUTCHours().toString().padStart(2, '0');
            const minutes = date.getUTCMinutes().toString().padStart(2, '0');
            return `${hours}:${minutes}`;
          }
          return cell;
        })
      );

      setPreviewData({
        headers,
        rows,
        sheetName: file.name.endsWith('.csv') ? 'CSV Data' : sheetName
      });
      setShowPreview(true);
    } catch (error) {
      console.error("Error reading file:", error);
      toast.error("Failed to read the file. Please check the file format.");
      setUploadedFile(null);
    }
  };

  const handleIngest = async () => {
    if (!selectedWorkspace) {
      toast.error("Please select a workspace");
      return;
    }

    let sanitizedText = "";

    if (mode === "text") {
      if (!rawText.trim()) {
        toast.error("Please enter some text to ingest");
        return;
      }
      sanitizedText = rawText.replace(/\s+/g, ' ').replace(/\n\s*\n/g, '\n').trim();
    } else if (mode === "json") {
      if (!jsonText.trim()) {
        toast.error("Please enter JSON data to ingest");
        return;
      }
      try {
        JSON.parse(jsonText);
        sanitizedText = jsonText.trim();
      } catch (e: any) {
        toast.error("Invalid JSON format: " + e.message);
        return;
      }
    } else if (mode === "upload") {
      toast.error("PDF upload functionality is not yet implemented");
      return;
    } else if (mode === "excel") {
      if (!uploadedFile?.file) {
        toast.error("Please select an Excel or CSV file to ingest");
        return;
      }
    }

    setIsIngesting(true);
    setCurrentStep("uploaded");

    try {
      let response;

      if (mode === "excel") {
        const formData = new FormData();
        formData.append('file', uploadedFile!.file);
        formData.append('workspaceId', selectedWorkspace);

        response = await fetch('http://localhost:3000/api/robot/ingest-file', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
          },
          body: formData,
        });
      } else {
        response = await fetch('http://localhost:3000/api/robot/ingest', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`,
          },
          body: JSON.stringify({ text: sanitizedText, input_type: mode === 'json' ? 'json' : 'text', workspaceId: selectedWorkspace }),
        });
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(`${errorData.message || `HTTP ${response.status}`}`);
      }

      const result = await response.json();

      setTimeout(() => setCurrentStep("chunked"), 500);
      setTimeout(() => setCurrentStep("embedded"), 1000);
      setTimeout(() => {
        setCurrentStep("indexed");
        setChunksCreated(result.num_vectors || 0);
      }, 1500);

      setTimeout(() => {
        if (mode === "text") setRawText("");
        else if (mode === "json") setJsonText("");
        else if (mode === "excel") setUploadedFile(null);

        toast.success(`Successfully ingested ${result.num_documents || 1} document(s)`);
      }, 2000);

    } catch (error: any) {
      console.error("Ingestion error:", error);
      toast.error(error.message || "Failed to ingest data");
      setCurrentStep("idle");
    } finally {
      setIsIngesting(false);
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) {
      setUploadedFile({
        name: file.name,
        size: `${(file.size / 1024).toFixed(1)} KB`,
        file: file,
      });

      if (mode === "excel" && (file.name.endsWith('.xlsx') || file.name.endsWith('.xls') || file.name.endsWith('.csv'))) {
        readExcelFile(file);
      }
    }
  };

  const getStepStatus = (step: IngestStep) => {
    const stepOrder = steps.findIndex((s) => s.id === step);
    const currentOrder = steps.findIndex((s) => s.id === currentStep);

    if (currentStep === "idle") return "pending";
    if (stepOrder < currentOrder) return "complete";
    if (stepOrder === currentOrder) return currentStep === "indexed" ? "complete" : "active";
    return "pending";
  };

  const clearFile = () => {
    setUploadedFile(null);
    setCurrentStep("idle");
    setShowPreview(false);
    setPreviewData(null);
  };

  return (
    <div className="max-w-4xl mx-auto animate-fade-in space-y-8 pb-12">
      {/* Header Section */}
      <div className="flex items-center gap-4">
        <div className="p-3 bg-foreground/5 rounded-2xl border border-border/50 shadow-sm">
          <Database className="w-6 h-6 text-foreground" />
        </div>
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Knowledge Base
          </h1>
          <p className="text-muted-foreground mt-1">
            Upload and process documents to train your assistant.
          </p>
        </div>
      </div>

      {/* Main Container */}
      <div className="bg-card border border-border/50 rounded-3xl p-2 shadow-sm">
        <div className="bg-background rounded-[22px] p-6 sm:p-8 space-y-8">

          {/* Workspace Selector */}
          <div className="space-y-3">
            <label className="text-sm font-semibold text-foreground flex items-center gap-2">
              Target Workspace
            </label>
            <div className="relative group">
              <select
                value={selectedWorkspace}
                onChange={(e) => setSelectedWorkspace(e.target.value)}
                className="w-full appearance-none px-4 py-3.5 pr-10 rounded-xl bg-secondary/30 border border-border/60 text-sm text-foreground hover:border-foreground/30 focus:outline-none focus:ring-4 focus:ring-foreground/5 focus:border-foreground/40 transition-all cursor-pointer"
              >
                <option value="" disabled>Select a workspace to ingest into...</option>
                {workspacesData.map((workspace) => (
                  <option key={workspace.workspaceId || 'unknown'} value={workspace.workspaceId || 'unknown'}>
                    {workspace.workspaceName || 'Unnamed Workspace'}
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none group-hover:text-foreground transition-colors" />
            </div>
            {workspacesData.length === 0 && (
              <p className="text-destructive text-sm mt-2 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-destructive" />
                No workspaces available. Please create one first.
              </p>
            )}
          </div>

          {/* Mode Selector (Tabs) */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 p-1.5 bg-secondary/40 rounded-2xl border border-border/40">
            {[
              { id: "text", icon: FileText, label: "Text" },
              { id: "json", icon: Code, label: "JSON" },
              { id: "excel", icon: FileSpreadsheet, label: "Data Table" },
              { id: "upload", icon: Upload, label: "PDF Document" }
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => {
                  setMode(tab.id as IngestMode);
                  clearFile();
                }}
                className={cn(
                  "flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-300",
                  mode === tab.id
                    ? "bg-background text-foreground shadow-sm ring-1 ring-border/50"
                    : "text-muted-foreground hover:text-foreground hover:bg-background/50"
                )}
              >
                <tab.icon className={cn("w-4 h-4", mode === tab.id ? "text-foreground" : "text-muted-foreground")} />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content Area */}
          <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">

            {/* Text & JSON Inputs */}
            {(mode === "text" || mode === "json") && (
              <div className="space-y-4">
                <div className="relative group">
                  <textarea
                    value={mode === "text" ? rawText : jsonText}
                    onChange={(e) => mode === "text" ? setRawText(e.target.value) : setJsonText(e.target.value)}
                    placeholder={mode === "text" ? "Paste your plain text content here..." : '[\n  {\n    "key": "value"\n  }\n]'}
                    rows={8}
                    className="w-full px-5 py-4 rounded-2xl bg-secondary/20 border border-border/60 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-4 focus:ring-foreground/5 focus:border-foreground/40 transition-all resize-none font-mono leading-relaxed"
                  />
                  <div className="absolute bottom-4 right-4 text-xs font-mono text-muted-foreground/50 pointer-events-none">
                    {mode.toUpperCase()}
                  </div>
                </div>
              </div>
            )}

            {/* File Dropzone */}
            {(mode === "excel" || mode === "upload") && (
              <div className="space-y-4">
                <div
                  className={cn(
                    "relative overflow-hidden border-2 border-dashed rounded-2xl transition-all duration-300 ease-in-out",
                    isDragging
                      ? "border-foreground/50 bg-foreground/5 scale-[1.01]"
                      : "border-border/60 bg-secondary/10 hover:border-foreground/30 hover:bg-secondary/20",
                    uploadedFile ? "p-6" : "p-12"
                  )}
                  onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={handleFileDrop}
                >
                  {uploadedFile ? (
                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                      <div className="flex items-center gap-4">
                        <div className="p-3 bg-background rounded-xl shadow-sm border border-border/50">
                          {mode === "excel" ? <FileSpreadsheet className="w-6 h-6 text-emerald-500" /> : <File className="w-6 h-6 text-red-500" />}
                        </div>
                        <div>
                          <p className="font-semibold text-foreground truncate max-w-[200px] sm:max-w-md">{uploadedFile.name}</p>
                          <p className="text-xs text-muted-foreground font-medium mt-0.5">{uploadedFile.size}</p>
                        </div>
                      </div>
                      <button
                        onClick={clearFile}
                        className="p-2 rounded-full hover:bg-destructive/10 hover:text-destructive text-muted-foreground transition-colors self-end sm:self-auto"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <div className="text-center">
                      <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-background flex items-center justify-center shadow-sm border border-border/50 group-hover:scale-110 transition-transform duration-300">
                        {mode === "excel" ? <FileSpreadsheet className="w-6 h-6 text-foreground/60" /> : <Upload className="w-6 h-6 text-foreground/60" />}
                      </div>
                      <p className="font-semibold text-foreground mb-1.5">
                        Drag and drop your {mode === "excel" ? "Excel or CSV" : "PDF"} file
                      </p>
                      <p className="text-sm text-muted-foreground mb-6">
                        or click below to browse your computer
                      </p>
                      <input
                        type="file"
                        accept={mode === "excel" ? ".xlsx,.xls,.csv" : ".pdf"}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) {
                            setUploadedFile({ name: file.name, size: `${(file.size / 1024).toFixed(1)} KB`, file: file });
                            if (mode === "excel") readExcelFile(file);
                          }
                        }}
                        className="hidden"
                        id="file-upload"
                      />
                      <label
                        htmlFor="file-upload"
                        className="inline-flex items-center justify-center px-6 py-2.5 rounded-xl bg-background border border-border/60 text-foreground font-medium text-sm hover:bg-secondary transition-colors cursor-pointer shadow-sm hover:shadow"
                      >
                        Browse Files
                      </label>
                    </div>
                  )}
                </div>

                {/* Modern Data Table Preview */}
                {mode === "excel" && showPreview && previewData && (
                  <div className="mt-6 rounded-2xl border border-border/60 bg-background overflow-hidden shadow-sm animate-in fade-in slide-in-from-bottom-2">
                    <div className="flex items-center justify-between p-4 border-b border-border/60 bg-secondary/20">
                      <div className="flex items-center gap-2">
                        <Eye className="w-4 h-4 text-muted-foreground" />
                        <h4 className="text-sm font-semibold text-foreground">
                          Preview: <span className="text-muted-foreground font-normal">{previewData.sheetName}</span>
                        </h4>
                      </div>
                      <button
                        onClick={() => setShowPreview(false)}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors text-muted-foreground"
                      >
                        Hide Preview
                      </button>
                    </div>

                    <div className="overflow-auto max-h-[300px] w-full custom-scrollbar">
                      <table className="w-full text-sm text-left whitespace-nowrap">
                        <thead className="text-xs text-muted-foreground uppercase bg-secondary/30 sticky top-0 backdrop-blur-sm">
                          <tr>
                            {previewData.headers.map((header, index) => (
                              <th key={index} className="px-4 py-3 font-semibold border-b border-border/60">
                                {header || `Col ${index + 1}`}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border/40">
                          {previewData.rows.map((row, rowIndex) => (
                            <tr key={rowIndex} className="hover:bg-secondary/20 transition-colors">
                              {previewData.headers.map((_, colIndex) => (
                                <td key={colIndex} className="px-4 py-2.5 text-foreground/80">
                                  {row[colIndex] !== undefined ? String(row[colIndex]) : <span className="text-muted-foreground/30">-</span>}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {previewData.rows.length >= 10 && (
                      <div className="p-3 text-xs text-center text-muted-foreground bg-secondary/10 border-t border-border/40">
                        Showing sample of 10 rows. Full file will be processed.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Submit Action */}
            <div className="mt-8">
              <button
                onClick={handleIngest}
                disabled={isIngesting || (!rawText && mode === 'text') || (!jsonText && mode === 'json') || (!uploadedFile && (mode === 'excel' || mode === 'upload'))}
                className="w-full py-3.5 rounded-xl bg-foreground text-background font-semibold text-sm hover:bg-foreground/90 transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-md hover:shadow-lg active:scale-[0.99] flex items-center justify-center gap-2"
              >
                {isIngesting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Processing Data...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    Start Ingestion
                  </>
                )}
              </button>
            </div>
          </div>

        </div>
      </div>

      {/* Progress Tracker Tracker */}
      {currentStep !== "idle" && (
        <div className="bg-card border border-border/50 rounded-3xl p-6 sm:p-8 shadow-sm animate-in fade-in slide-in-from-bottom-4">
          <h3 className="text-sm font-semibold text-foreground mb-8">Processing Status</h3>

          <div className="relative flex justify-between">
            {/* Background Line */}
            <div className="absolute top-5 left-6 right-6 h-0.5 bg-secondary -z-10" />

            {steps.map((step, index) => {
              const status = getStepStatus(step.id);
              const isComplete = status === "complete";
              const isActive = status === "active";

              return (
                <div key={step.id} className="flex flex-col items-center relative z-10 w-24">
                  <div
                    className={cn(
                      "w-10 h-10 rounded-full flex items-center justify-center transition-all duration-500 shadow-sm border-2",
                      isComplete
                        ? "bg-foreground border-foreground text-background"
                        : isActive
                          ? "bg-background border-foreground text-foreground ring-4 ring-foreground/10"
                          : "bg-background border-border text-muted-foreground"
                    )}
                  >
                    {isComplete ? (
                      <Check className="w-5 h-5 animate-in zoom-in duration-300" />
                    ) : isActive ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      <span className="text-sm font-medium">{index + 1}</span>
                    )}
                  </div>
                  <span
                    className={cn(
                      "text-xs mt-3 font-semibold text-center transition-colors duration-300",
                      isComplete || isActive ? "text-foreground" : "text-muted-foreground"
                    )}
                  >
                    {step.label}
                  </span>
                </div>
              );
            })}
          </div>

          {currentStep === "indexed" && (
            <div className="mt-8 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center gap-3 text-emerald-600 dark:text-emerald-400 animate-in fade-in slide-in-from-bottom-2">
              <Check className="w-5 h-5" />
              <p className="text-sm font-medium">
                Success! System processed and indexed {chunksCreated} document chunks into the workspace.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}