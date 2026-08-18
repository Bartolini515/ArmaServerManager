import { useEffect, useState } from "react";
import { Box, Typography } from "@mui/material";
import MyTextField from "../../UI/forms/MyTextField";
import MyPassField from "../../UI/forms/MyPassField";
import MyButton from "../../UI/forms/MyButton";
// import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import AxiosInstance from "../AxiosInstance";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { useAlert } from "../../contexts/AlertContext";
import { useCustomTheme } from "../../contexts/ThemeContext";
import {
	getApiErrorData,
	getApiErrorMessage,
	getApiErrorStatus,
	getFieldErrorMessage,
} from "../../util/api-error";

interface FormData {
	username?: string;
	password?: string;
}

export default function Login() {
	const navigate = useNavigate();
	const { handleSubmit, control, setError } = useForm<FormData>({
		defaultValues: {
			username: "",
			password: "",
		},
	});
	const [showMessage, setShowMessage] = useState(false);
	const { setIsAdmin, setUser } = useAuth();
	const { setAlert } = useAlert();
	const { mode } = useCustomTheme();

	const submission = (data: FormData) => {
		AxiosInstance.post(`login/`, {
			username: data.username,
			password: data.password,
		})

			.then((response) => {
				localStorage.setItem("Token", response.data.token);
				setUser(response.data.user);

				if (response.data.isAdmin) {
					setIsAdmin(response.data.isAdmin);
				}

				if (response.data.user.last_login === null) {
					navigate(`/change_password`);
				} else {
					navigate(`/dashboard`);
					setAlert(response.data.message, "success");
				}
			})
			.catch((error: unknown) => {
				setShowMessage(true);
				const serverErrors = getApiErrorData(error);
				if (getApiErrorStatus(error) === 400 && serverErrors) {
					Object.keys(serverErrors).forEach((field) => {
						setError(field as keyof FormData, {
							type: "server",
							message: getFieldErrorMessage(serverErrors, field),
						});
					});
				} else if (getApiErrorStatus(error) === 401) {
					console.log(error);
					setAlert("Nieprawidłowe dane logowania", "error");
				} else {
					console.log(error);
					setAlert(
						"Wystąpił błąd, spróbuj ponownie później: " +
							getApiErrorMessage(error, "Nieznany błąd"),
						"error",
					);
				}
			});
	};

	useEffect(() => {
		const token = localStorage.getItem("Token");
		if (token) {
			navigate("/dashboard");
		}
	}, []);

	return (
		<Box
			sx={{
				display: "flex",
				justifyContent: "center",
				alignItems: "center",
				minHeight: "100vh",
				backgroundColor: mode === "light" ? "#f5f5f5" : "#121212",
			}}
		>
			<form onSubmit={handleSubmit(submission)}>
				<Box
					sx={{
						width: 350,
						padding: 4,
						backgroundColor: mode === "light" ? "white" : "#1e1e1e",
						borderRadius: 2,
						boxShadow: "0 4px 8px rgba(0, 0, 0, 0.1)",
					}}
				>
					<Typography
						variant="h5"
						sx={{ textAlign: "center", marginBottom: 2, fontWeight: "bold" }}
					>
						Logowanie
					</Typography>

					{showMessage && (
						<Typography
							sx={{
								color: "red",
								marginBottom: 2,
								textAlign: "center",
							}}
						>
							Logowanie nie powiodło się
						</Typography>
					)}

					<Box sx={{ marginBottom: 2 }}>
						<MyTextField label={"Login"} name={"username"} control={control} />
					</Box>

					<Box sx={{ marginBottom: 2 }}>
						<MyPassField label={"Hasło"} name={"password"} control={control} />
					</Box>

					<Box sx={{ marginTop: 2 }}>
						<MyButton label={"Zaloguj"} type={"submit"} />
					</Box>
				</Box>
			</form>
		</Box>
	);
}
