import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../../AxiosInstance";
import { Button, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import MyTextField from "../../../UI/forms/MyTextField";
import { useForm } from "react-hook-form";
import MyButton from "../../../UI/forms/MyButton";
import { useAlert } from "../../../contexts/AlertContext";

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
}

interface FormData {
	url?: string;
}

export default function ConfigModeratorPanel(props: Props) {
	const { handleSubmit, control, setError, clearErrors } = useForm<FormData>({
		defaultValues: {
			url: "",
		},
	});

	const handleClose = () => {
		props.setOpen(false);
	};

	const { setAlert } = useAlert();

	const submission = (data: FormData) => {
		if (props.option.name === "schedule") {
			setAlert("Trwa tworzenie planu lekcji, proszę czekać", "info");
		}
		AxiosInstance.post(
			`moderator_panel/${props.option.axiosUrl}`,
			props.option.payload(data)
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
					const serverErrors = error.response.data;
					Object.keys(serverErrors).forEach((field) => {
						setError(field as keyof FormData, {
							type: "server",
							message: serverErrors[field][0],
						});
					});
				} else {
					console.log(error);
					setAlert(error.message, "error");
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
								<Box sx={{ fontWeight: "bold", alignContent: "center" }}>
									{props.option.forms.first_field.title}:{" "}
								</Box>
								<Box sx={{ marginLeft: "10px" }}>
									<MyTextField
										label={props.option.forms.first_field.label}
										name={props.option.forms.first_field.name}
										control={control}
										helperText={props.option.forms.first_field.helperText}
									/>
								</Box>
							</Box>
							<MyButton
								label="Stwórz"
								type="submit"
								onClick={() => clearErrors()}
								style={{ width: "100%" }}
							/>
						</form>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
