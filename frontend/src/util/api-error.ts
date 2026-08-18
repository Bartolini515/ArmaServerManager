import axios from "axios";

export type ApiErrorData = Record<string, unknown>;

export function isRecord(value: unknown): value is ApiErrorData {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function getApiErrorData(error: unknown): ApiErrorData | undefined {
	if (!axios.isAxiosError(error)) {
		return undefined;
	}

	return isRecord(error.response?.data) ? error.response.data : undefined;
}

export function getApiErrorStatus(error: unknown): number | undefined {
	return axios.isAxiosError(error) ? error.response?.status : undefined;
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
	const data = getApiErrorData(error);
	if (typeof data?.message === "string" && data.message) {
		return data.message;
	}
	if (error instanceof Error && error.message) {
		return error.message;
	}
	return fallback;
}

export function getFieldErrorMessage(
	data: ApiErrorData,
	field: string,
	): string {
	const value = data[field];
	if (typeof value === "string") {
		return value;
	}
	if (Array.isArray(value)) {
		const firstMessage = value.find(
			(message): message is string => typeof message === "string",
		);
		if (firstMessage) {
			return firstMessage;
		}
	}
	return "Nieprawidłowa wartość.";
}
