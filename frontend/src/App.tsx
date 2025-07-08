import { Routes, Route, useLocation } from "react-router";
import "./App.css";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFnsV3";
import { LocalizationProvider } from "@mui/x-date-pickers";
import { de } from "date-fns/locale";
import { plPL } from "@mui/x-date-pickers/locales";
import Login from "./components/Login";
import ChangePassword from "./components/ChangePassword";
import Navbar from "./components/navbar/Navbar";
import ProtectedRoutes from "./components/ProtectedRoutes";
import Dashboard from "./components/dashboard/Dashboard";
import Account from "./components/Account";

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
							</Route>
						</Routes>
					}
				/>
			)}
		</LocalizationProvider>
	);
}
