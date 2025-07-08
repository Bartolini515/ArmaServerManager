import { Outlet, Navigate } from "react-router";
import { useAuth } from "../contexts/AuthContext";
import AxiosInstance from "./AxiosInstance";

export default function ProtectedRoutes() {
	const token = localStorage.getItem("Token");
	const { user, setUser, setIsAdmin } = useAuth();

	if (!token) {
		return <Navigate to="/" />;
	}

	if (!user) {
		AxiosInstance.get("account/getuser/")
			.then((response) => {
				if (response.data.user.profile_picture) {
					const baseURL = AxiosInstance.defaults.baseURL?.replace("/api", "");
					response.data.user.profile_picture = `${baseURL}${response.data.user.profile_picture}`;
				}
				setUser(response.data.user);
				setIsAdmin(response.data.isAdmin);
			})
			.catch((error: any) => {
				console.log(error);
				localStorage.removeItem("Token");
				return <Navigate to="/" />;
			});
	}

	return <Outlet />;
}
