import { useState } from "react";
import { Copy, Check } from "lucide-react";
import CodeBlock from "@/components/CodeBlock";

type CodeTab = "curl" | "javascript" | "python";

const userStr = localStorage.getItem("user");
const user = userStr ? JSON.parse(userStr) : null;
const apiKey = user?.api_key || "YOUR_API_KEY";

const codeExamples: Record<CodeTab, string> = {
  curl: `curl -X POST http://localhost:3000/api/get-answer/${apiKey} \\
  -H "Content-Type: application/json" \\
  -d '{
    "question": "What is the return policy?"
  }'`,

  javascript: `const response = await fetch(
  'http://localhost:3000/api/get-answer/${apiKey}',
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      question: 'What is the return policy?'
    }),
  }
);

const data = await response.json();
console.log(data.answer);
console.log(data.sources);`,

  python: `import requests

response = requests.post(
    'http://localhost:3000/api/get-answer/${apiKey}',
    headers={
        'Content-Type': 'application/json',
    },
    json={
        'question': 'What is the return policy?'
    }
)

data = response.json()
print(data['answer'])
print(data['sources'])`,
};

export function ApiUsageView() {
  const [activeTab, setActiveTab] = useState<CodeTab>("curl");
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(codeExamples[activeTab]);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="animate-fade-in max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground tracking-tight mb-2">
          API Usage
        </h1>
        <p className="text-muted-foreground">
          Learn how to integrate the RAG API into your applications.
        </p>
      </div>

      {/* Endpoint Info */}
      <div className="bg-card rounded-2xl p-6 dark:bg-neutral-950 card-shadow">
        <h3 className="font-medium text-foreground mb-4">Query Endpoint</h3>

        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-secondary/50 dark:bg-neutral-900 border border-border mb-4">
          <span className="px-2 py-1 rounded-md bg-success/10 text-success text-xs font-medium">
            POST
          </span>
          <code className="font-mono text-sm text-foreground">
            /api/get-answer/{"{api_key}"}
          </code>
        </div>

        <p className="text-sm text-muted-foreground">
          Send a query to retrieve relevant chunks and get an AI-generated answer
          with citations.
        </p>
      </div>

      {/* Code Examples */}
      <div className="bg-card dark:bg-neutral-900 rounded-2xl card-shadow overflow-hidden">
        {/* Tabs */}
        <div className="flex items-center dark:bg-neutral-950 gap-1 p-0 border-b border-border bg-secondary/30">
          {(["curl", "javascript", "python"] as CodeTab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 rounded-lg text-sm  font-medium transition-all duration-200 ${activeTab === tab
                ? "bg-card text-foreground nav-shadow"
                : "text-muted-foreground hover:text-foreground"
                }`}
            >
              {tab === "curl"
                ? "cURL"
                : tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}

          <button
            onClick={handleCopy}
            className="ml-auto flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            {copied ? (
              <>
                <Check className="w-4 h-4" />
                Copied
              </>
            ) : (
              <>
                <Copy className="w-4 h-4" />
                Copy
              </>
            )}
          </button>
        </div>

        {/* Code Block */}
        <div className="p-6 overflow-x-auto">
          <CodeBlock code={codeExamples[activeTab]} language={activeTab} />
        </div>
      </div>

      {/* Response Example */}
      <div className="bg-neutral-900 border border-[#30363d] rounded-2xl overflow-hidden shadow-lg">
        <div className="px-6 py-4 border-b border-[#30363d] bg-neutral-950">
          <h3 className="font-medium text-white">Response Example</h3>
        </div>

        <div className="p-6 overflow-x-auto font-mono text-sm leading-relaxed">
          <pre>
            {`{`}
            {"\n  "}
            <span className="text-orange-400">"answer"</span>
            {`: `}
            <span className="text-green-400">"Our return policy allows returns within 30 days..."</span>
            {`,\n  `}
            <span className="text-orange-400">"sources"</span>
            {`: [\n    {`}
            {"\n      "}
            <span className="text-orange-400">"chunk_id"</span>
            {`: `}
            <span className="text-green-400">"chunk_abc123"</span>
            {`,\n      `}
            <span className="text-orange-400">"text"</span>
            {`: `}
            <span className="text-green-400">"Returns are accepted within 30 days of purchase..."</span>
            {`,\n      `}
            <span className="text-orange-400">"score"</span>
            {`: `}
            <span className="text-green-400">0.94</span>
            {`,\n      `}
            {/* <span className="text-orange-400">"metadata"</span>
            {`: { `}
            <span className="text-orange-400">"source"</span>
            {`: `}
            <span className="text-green-400">"return-policy.pdf"</span>
            {`, `}
            <span className="text-orange-400">"page"</span>
            {`: `}
            <span className="text-green-400">1</span>
            {` }\n    }]\n  `}
            <span className="text-orange-400">"confidence"</span>
            {`: `}
            <span className="text-green-400">0.92</span>
            {`,\n  `}
            <span className="text-orange-400">"tokens_used"</span>
            {`: `}
            <span className="text-green-400">245</span>
            {`\n}`} */}
          </pre>
        </div>
      </div>


      {/* Parameters Table */}
      <div className="bg-card rounded-2xl p-6 card-shadow">
        <h3 className="font-medium text-foreground mb-4">Request Parameters</h3>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-3 text-muted-foreground font-medium">
                  Parameter
                </th>
                <th className="text-left py-3 text-muted-foreground font-medium">
                  Type
                </th>
                <th className="text-left py-3 text-muted-foreground font-medium">
                  Required
                </th>
                <th className="text-left py-3 text-muted-foreground font-medium">
                  Description
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              <tr>
                <td className="py-3 font-mono text-foreground">query</td>
                <td className="py-3 text-muted-foreground">string</td>
                <td className="py-3">
                  <span className="text-success">Yes</span>
                </td>
                <td className="py-3 text-muted-foreground">
                  The search query
                </td>
              </tr>
              <tr>
                <td className="py-3 font-mono text-foreground">top_k</td>
                <td className="py-3 text-muted-foreground">integer</td>
                <td className="py-3 text-muted-foreground">No</td>
                <td className="py-3 text-muted-foreground">
                  Number of chunks to retrieve (default: 5)
                </td>
              </tr>
              <tr>
                <td className="py-3 font-mono text-foreground">
                  include_sources
                </td>
                <td className="py-3 text-muted-foreground">boolean</td>
                <td className="py-3 text-muted-foreground">No</td>
                <td className="py-3 text-muted-foreground">
                  Include source chunks in response (default: true)
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
