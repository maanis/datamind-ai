import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import "prismjs/themes/prism-tomorrow.css";
import Prism from "prismjs";
import "prismjs/components/prism-json";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-python";


createRoot(document.getElementById("root")!).render(<App />);
