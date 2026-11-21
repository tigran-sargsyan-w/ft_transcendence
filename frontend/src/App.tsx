import { BrowserRouter, Route, Routes } from "react-router";
import { HomePage } from "./pages/HomePage";
import DashboardLayout from "./layouts/DashboardLayout";
import Dashboard from "./pages/Dashboard";
import RiskMap from "./pages/RiskMap";
import Incidents from "./pages/Incidents";
import Profile from "./pages/Profile";
import Organization from "./pages/Organization";
import Topology from "./pages/Topology";
import { RequireAuth } from "./features/auth/RequireAuth";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route element={<RequireAuth />}>
          <Route element={<DashboardLayout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/topology" element={<Topology />} />
            <Route path="/riskmap" element={<RiskMap />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/organization" element={<Organization />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
