import { useEffect, useState } from "react";
import { Alert, Box, Button, CircularProgress, Typography } from "@mui/material";
import { Outlet, Navigate } from "react-router";
import { useAuth, type UserType } from "../contexts/AuthContext";
import { AuthSessionBootstrap } from "../contexts/authSession";
import AxiosInstance from "./AxiosInstance";

interface AccountResponse {
	user: UserType;
	isAdmin: boolean;
}

type BootstrapStatus = "loading" | "ready" | "error";

interface BootstrapState {
	token: string | null;
	status: BootstrapStatus;
}

export default function ProtectedRoutes() {
	const { token, setToken, setUser, setIsAdmin } = useAuth();
	const [attempt, setAttempt] = useState(0);
	const [bootstrapState, setBootstrapState] = useState<BootstrapState>({
		token: null,
		status: "loading",
	});
	const [bootstrap] = useState(
		() => new AuthSessionBootstrap<AccountResponse>(),
	);

	useEffect(() => {
		if (!token) {
			setUser(null);
			setIsAdmin(false);
			return;
		}

		let active = true;
		setUser(null);
		setIsAdmin(false);
		setBootstrapState({ token, status: "loading" });

		void bootstrap
			.load(
				token,
				() => localStorage.getItem("Token"),
				() =>
					AxiosInstance.get<AccountResponse>("account/get_user/").then(
						(response) => response.data,
					),
			)
			.then((result) => {
				if (!active) {
					return;
				}
				if (result.kind === "stale") {
					const storedToken = localStorage.getItem("Token");
					if (storedToken !== token) {
						setToken(storedToken);
					} else {
						setBootstrapState({ token, status: "error" });
					}
					return;
				}

				if (result.kind === "invalid") {
					setToken(null);
					return;
				}

				if (result.kind === "retryable") {
					setBootstrapState({ token, status: "error" });
					return;
				}

				const verifiedUser = { ...result.identity.user };
				if (verifiedUser.profile_picture) {
					const baseURL = AxiosInstance.defaults.baseURL?.replace("/api", "");
					verifiedUser.profile_picture = `${baseURL}${verifiedUser.profile_picture}`;
				}
				setUser(verifiedUser);
				setIsAdmin(result.identity.isAdmin);
				setBootstrapState({ token, status: "ready" });
			});

		return () => {
			active = false;
		};
	}, [attempt, bootstrap, setIsAdmin, setToken, setUser, token]);

	if (!token) {
		return <Navigate to="/" replace />;
	}

	if (bootstrapState.token !== token || bootstrapState.status === "loading") {
		return (
			<Box
				role="status"
				sx={{
					display: "flex",
					flexDirection: "column",
					alignItems: "center",
					gap: 2,
					p: 4,
				}}
			>
				<CircularProgress />
				<Typography>Sprawdzanie sesji...</Typography>
			</Box>
		);
	}

	if (bootstrapState.status === "error") {
		return (
			<Box sx={{ maxWidth: 600, mx: "auto", p: 3 }}>
				<Alert
					severity="error"
					action={
						<Button
							color="inherit"
							size="small"
							onClick={() => {
								setBootstrapState({ token, status: "loading" });
								setAttempt((current) => current + 1);
							}}
						>
							Spróbuj ponownie
						</Button>
					}
				>
					Nie udało się sprawdzić sesji. Usługa może być chwilowo niedostępna.
					Sesja została zachowana.
				</Alert>
			</Box>
		);
	}

	return <Outlet />;
}
