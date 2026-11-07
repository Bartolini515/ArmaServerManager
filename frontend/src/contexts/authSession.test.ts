import {
	AuthSessionBootstrap,
	AUTH_INTEGRATION_FIXTURE_ORIGIN,
	canRunAuthIntegrationFixture,
	getKnoxToken,
	isAuthIntegrationFixtureToken,
	isPublicLoginRequest,
	shouldInvalidateAuthSession,
} from "./authSession.ts";

function assertEqual<T>(actual: T, expected: T, message: string): void {
	if (actual !== expected) {
		throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
	}
}

function deferred<T>() {
	let resolve!: (value: T | PromiseLike<T>) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

function httpError(status: number): unknown {
	return { response: { status } };
}

async function run(): Promise<void> {
	assertEqual(
		shouldInvalidateAuthSession(401, "get", "instances/", "token-a", "token-a"),
		true,
		"a current session's 401 should invalidate it",
	);
	assertEqual(
		shouldInvalidateAuthSession(401, "post", "login/", "token-valid", "token-valid"),
		false,
		"a failed public login must not invalidate a current token",
	);
	assertEqual(
		shouldInvalidateAuthSession(401, "post", "login/", null, "token-b"),
		false,
		"a public request's 401 should not invalidate a newer session",
	);
	assertEqual(
		shouldInvalidateAuthSession(401, "get", "account/get_user/", "token-a", "token-b"),
		false,
		"a stale request's 401 should not invalidate the current session",
	);
	assertEqual(
		shouldInvalidateAuthSession(503, "get", "instances/", "token-a", "token-a"),
		false,
		"a transient response should not invalidate a session",
	);
	assertEqual(
		isPublicLoginRequest("POST", "http://example.test/api/login/?next=dashboard"),
		true,
		"public login request detection should normalize method and URL",
	);
	assertEqual(
		canRunAuthIntegrationFixture(true, AUTH_INTEGRATION_FIXTURE_ORIGIN, null, null),
		true,
		"an empty dedicated development origin should allow the fixture",
	);
	assertEqual(
		canRunAuthIntegrationFixture(true, "http://localhost:5187", null, null),
		false,
		"a different loopback origin should not allow the fixture",
	);
	assertEqual(
		canRunAuthIntegrationFixture(false, AUTH_INTEGRATION_FIXTURE_ORIGIN, null, null),
		false,
		"a production build should not allow the fixture",
	);
	assertEqual(
		canRunAuthIntegrationFixture(
			true,
			AUTH_INTEGRATION_FIXTURE_ORIGIN,
			"do-not-touch",
			"token-admin",
		),
		false,
		"an unknown existing token must block the fixture without being changed",
	);
	assertEqual(
		isAuthIntegrationFixtureToken("do-not-touch"),
		false,
		"unknown synthetic test values must not be treated as fixture tokens",
	);
	assertEqual(getKnoxToken("Token token-a"), "token-a", "Knox header token should parse");
	assertEqual(getKnoxToken(""), null, "empty authorization should not identify a session");

	const bootstrap = new AuthSessionBootstrap<{ userId: number }>();
	let currentToken: string | null = "token-a";
	let requests = 0;
	const delayedIdentity = deferred<{ userId: number }>();
	const first = bootstrap.load("token-a", () => currentToken, () => {
		requests += 1;
		return delayedIdentity.promise;
	});
	const duplicate = bootstrap.load("token-a", () => currentToken, () => {
		requests += 1;
		return Promise.resolve({ userId: 99 });
	});
	assertEqual(first, duplicate, "same-token initialization should share one request");
	assertEqual(requests, 1, "identity lookup should not be duplicated");
	delayedIdentity.resolve({ userId: 7 });
	const loaded = await first;
	assertEqual(loaded.kind, "authenticated", "delayed identity should authenticate");
	if (loaded.kind === "authenticated") {
		assertEqual(loaded.identity.userId, 7, "the delayed identity should be returned");
	}

	const staleBootstrap = new AuthSessionBootstrap<number>();
	const staleRequest = deferred<number>();
	currentToken = "token-a";
	const oldResult = staleBootstrap.load("token-a", () => currentToken, () => staleRequest.promise);
	currentToken = "token-b";
	await staleBootstrap.load("token-b", () => currentToken, () => Promise.resolve(2));
	staleRequest.resolve(1);
	const stale = await oldResult;
	assertEqual(stale.kind, "stale", "completion for a changed token must be ignored");

	const failureBootstrap = new AuthSessionBootstrap<string>();
	const invalid = await failureBootstrap.load("token-a", () => "token-a", () => Promise.reject(httpError(401)));
	assertEqual(invalid.kind, "invalid", "401 should invalidate the session");
	const unavailable = await failureBootstrap.load("token-a", () => "token-a", () => Promise.reject(httpError(503)));
	assertEqual(unavailable.kind, "retryable", "5xx should preserve the session and allow retry");
	const timeout = await failureBootstrap.load("token-a", () => "token-a", () => Promise.reject(new Error("timeout")));
	assertEqual(timeout.kind, "retryable", "network errors should preserve the session and allow retry");

	let attempts = 0;
	const retryBootstrap = new AuthSessionBootstrap<string>();
	const firstAttempt = await retryBootstrap.load("token-a", () => "token-a", () => {
		attempts += 1;
		return Promise.reject(httpError(503));
	});
	assertEqual(firstAttempt.kind, "retryable", "the first transient failure should be retryable");
	const secondAttempt = await retryBootstrap.load("token-a", () => "token-a", () => {
		attempts += 1;
		return Promise.resolve("restored");
	});
	assertEqual(attempts, 2, "retry should start a new request after the previous one completes");
	if (secondAttempt.kind !== "authenticated") {
		throw new Error("a successful retry should authenticate");
	}
	assertEqual(secondAttempt.identity, "restored", "the successful retry should return the identity");
	console.log("Auth session regressions passed (deduplication, delayed identity, stale token, 401, transient failures, retry).");
}

void run();
