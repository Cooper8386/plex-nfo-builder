import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import "./index.css";

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <ConfirmProvider>
        <App />
      </ConfirmProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
