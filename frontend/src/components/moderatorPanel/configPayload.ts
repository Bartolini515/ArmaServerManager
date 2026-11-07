import type {
	ConfigModeratorFormData,
	ConfigModeratorPayload,
} from "./types.ts";

export const STEAM_SECRET_MASK = "********";

export function buildConfigPayload(
	data: ConfigModeratorFormData,
): ConfigModeratorPayload {
	return {
		paths: {
			steamcmd: data.steamcmd,
			arma3: data.arma3,
			mods_directory: data.mods_directory,
			logs_directory: data.logs_directory,
			download_directory: data.download_directory,
		},
		steam_auth: {
			username: data.username,
			...(data.password !== STEAM_SECRET_MASK
				? { password: data.password }
				: {}),
			...(data.shared_secret !== STEAM_SECRET_MASK
				? { shared_secret: data.shared_secret }
				: {}),
		},
	};
}
