import {
	buildConfigPayload,
	STEAM_SECRET_MASK,
} from "./configPayload.ts";
import type { ConfigModeratorFormData } from "./types.ts";

function assertEqual<T>(actual: T, expected: T, message: string): void {
	if (actual !== expected) {
		throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
	}
}

function assertHasOwn(
	value: object,
	property: PropertyKey,
	message: string,
): void {
	if (!Object.prototype.hasOwnProperty.call(value, property)) {
		throw new Error(message);
	}
}

function assertDoesNotHaveOwn(
	value: object,
	property: PropertyKey,
	message: string,
): void {
	if (Object.prototype.hasOwnProperty.call(value, property)) {
		throw new Error(message);
	}
}

function createForm(
	overrides: Partial<ConfigModeratorFormData> = {},
): ConfigModeratorFormData {
	return {
		steamcmd: "/synthetic/steamcmd",
		arma3: "/synthetic/arma3",
		mods_directory: "/synthetic/mods",
		logs_directory: "/synthetic/logs",
		download_directory: "/synthetic/downloads",
		username: "synthetic-user",
		password: STEAM_SECRET_MASK,
		shared_secret: STEAM_SECRET_MASK,
		...overrides,
	};
}

function assertNotMutated(data: ConfigModeratorFormData, message: string): void {
	const before = JSON.stringify(data);
	buildConfigPayload(data);
	assertEqual(JSON.stringify(data), before, message);
}

function run(): void {
	const maskedSecretsWithPathEdit = createForm({
		mods_directory: "/synthetic/changed-mods",
	});
	const maskedPayload = buildConfigPayload(maskedSecretsWithPathEdit);
	assertEqual(
		maskedPayload.paths.mods_directory,
		"/synthetic/changed-mods",
		"an unrelated path edit should be included",
	);
	assertEqual(
		maskedPayload.steam_auth.username,
		"synthetic-user",
		"the Steam username should be preserved",
	);
	assertDoesNotHaveOwn(
		maskedPayload.steam_auth,
		"password",
		"a masked password should be omitted from nested steam_auth",
	);
	assertDoesNotHaveOwn(
		maskedPayload.steam_auth,
		"shared_secret",
		"a masked shared secret should be omitted from nested steam_auth",
	);
	assertEqual(
		Object.values(maskedPayload.steam_auth).includes(STEAM_SECRET_MASK),
		false,
		"the literal mask must not appear in nested steam_auth",
	);
	assertNotMutated(
		maskedSecretsWithPathEdit,
		"building a payload must not mutate masked form values",
	);

	const passwordOnlyConfigured = createForm({ shared_secret: "" });
	const passwordOnlyPayload = buildConfigPayload(passwordOnlyConfigured);
	assertDoesNotHaveOwn(
		passwordOnlyPayload.steam_auth,
		"password",
		"an existing password mask should be omitted",
	);
	assertHasOwn(
		passwordOnlyPayload.steam_auth,
		"shared_secret",
		"an empty shared secret should remain present so it can be cleared",
	);
	assertEqual(
		passwordOnlyPayload.steam_auth.shared_secret,
		"",
		"an empty shared secret should be preserved exactly",
	);

	const replacedPassword = createForm({ password: "synthetic-new-password" });
	const replacedPasswordPayload = buildConfigPayload(replacedPassword);
	assertEqual(
		replacedPasswordPayload.steam_auth.password,
		"synthetic-new-password",
		"a replaced password should be sent",
	);
	assertDoesNotHaveOwn(
		replacedPasswordPayload.steam_auth,
		"shared_secret",
		"the other masked secret should still be omitted",
	);
	assertNotMutated(
		replacedPassword,
		"building a payload must not mutate replaced form values",
	);

	const explicitlyClearedSecrets = createForm({
		password: "",
		shared_secret: "",
	});
	const clearedPayload = buildConfigPayload(explicitlyClearedSecrets);
	assertHasOwn(
		clearedPayload.steam_auth,
		"password",
		"an explicitly cleared password should remain present",
	);
	assertHasOwn(
		clearedPayload.steam_auth,
		"shared_secret",
		"an explicitly cleared shared secret should remain present",
	);
	assertEqual(
		clearedPayload.steam_auth.password,
		"",
		"an explicitly cleared password should remain empty",
	);
	assertEqual(
		clearedPayload.steam_auth.shared_secret,
		"",
		"an explicitly cleared shared secret should remain empty",
	);
	assertNotMutated(
		explicitlyClearedSecrets,
		"building a payload must not mutate explicitly cleared values",
	);

	console.log("Configuration payload regressions passed.");
}

run();
