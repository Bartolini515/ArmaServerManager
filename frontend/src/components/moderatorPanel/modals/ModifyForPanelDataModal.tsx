import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../../AxiosInstance";
import { Button, Typography, Skeleton } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import MyTextField from "../../../UI/forms/MyTextField";
import { useForm } from "react-hook-form";
import MyButton from "../../../UI/forms/MyButton";
import { useAlert } from "../../../contexts/AlertContext";
import { useEffect, useState } from "react";
import MyPassField from "../../../UI/forms/MyPassField";

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
		label: string;
		labelSingle: string;
		headers: string[];
		buttonAdd: string;
		forms: {
			first_field: {
				title: string;
				label: string;
				name: string;
				helperText?: string;
			};
		};
		payload: (data: any) => any;
	};
	open: boolean;
	setOpen: any;
	setRefresh: any;
	id: number;
	setClickedId: any;
}

interface ResponseData {
	id: number;
	username?: string;
}

interface FormData {
	username?: string;
	password?: string;
}

export default function ModifyDataModerator(props: Props) {
	const [responseData, setResponseData] = useState<ResponseData>({
		id: 0,
		username: "",
	});
	const handleClose = () => {
		props.setOpen(false);
		props.setRefresh(true);
	};

	const { setAlert } = useAlert();
	const [loading, setLoading] = useState(true);

	const GetData = () => {
		AxiosInstance.get(`moderator_panel/${props.id}/${props.option.name}/`)
			.then((response) => {
				setResponseData(response.data);
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.response.data.message || error.message, "error");
			});
	};
	useEffect(() => {
		GetData();
	}, []);

	const { handleSubmit, control, setError, clearErrors, reset } =
		useForm<FormData>({
			defaultValues: {
				username: responseData.username,
			},
		});

	useEffect(() => {
		reset({
			username: responseData.username,
		});
	}, [responseData, reset]);

	const submission = (data: FormData) => {
		const payload = props.option.payload(data);

		if (data.password) {
			payload.password = data.password;
		} else {
			delete payload.password;
		}

		AxiosInstance.put(
			`moderator_panel/${props.id}/${props.option.name}/update/`,
			payload
		)
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
					console.log(error);
					const serverErrors = error.response.data;
					Object.keys(serverErrors).forEach((field) => {
						setError(field as keyof FormData, {
							type: "server",
							message: serverErrors[field][0],
						});
					});
				} else {
					console.log(error);
					setAlert(error.response.data.message || error.message, "error");
				}
			});
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
					<Box sx={style}>
						{loading ? (
							<Skeleton variant="rectangular" height={400} />
						) : (
							<>
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
										Zmień {props.option.labelSingle}
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
										<Box sx={{ fontWeight: "bold", alignContent: "center" }}>
											{props.option.forms.first_field.title}:
										</Box>
										<Box sx={{ marginLeft: "10px" }}>
											<MyTextField
												label={props.option.forms.first_field.label}
												name={props.option.forms.first_field.name}
												control={control}
											/>
										</Box>
									</Box>

									{props.option.name === "user" && (
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
												<Box
													sx={{ fontWeight: "bold", alignContent: "center" }}
												>
													Hasło:{" "}
												</Box>
												<Box sx={{ marginLeft: "10px" }}>
													<MyPassField
														label="Hasło"
														name="password"
														control={control}
													/>
												</Box>
											</Box>
										</>
									)}
									<MyButton
										label="Zatwierdź"
										type="submit"
										onClick={() => clearErrors()}
										style={{ width: "100%" }}
									/>
								</form>
							</>
						)}
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
