import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../../AxiosInstance";
import { Button, Skeleton, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import MyTextField from "../../../UI/forms/MyTextField";
import { useForm } from "react-hook-form";
import MyButton from "../../../UI/forms/MyButton";
import { useAlert } from "../../../contexts/AlertContext";
import { useEffect, useState } from "react";

const style = {
	position: "absolute",
	top: "50%",
	left: "50%",
	transform: "translate(-50%, -50%)",
	width: { xs: "80%", sm: "600px" },
	height: "90%",
	maxHeight: "max-content",
	overflow: "auto",
	bgcolor: "background.paper",
	border: "2px solid #000",
	borderRadius: 4,
	boxShadow: 24,
	p: 4,
};

interface Props {
	option: {
		name: string;
		axiosUrl: string;
		labelModal: string;
		buttonSend: string;
		forms: {
			first_field: {
				title: string | null;
				label: string;
				name: string;
				helperText?: string;
			};
		};
		payload: (data: any) => any;
	};
	open: boolean;
	setOpen: any;
}

interface FormData {
	steamcmd?: string;
	arma3?: string;
	mods_directory?: string;
	logs_directory?: string;
	download_directory?: string;
	username?: string;
	password?: string;
	shared_secret?: string;
}

export default function ConfigModeratorPanel(props: Props) {
	const { handleSubmit, control, setError, clearErrors, reset } =
		useForm<FormData>({
			defaultValues: {
				steamcmd: "",
				arma3: "",
				mods_directory: "",
				logs_directory: "",
				download_directory: "",
				username: "",
				password: "",
				shared_secret: "",
			},
		});
	const [loading, setLoading] = useState(true);

	const handleClose = () => {
		props.setOpen(false);
	};

	const { setAlert } = useAlert();

	const GetConfig = () => {
		AxiosInstance.get(`moderator_panel/config/`)
			.then((response) => {
				const paths = response.data.paths;
				const steam_auth = response.data.steam_auth;
				reset({
					steamcmd: paths.steamcmd,
					arma3: paths.arma3,
					mods_directory: paths.mods_directory,
					logs_directory: paths.logs_directory,
					download_directory: paths.download_directory,
					username: steam_auth.username,
					password: steam_auth.password,
					shared_secret: steam_auth.shared_secret,
				});
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.message, "error");
			});
	};

	useEffect(() => {
		GetConfig();
	}, []);

	const submission = (data: FormData) => {
		const payload = props.option.payload(data);
		if (props.option.name === "config") {
			if (data.password === "********") {
				delete payload.password;
			}
			if (data.shared_secret === "********") {
				delete payload.shared_secret;
			}
			AxiosInstance.put(`moderator_panel/${props.option.axiosUrl}`, payload)
				.then((response) => {
					handleClose();
					setAlert(response.data.message, "success");
				})
				.catch((error: any) => {
					if (
						error.response &&
						error.response.data &&
						error.response.status === 400
					) {
						const serverErrors = error.response.data;
						const path_errors = serverErrors.paths;
						if (path_errors) {
							Object.keys(path_errors).forEach((field) => {
								setError(field as keyof FormData, {
									type: "server",
									message: path_errors[field][0],
								});
							});
						}

						const steam_auth_errors = serverErrors.steam_auth;
						if (steam_auth_errors) {
							Object.keys(steam_auth_errors).forEach((field) => {
								setError(field as keyof FormData, {
									type: "server",
									message: steam_auth_errors[field][0],
								});
							});
						}
					} else {
						console.log(error);
						setAlert(
							error.response.data.message
								? error.response.data.message
								: error.message,
							"error"
						);
					}
				});
		}

		// AxiosInstance.post(
		// 	`moderator_panel/${props.option.axiosUrl}`,
		// 	payload
		// )
		// 	.then((response) => {
		// 		handleClose();
		// 		setAlert(response.data.message, "success");
		// 	})
		// 	.catch((error: any) => {
		// 		if (
		// 			error.response &&
		// 			error.response.data &&
		// 			error.response.status === 400
		// 		) {
		// 			const serverErrors = error.response.data;
		// 			Object.keys(serverErrors).forEach((field) => {
		// 				setError(field as keyof FormData, {
		// 					type: "server",
		// 					message: serverErrors[field][0],
		// 				});
		// 			});
		// 		} else {
		// 			console.log(error);
		// 			setAlert(error.response.data.message ? error.response.data.message : error.message, "error");
		// 		}
		// 	});
	};

	return (
		<>
			<Modal
				aria-labelledby="transition-modal-title"
				aria-describedby="transition-modal-description"
				open={props.open}
				onClose={handleClose}
				closeAfterTransition
				slots={{ backdrop: Backdrop }}
				slotProps={{
					backdrop: {
						timeout: 500,
					},
				}}
			>
				<Fade in={props.open}>
					{loading ? (
						<Skeleton variant="rectangular" height={400} />
					) : (
						<Box sx={style}>
							<Box
								sx={{
									display: "flex",
									justifyContent: "center",
									alignItems: "center",
									marginBottom: 2,
								}}
							>
								<Typography
									id="transition-modal-title"
									variant="h5"
									component="h2"
								>
									{props.option.labelModal}
								</Typography>
							</Box>
							<Button
								sx={{
									position: "absolute",
									right: "2px",
									top: "4px",
									zIndex: 2,
									padding: "0px",
									minWidth: "0px",
								}}
								onClick={handleClose}
							>
								<CloseIcon sx={{ color: "red" }} fontSize="medium" />
							</Button>
							<form onSubmit={handleSubmit(submission)}>
								<Box
									sx={{
										boxShadow: 3,
										padding: "20px",
										display: "flex",
										flexDirection: "row",
										marginBottom: "20px",
									}}
								>
									{props.option.forms.first_field.title && (
										<Box sx={{ fontWeight: "bold", alignContent: "center" }}>
											{props.option.forms.first_field.title}:{" "}
										</Box>
									)}

									{props.option.forms.first_field.title ? (
										<Box sx={{ marginLeft: "10px" }}>
											<MyTextField
												label={props.option.forms.first_field.label}
												name={props.option.forms.first_field.name}
												control={control}
												helperText={props.option.forms.first_field.helperText}
											/>
										</Box>
									) : (
										<MyTextField
											label={props.option.forms.first_field.label}
											name={props.option.forms.first_field.name}
											control={control}
											helperText={props.option.forms.first_field.helperText}
										/>
									)}
								</Box>
								{props.option.name === "config" && (
									<>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Ścieżka do folderu Arma 3"
												name="arma3"
												control={control}
												helperText="Podaj ścieżkę do folderu Arma 3, np. '/path/to/arma3'"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Ścieżka do folderu Mods"
												name="mods_directory"
												control={control}
												helperText="Podaj ścieżkę do folderu Mods, np. '/path/to/mods'"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Ścieżka do folderu Logs"
												name="logs_directory"
												control={control}
												helperText="Podaj ścieżkę do folderu Logs, np. '/path/to/logs'"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Ścieżka do folderu Download"
												name="download_directory"
												control={control}
												helperText="Podaj ścieżkę do folderu Download, np. '/path/to/download'"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Nazwa konta Steam"
												name="username"
												control={control}
												helperText="Podaj nazwę konta Steam"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Hasło konta Steam"
												name="password"
												control={control}
												helperText="Podaj hasło do konta Steam. '*******' oznacza że hasło jest już ustawione"
											/>
										</Box>
										<Box
											sx={{
												boxShadow: 3,
												padding: "20px",
												display: "flex",
												flexDirection: "row",
												marginBottom: "20px",
											}}
										>
											<MyTextField
												label="Steam Shared Secret"
												name="shared_secret"
												control={control}
												helperText="Podaj shared secret do konta Steam. '*******' oznacza że shared secret jest już ustawiony"
											/>
										</Box>
									</>
								)}
								<MyButton
									label={props.option.buttonSend}
									type="submit"
									onClick={() => clearErrors()}
									style={{ width: "100%" }}
								/>
							</form>
						</Box>
					)}
				</Fade>
			</Modal>
		</>
	);
}
