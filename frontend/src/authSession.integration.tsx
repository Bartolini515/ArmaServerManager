import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import {
	AxiosError,
	type AxiosAdapter,
	type AxiosResponse,
	type InternalAxiosRequestConfig,
} from "axios";
import { Button, Paper, Stack, Typography } from "@mui/material";
import App from "./App";
import AxiosInstance from "./components/AxiosInstance";
import { AlertProvider } from "./contexts/AlertContext";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { CustomThemeProvider } from "./contexts/ThemeContext";
import {
	AUTH_INTEGRATION_FIXTURE_ORIGIN,
	canRunAuthIntegrationFixture,
	getKnoxToken,
	isAuthIntegrationFixtureToken,
} from "./contexts/authSession";

interface MockRequest {
	url: string;
	token: string | null;
	authorizationPresent: boolean;
}

interface PendingRequest {
	kind: "identity" | "ordinary" | "public-login";
	token: string | null;
	resolve: () => void;
	reject: () => void;
}

interface FixtureState {
	requests: MockRequest[];
	pending: PendingRequest[];
	failNextIdentity: number;
	delayedIdentityTokens: Set<string>;
}

const fixtureState: FixtureState = {
	requests: [],
	pending: [],
	failNextIdentity: 0,
	delayedIdentityTokens: new Set(),
};

function notifyFixture(): void {
	window.dispatchEvent(new Event("auth-fixture-update"));
}

function response(
	config: InternalAxiosRequestConfig,
	data: unknown,
	status = 200,
): AxiosResponse<unknown> {
	return {
		data,
		status,
		statusText: String(status),
		headers: {},
		config,
	};
}

function errorResponse(
	config: InternalAxiosRequestConfig,
	status: number,
): AxiosError<unknown> {
	return new AxiosError(
		"Synthetic HTTP " + status,
		status === 401 ? "ERR_BAD_REQUEST" : "ERR_BAD_RESPONSE",
		config,
		undefined,
		response(config, { detail: "Synthetic HTTP " + status }, status),
	);
}

function identityForToken(token: string | null) {
	const isAdmin = token === "token-admin";
	const username = token?.replace(/^token-/, "") || "synthetic-user";
	return {
		user: {
			id: isAdmin ? 1 : 2,
			username,
			last_login: "2026-01-01T00:00:00Z",
			profile_picture: null,
		},
		isAdmin,
	};
}

function enqueuePending(
	config: InternalAxiosRequestConfig,
	kind: PendingRequest["kind"],
	token: string | null,
	resolve: (result: AxiosResponse<unknown>) => void,
	reject: (error: AxiosError<unknown>) => void,
): void {
	fixtureState.pending.push({
		kind,
		token,
		resolve: () => resolve(response(config, identityForToken(token))),
		reject: () => reject(errorResponse(config, 401)),
	});
	notifyFixture();
}

function parseBody(value: unknown): Record<string, unknown> {
	if (typeof value === "string") {
		const parsed: unknown = JSON.parse(value);
		return typeof parsed === "object" && parsed !== null
			? (parsed as Record<string, unknown>)
			: {};
	}
	return typeof value === "object" && value !== null
		? (value as Record<string, unknown>)
		: {};
}

const syntheticAdapter: AxiosAdapter = (config) => {
	const url = (config.url ?? "").replace(/^\/+/, "");
	const authorization = config.headers.get("Authorization");
	const token = getKnoxToken(authorization);
	fixtureState.requests.push({
		url,
		token,
		authorizationPresent: config.headers.has("Authorization"),
	});
	notifyFixture();

	if (url === "account/get_user/") {
		if (fixtureState.failNextIdentity > 0) {
			fixtureState.failNextIdentity -= 1;
			notifyFixture();
			return Promise.reject(errorResponse(config, 503));
		}
		if (token && fixtureState.delayedIdentityTokens.has(token)) {
			fixtureState.delayedIdentityTokens.delete(token);
			return new Promise((resolve, reject) => {
				enqueuePending(config, "identity", token, resolve, reject);
			});
		}
		return Promise.resolve(response(config, identityForToken(token)));
	}

	if (url === "login/") {
		const username = parseBody(config.data).username;
		if (username === "fixture-deferred-failure") {
			return new Promise((resolve, reject) => {
				enqueuePending(config, "public-login", token, resolve, reject);
			});
		}
		const loginName = typeof username === "string" ? username : "synthetic-user";
		const loginToken =
			loginName === "admin" ? "token-admin" : "token-" + loginName;
		const identity = identityForToken(loginToken);
		return Promise.resolve(
			response(config, {
				token: loginToken,
				...identity,
				message: "Synthetic login succeeded",
			}),
		);
	}

	if (url === "logout/") {
		return Promise.resolve(response(config, { message: "Synthetic logout succeeded" }));
	}

	if (url === "fixture/ordinary-401/") {
		return new Promise((resolve, reject) => {
			enqueuePending(config, "ordinary", token, resolve, reject);
		});
	}

	if (url === "instances/") {
		return Promise.resolve(
			response(config, { user_instances: [], admin_instances: [] }),
		);
	}

	if (url === "services/get_system_info/") {
		return Promise.resolve(
			response(config, {
				cpuUsage: 10,
				memoryLeft: 512,
				memoryTotal: 1024,
				spaceLeft: 1000,
				spaceTotal: 2000,
				cpuCount: 4,
				osName: "Synthetic",
			}),
		);
	}

	return Promise.resolve(response(config, {}));
};

function AuthStateProbe() {
	const { token, user, isAdmin } = useAuth();
	return (
		<Typography
			role="status"
			data-testid="auth-state-probe"
			sx={{ position: "fixed", top: 72, right: 8, zIndex: 1600 }}
		>
			Session probe: {token ?? "none"}; user {user?.username ?? "pending"}; admin{" "}
			{String(isAdmin)}
		</Typography>
	);
}

function FixtureControls() {
	const [, setRevision] = useState(0);
	useEffect(() => {
		const refresh = () => setRevision((value) => value + 1);
		window.addEventListener("auth-fixture-update", refresh);
		window.addEventListener("storage", refresh);
		return () => {
			window.removeEventListener("auth-fixture-update", refresh);
			window.removeEventListener("storage", refresh);
		};
	}, []);

	const postToSecondary = (token: string | null) => {
		const frame = document.getElementById(
			"secondary-storage-context",
		) as HTMLIFrameElement | null;
		frame?.contentWindow?.postMessage(
			{ type: "set-token", token },
			window.location.origin,
		);
	};
	const pendingCount = (kind: PendingRequest["kind"]) =>
		fixtureState.pending.filter((request) => request.kind === kind).length;
	const lastLoginRequest = [...fixtureState.requests]
		.reverse()
		.find((request) => request.url === "login/");
	const settlePending = (
		kind: PendingRequest["kind"],
		outcome: "resolve" | "reject",
	) => {
		const requests = fixtureState.pending.filter((request) => request.kind === kind);
		fixtureState.pending = fixtureState.pending.filter(
			(request) => request.kind !== kind,
		);
		requests.forEach((request) => request[outcome]());
		notifyFixture();
	};

	return (
		<Paper
			component="aside"
			aria-label="Local auth test controls"
			elevation={8}
			sx={{
				position: "fixed",
				bottom: 8,
				left: 8,
				zIndex: 1600,
				width: "min(430px, calc(100vw - 16px))",
				maxHeight: 260,
				overflow: "auto",
				p: 1.5,
			}}
		>
			<Typography variant="subtitle2">Local auth test controls</Typography>
			<Typography variant="body2" data-testid="mock-request-stats">
				Synthetic identity calls:{" "}
				{fixtureState.requests.filter((request) => request.url === "account/get_user/")
					.length}
				; pending identity {pendingCount("identity")}; pending ordinary 401{" "}
				{pendingCount("ordinary")}; public 401 {pendingCount("public-login")};
				stored token {localStorage.getItem("Token") ?? "none"}; latest login Authorization{" "}
				{lastLoginRequest?.authorizationPresent ? "present" : "omitted"}
			</Typography>
			<Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 1 }}>
				<Button size="small" onClick={() => settlePending("identity", "resolve")}>
					Release pending identity
				</Button>
				<Button size="small" onClick={() => settlePending("identity", "reject")}>
					Reject pending identity 401
				</Button>
				<Button
					size="small"
					onClick={() => {
						fixtureState.failNextIdentity += 1;
						notifyFixture();
					}}
				>
					Fail next identity 503
				</Button>
				<Button size="small" onClick={() => postToSecondary("token-user")}>
					Other context: user token
				</Button>
				<Button size="small" onClick={() => postToSecondary("token-admin")}>
					Other context: admin token
				</Button>
				<Button size="small" onClick={() => postToSecondary(null)}>
					Other context: logout
				</Button>
				<Button
					size="small"
					onClick={() => {
						void AxiosInstance.get("fixture/ordinary-401/").catch(() => undefined);
					}}
				>
					Start ordinary 401
				</Button>
				<Button size="small" onClick={() => settlePending("ordinary", "reject")}>
					Reject ordinary 401
				</Button>
				<Button
					size="small"
					onClick={() => {
						void AxiosInstance.post("login/", {
							username: "fixture-deferred-failure",
							password: "synthetic",
						}).catch(() => undefined);
					}}
				>
					Start public login 401
				</Button>
				<Button
					size="small"
					onClick={() => settlePending("public-login", "reject")}
				>
					Reject public login 401
				</Button>
			</Stack>
			<iframe
				id="secondary-storage-context"
				title="Secondary local storage context"
				src="/auth-session.integration.html?secondary=1"
				width="1"
				height="1"
				aria-hidden="true"
			/>
		</Paper>
	);
}

function SecondaryStorageContext() {
	useEffect(() => {
		const handleMessage = (event: MessageEvent<unknown>) => {
			if (event.origin !== window.location.origin) {
				return;
			}
			if (
				typeof event.data !== "object" ||
				event.data === null ||
				(event.data as { type?: unknown }).type !== "set-token"
			) {
				return;
			}
			const token = (event.data as { token?: unknown }).token;
			const nextToken = typeof token === "string" && token ? token : null;
			if (
				!canRunAuthIntegrationFixture(
					import.meta.env.DEV,
					window.location.origin,
					localStorage.getItem("Token"),
					nextToken,
				) ||
				!isAuthIntegrationFixtureToken(nextToken)
			) {
				return;
			}
			if (typeof token === "string" && token) {
				localStorage.setItem("Token", token);
			} else {
				localStorage.removeItem("Token");
			}
		};
		window.addEventListener("message", handleMessage);
		return () => window.removeEventListener("message", handleMessage);
	}, []);

	return <Typography>Secondary storage context ready</Typography>;
}

export function AuthSessionIntegrationApp() {
	const query = new URLSearchParams(window.location.search);
	return (
		<CustomThemeProvider>
			<AuthProvider>
				<AlertProvider>
					<StrictMode>
						<MemoryRouter initialEntries={[query.get("start") ?? "/"]}>
							<App />
						</MemoryRouter>
						<AuthStateProbe />
						<FixtureControls />
					</StrictMode>
				</AlertProvider>
			</AuthProvider>
		</CustomThemeProvider>
	);
}

const rootElement = document.getElementById("root");
if (!rootElement) {
	throw new Error("Auth integration fixture is missing its root element");
}

const query = new URLSearchParams(window.location.search);

if (
	!import.meta.env.DEV ||
	window.location.origin !== AUTH_INTEGRATION_FIXTURE_ORIGIN
) {
	rootElement.textContent =
		"Auth fixture disabled. Use Vite development mode at " +
		AUTH_INTEGRATION_FIXTURE_ORIGIN +
		".";
} else {
	const existingToken = localStorage.getItem("Token");
	const requestedToken = query.get("token");
	if (
		!canRunAuthIntegrationFixture(
			import.meta.env.DEV,
			window.location.origin,
			existingToken,
			requestedToken,
		)
	) {
		rootElement.textContent =
			"Auth fixture stopped: an unrecognized Token value was supplied or stored. " +
			"No storage changes were made.";
	} else if (query.get("secondary") === "1") {
		createRoot(rootElement).render(<SecondaryStorageContext />);
	} else {
		if (requestedToken) {
			localStorage.setItem("Token", requestedToken);
		} else {
			localStorage.removeItem("Token");
		}
		fixtureState.failNextIdentity = Number(query.get("failIdentity") ?? 0);
		fixtureState.delayedIdentityTokens = new Set(
			(query.get("delayIdentity") ?? "")
				.split(",")
				.map((token) => token.trim())
				.filter(Boolean),
		);
		AxiosInstance.defaults.baseURL = "/__synthetic_api__/";
		AxiosInstance.defaults.adapter = syntheticAdapter;

		createRoot(rootElement).render(<AuthSessionIntegrationApp />);
}
}
