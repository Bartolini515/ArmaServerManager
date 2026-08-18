import { useState } from "react";
import { Box, Typography } from "@mui/material";
import MyPassField from "../../UI/forms/MyPassField";
import MyButton from "../../UI/forms/MyButton";
import { useForm } from "react-hook-form";
import AxiosInstance from "../AxiosInstance";
import { useNavigate } from "react-router-dom";
import { useAlert } from "../../contexts/AlertContext";
import { useCustomTheme } from "../../contexts/ThemeContext";
import {
	getApiErrorData,
	getApiErrorMessage,
	getApiErrorStatus,
	getFieldErrorMessage,
} from "../../util/api-error";

interface FormData {
	password?: string;
	confirmPassword?: string;
}

export default function ChangePasswordLogin() {
	const navigate = useNavigate();
	const { handleSubmit, control, setError } = useForm<FormData>({
		defaultValues: {
			password: "",
			confirmPassword: "",
		},
	});
	const [showMessage, setShowMessage] = useState(false);
	const { setAlert } = useAlert();
	const { mode } = useCustomTheme();

	const submission = (data: FormData) => {
		if (data.password !== data.confirmPassword) {
			setError("confirmPassword", {
				type: "manual",
				message: "Hasła się nie zgadzają",
			});
			return;
		}

		AxiosInstance.post(`account/change_password/`, {
			password: data.password,
		})
			.then((response) => {
				navigate(`/dashboard`);
				setAlert(response.data.message, "success");
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
				} else {
					console.log(error);
					setAlert(getApiErrorMessage(error, "Wystąpił błąd."), "error");
				}
			});
	};

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
						Zmień hasło
					</Typography>

					{showMessage && (
						<Typography
							sx={{
								color: "red",
								marginBottom: 2,
								textAlign: "center",
							}}
						>
							Zmiana hasła nie powiodła się, proszę spróbować ponownie.
						</Typography>
					)}

					<Box sx={{ marginBottom: 2 }}>
						<MyPassField
							label={"Nowe hasło"}
							name={"password"}
							control={control}
						/>
					</Box>

					<Box sx={{ marginBottom: 2 }}>
						<MyPassField
							label={"Potwierdź hasło"}
							name={"confirmPassword"}
							control={control}
						/>
					</Box>

					<Box sx={{ marginTop: 2 }}>
						<MyButton label={"Zmień hasło"} type={"submit"} />
					</Box>
				</Box>
			</form>
		</Box>
	);
}
