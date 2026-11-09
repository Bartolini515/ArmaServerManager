export type AuthSessionBootstrapResult<T> =
	| { kind: "authenticated"; identity: T }
	| { kind: "invalid" }
	| { kind: "retryable"; error: unknown }
	| { kind: "stale" };

export const AUTH_INTEGRATION_FIXTURE_ORIGIN = "http://127.0.0.1:5187";
const AUTH_INTEGRATION_FIXTURE_TOKENS = new Set([
	"token-admin",
	"token-user",
	"token-a",
	"token-guest",
]);

export function isAuthIntegrationFixtureToken(token: string | null): boolean {
	return token === null || AUTH_INTEGRATION_FIXTURE_TOKENS.has(token);
}

export function canRunAuthIntegrationFixture(
	isDev: boolean,
	origin: string,
	existingToken: string | null,
	requestedToken: string | null,
): boolean {
	return (
		isDev &&
		origin === AUTH_INTEGRATION_FIXTURE_ORIGIN &&
		isAuthIntegrationFixtureToken(existingToken) &&
		isAuthIntegrationFixtureToken(requestedToken)
	);
}

export function getKnoxToken(authorization: unknown): string | null {
	if (typeof authorization !== "string" || !authorization.startsWith("Token ")) {
		return null;
	}
	const token = authorization.slice("Token ".length);
	return token || null;
}

export function isPublicLoginRequest(method: unknown, url: unknown): boolean {
	if (
		typeof method !== "string" ||
		method.toLowerCase() !== "post" ||
		typeof url !== "string"
	) {
		return false;
	}
	const path = url.split(/[?#]/, 1)[0].replace(/\/+$/, "");
	return path === "login" || path.endsWith("/login");
}

export function shouldInvalidateAuthSession(
	status: number | undefined,
	method: unknown,
	url: unknown,
	failedRequestToken: string | null,
	currentToken: string | null,
): boolean {
	return (
		status === 401 &&
		!isPublicLoginRequest(method, url) &&
		failedRequestToken !== null &&
		failedRequestToken === currentToken
	);
}

export class AuthSessionBootstrap<T> {
	private requestGeneration = 0;
	private pendingRequest:
		| {
			token: string;
			generation: number;
			promise: Promise<AuthSessionBootstrapResult<T>>;
		}
		| undefined;

	load(
		token: string,
		currentToken: () => string | null,
		loadIdentity: () => Promise<T>,
	): Promise<AuthSessionBootstrapResult<T>> {
		if (currentToken() !== token) {
			return Promise.resolve({ kind: "stale" });
		}

		if (this.pendingRequest?.token === token) {
			return this.pendingRequest.promise;
		}

		const generation = ++this.requestGeneration;
		const isCurrentRequest = () =>
			this.requestGeneration === generation &&
			currentToken() === token;

		const promise = (async (): Promise<AuthSessionBootstrapResult<T>> => {
			try {
				const identity = await loadIdentity();
				return isCurrentRequest()
					? { kind: "authenticated", identity }
					: { kind: "stale" };
			} catch (error: unknown) {
				if (!isCurrentRequest()) {
					return { kind: "stale" };
				}
				return getResponseStatus(error) === 401
					? { kind: "invalid" }
					: { kind: "retryable", error };
			} finally {
				if (this.pendingRequest?.generation === generation) {
					this.pendingRequest = undefined;
				}
			}
		})();

		this.pendingRequest = { token, generation, promise };
		return promise;
	}
}

function getResponseStatus(error: unknown): number | undefined {
	if (typeof error !== "object" || error === null) {
		return undefined;
	}
	const response = (error as { response?: unknown }).response;
	if (typeof response !== "object" || response === null) {
		return undefined;
	}
	const status = (response as { status?: unknown }).status;
	return typeof status === "number" ? status : undefined;
}
