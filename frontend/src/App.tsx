import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Keywords from "./pages/Keywords";
import Scoring from "./pages/Scoring";
import Channels from "./pages/Channels";
import Generation from "./pages/Generation";
import Tasks from "./pages/Tasks";
import Export from "./pages/Export";
import BrandProfile from "./pages/BrandProfile";
import Relevance from "./pages/Relevance";

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/brand-profile" element={<BrandProfile />} />
          <Route path="/keywords" element={<Keywords />} />
          <Route path="/scoring" element={<Scoring />} />
          <Route path="/relevance" element={<Relevance />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/generation" element={<Generation />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/export" element={<Export />} />
          <Route
            path="/google-ads"
            element={<Navigate to="/keywords?tab=google-ads" replace />}
          />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
