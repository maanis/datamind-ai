import { useEffect, useRef } from "react";
import Prism from "prismjs";

export default function CodeBlock({ code, language }) {
  const ref = useRef();

  useEffect(() => {
    Prism.highlightElement(ref.current);
  }, [code]);

  return (
    <pre className="rounded-xl overflow-x-auto bg-[#0d1117] p-6 text-sm">
      <code ref={ref} className={`language-${language}`}>
        {code}
      </code>
    </pre>
  );
}
