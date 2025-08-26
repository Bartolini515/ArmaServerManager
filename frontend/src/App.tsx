import { Routes, Route, useLocation } from "react-router";
import "./App.css";
import "./index.css";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFnsV3";
import { LocalizationProvider } from "@mui/x-date-pickers";
import { de } from "date-fns/locale";
import { plPL } from "@mui/x-date-pickers/locales";
import Login from "./components/auth/Login";
import ChangePassword from "./components/auth/ChangePassword";
import Navbar from "./components/navbar/Navbar";
import ProtectedRoutes from "./components/ProtectedRoutes";
import Dashboard from "./components/dashboard/Dashboard";
import Account from "./components/account/Account";
import Instances from "./components/instances/Instances";
import ModeratorPanelConfiguration from "./components/moderatorPanel/ModeratorPanelConfiguration";
import ModeratorPanelDataManagement from "./components/moderatorPanel/ModeratorPanelDataManagement";
import Missions from "./components/missions/Missions";

export default function App() {
	const location = useLocation();
	const noNavbar =
		location.pathname === "/" ||
		location.pathname === "/register" ||
		location.pathname === "/change_password";

	return (
		<LocalizationProvider
			dateAdapter={AdapterDateFns}
			adapterLocale={de}
			localeText={
				plPL.components.MuiLocalizationProvider.defaultProps.localeText
			}
		>
			{noNavbar ? (
				<Routes>
					<Route path="/" element={<Login />} />
					<Route path="/change_password" element={<ChangePassword />} />
				</Routes>
			) : (
				<Navbar
					content={
						<Routes>
							<Route element={<ProtectedRoutes />}>
								<Route path="/dashboard" element={<Dashboard />} />
								<Route path="/account" element={<Account />} />
								<Route path="/instances" element={<Instances />} />
								<Route path="/missions" element={<Missions />} />
								<Route
									path="/moderator_panel_data_management"
									element={<ModeratorPanelDataManagement />}
								/>
								<Route
									path="/moderator_panel_config"
									element={<ModeratorPanelConfiguration />}
								/>
							</Route>
						</Routes>
					}
				/>
			)}
		</LocalizationProvider>
	);
}
