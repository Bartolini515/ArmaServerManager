import axios from "axios";

const myBaseUrl = import.meta.env.VITE_BASE_URL || "http://127.0.0.1:8000/api/";
const AxiosInstance = axios.create({
	baseURL: myBaseUrl,
	timeout: 10000,
	headers: {
		"Content-Type": "application/json",
		Accept: "application/json",
	},
	withCredentials: true,
	xsrfCookieName: "csrftoken",
	xsrfHeaderName: "X-CSRFToken",
});

AxiosInstance.interceptors.request.use((config) => {
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
	(error) => {
		if (error.response && error.response.status === 401) {
			localStorage.removeItem("Token");
			if (window.location.pathname !== "/") {
				window.location.href = "/";
			}
		}
		return Promise.reject(error);
	}
);

export default AxiosInstance;
