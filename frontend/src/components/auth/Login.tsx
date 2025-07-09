import { useEffect, useState } from "react";
import { Box, Typography } from "@mui/material";
import MyTextField from "../forms/MyTextField";
import MyPassField from "../forms/MyPassField";
import MyButton from "../forms/MyButton";
// import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import AxiosInstance from "../AxiosInstance";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { useAlert } from "../../contexts/AlertContext";
// import MyMessage from "./Message";

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
			.catch((error: any) => {
				setShowMessage(true);
				if (
					error.response &&
					error.response.data &&
					error.response.status === 400
				) {
					const serverErrors = error.response.data;
					Object.keys(serverErrors).forEach((field) => {
						setError(field as keyof FormData, {
							type: "server",
							message: serverErrors[field][0],
						});
					});
				} else {
					console.log(error);
					setAlert("Nieprawidłowe dane logowania", "error");
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
				backgroundColor: "#f5f5f5",
			}}
		>
			<form onSubmit={handleSubmit(submission)}>
				<Box
					sx={{
						width: 300,
						padding: 4,
						backgroundColor: "white",
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
							Logowanie nie powiodło się, proszę spróbować ponownie.
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
