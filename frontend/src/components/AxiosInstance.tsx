import axios from "axios";
import type { AxiosRequestConfig } from "axios";
import {
	getKnoxToken,
	isPublicLoginRequest,
	shouldInvalidateAuthSession,
} from "../contexts/authSession";

const myBaseUrl = import.meta.env.VITE_BASE_URL || "http://127.0.0.1:8000/api/";
const isDebug = import.meta.env.VITE_DEBUG !== "false";

const config: AxiosRequestConfig = {
	baseURL: myBaseUrl,
	timeout: 10000,
	headers: {
		"Content-Type": "application/json",
		Accept: "application/json",
	},
};

if (!isDebug) {
	Object.assign(config, {
		withCredentials: true,
		xsrfCookieName: "csrftoken",
		xsrfHeaderName: "X-CSRFToken",
	});
}

const AxiosInstance = axios.create(config);

AxiosInstance.interceptors.request.use((config) => {
	if (isPublicLoginRequest(config.method, config.url)) {
		config.headers.delete("Authorization");
		return config;
	}

	const token = localStorage.getItem("Token");
	if (config.headers["Content-Type"] === "multipart/form-data") {
		config.timeout = 0;
	}
	if (token) {
		config.headers.Authorization = `Token ${token}`;
	} else {
		config.headers.Authorization = "";
	}
	return config;
});

AxiosInstance.interceptors.response.use(
	(response) => {
		return response;
	},
	(error: unknown) => {
		if (axios.isAxiosError(error)) {
			const publicLogin = isPublicLoginRequest(
				error.config?.method,
				error.config?.url,
			);
			const authorization =
				error.config?.headers?.get("Authorization") ??
				error.config?.headers?.Authorization;
			const failedRequestToken = getKnoxToken(authorization);
			if (
				!publicLogin &&
				shouldInvalidateAuthSession(
					error.response?.status,
					error.config?.method,
					error.config?.url,
					failedRequestToken,
					localStorage.getItem("Token"),
				)
			) {
				localStorage.removeItem("Token");
				window.dispatchEvent(new Event("auth:session-invalidated"));
				if (window.location.pathname !== "/") {
					window.location.href = "/";
				}
			}
		}
		return Promise.reject(error);
	}
);

export default AxiosInstance;
